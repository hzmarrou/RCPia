"""Loading a guideline corpus into PostgreSQL.

The properties that matter are about what does *not* get stored. A passage missing its
routing metadata would be invisible to every filter, which reads exactly like a corpus that
does not cover the topic — so it is refused rather than stored with a hole in it.
"""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evidence.embedding import Embedder
from rcpia.evidence.ingestion import (
    CorpusError,
    ReleaseSchema,
    ingest_release,
    names_an_out_of_scope_disease,
    validate_chunk,
    verify_search_configurations,
)
from rcpia.evidence.models import (
    EMBEDDING_DIMENSIONS,
    EvidenceChunk,
    EvidenceDocument,
)

# The real width. The column is `vector(1536)` and PostgreSQL refuses anything else, so a
# smaller fake would pass in Python and fail on write — which is the check working.
DIMENSIONS = EMBEDDING_DIMENSIONS


class FakeEmbedder(Embedder):
    """Deterministic vectors, no network. Counts calls so re-embedding is observable."""

    def __init__(self, name: str = "fake-embed") -> None:
        self._name = name
        self.embedded: list[str] = []

    @property
    def model(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [[float(len(text) % 7)] * DIMENSIONS for text in texts]


def document(document_id: str = "afu.prostate.2024-2026", **overrides) -> dict:
    record = {
        "document_id": document_id,
        "ref_id": "ref-01",
        "title": "AFU Prostate 2024-2026",
        "publisher": "Association francaise d'urologie",
        "language": "fr",
        "clinical_status": "unreviewed",
        "version": {"label": "2024-2026"},
        "source": {"source_url": "https://www.urofrance.org/x"},
        "licence": {"status": "unknown", "ai_use_allowed": True},
    }
    record.update(overrides)
    return record


def chunk(chunk_id: str = "afu.prostate.2024-2026.localise.01.0001", **overrides) -> dict:
    record = {
        "chunk_id": chunk_id,
        "document_id": "afu.prostate.2024-2026",
        "ref_id": "ref-01",
        "title": "AFU Prostate 2024-2026",
        "text": "La surveillance active est une option pour le risque faible.",
        "contextual_text": "Source: AFU. Section: Risque faible.\n\nLa surveillance active…",
        "section_path": ["Cancer localise", "Risque faible"],
        "source_location": {
            "kind": "pdf_page_range",
            "page_start": 42,
            "page_end": 42,
            "locator_method": "pdf_text_alignment",
            "locator_confidence": 0.9,
        },
        "citation": {"label": "AFU Prostate 2024-2026 — Risque faible", "source_url": "…"},
        "language": "fr",
        "cancer_types": ["prostate"],
        "disease_setting": "prostate_localized_low_risk",
        "therapy_line": "primary",
        "clinical_purpose": ["therapy_indication"],
        "agent_roles": ["urology_oncology"],
        "unit_type": "recommendation",
        "recommendation_number": 12,
        "retrieval": {"keyword_text": "surveillance active risque faible"},
        "content_sha256": "a" * 64,
        "embedding_sha256": "b" * 64,
        "not_specified_reason": "not_applicable",
        "clinical_status": "poc_unreviewed",
    }
    record.update(overrides)
    return record


# The enums the real `chunk.schema.json` declares, trimmed to what the tests exercise. The
# loader validates against the release's own schema, so a release without one is refused.
SCHEMA = {
    "required": ["chunk_id", "document_id", "text", "contextual_text", "content_sha256"],
    "properties": {
        "disease_setting": {
            "type": "string",
            "enum": [
                "not_specified",
                "prostate_localized_low_risk",
                "prostate_metastatic_hormone_sensitive",
                "bladder_mibc_non_metastatic",
                "bladder_nmibc_low_risk",
            ],
        },
        "therapy_line": {
            "type": "string",
            "enum": ["not_applicable", "primary", "neoadjuvant", "salvage"],
        },
        "language": {"type": "string", "enum": ["en", "fr"]},
        "clinical_status": {"type": "string", "enum": ["poc_unreviewed", "poc_reviewed"]},
        "not_specified_reason": {
            "type": "string",
            "enum": [
                "not_applicable",
                "spans_settings",
                "general_principle",
                "ancestry_unreliable",
                "not_determinable",
            ],
        },
        "cancer_types": {"type": "array", "items": {"enum": ["prostate", "bladder"]}},
        "clinical_purpose": {
            "type": "array",
            "items": {"enum": ["general", "therapy_indication", "modality_selection"]},
        },
        "agent_roles": {
            "type": "array",
            "items": {"enum": ["urology_oncology", "medical_oncology"]},
        },
    },
}


def release(
    tmp_path: Path,
    documents: list[dict],
    chunks: list[dict],
    *,
    schema: dict | None = SCHEMA,
) -> Path:
    directory = tmp_path / "release"
    directory.mkdir(exist_ok=True)
    if schema is not None:
        (directory / "chunk.schema.json").write_text(
            json.dumps(schema), encoding="utf-8"
        )
    for name, records in (("documents", documents), ("chunks", chunks)):
        (directory / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
            encoding="utf-8",
        )
    return directory


# --------------------------------------------------------------------------------------
# What must not be stored
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["disease_setting", "therapy_line", "cancer_types", "content_sha256", "text"],
)
def test_a_passage_missing_routing_metadata_is_refused(field: str) -> None:
    """Stored with a hole, it would be invisible to every filter — indistinguishable from a
    corpus that does not cover the topic."""
    assert validate_chunk(chunk(**{field: None})) is not None


def test_a_passage_claiming_two_exclusive_settings_is_refused() -> None:
    """13% of the previous release claimed both metastatic and non-metastatic. No correct
    filter can be written while that holds."""
    problem = validate_chunk(chunk(disease_setting="bladder_mibc_metastatic_non_metastatic"))

    assert problem is not None and "exclusive" in problem


def test_a_valid_passage_passes() -> None:
    assert validate_chunk(chunk()) is None


async def test_a_release_with_no_documents_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    with pytest.raises(CorpusError, match="no documents"):
        await ingest_release(
            session, directory=release(tmp_path, [], []), embedder=FakeEmbedder()
        )


async def test_a_passage_whose_document_is_absent_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A citation resolving to no document is a citation a reviewer cannot follow."""
    directory = release(
        tmp_path, [document()], [chunk(document_id="nccn.prostate.6.2026")]
    )

    with pytest.raises(CorpusError, match="not declared by this release"):
        await ingest_release(session, directory=directory, embedder=FakeEmbedder())

    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 0


async def test_an_excluded_document_is_not_loaded(
    session: AsyncSession, tmp_path: Path
) -> None:
    directory = release(
        tmp_path, [document(clinical_status="excluded")], []
    )

    report = await ingest_release(session, directory=directory, embedder=FakeEmbedder())

    assert report.documents == 0


# --------------------------------------------------------------------------------------
# What gets stored
# --------------------------------------------------------------------------------------


async def test_a_release_loads_with_its_provenance(
    session: AsyncSession, tmp_path: Path
) -> None:
    directory = release(tmp_path, [document()], [chunk()])
    embedder = FakeEmbedder()

    report = await ingest_release(
        session, directory=directory, embedder=embedder, release_id="corpus-v2.0"
    )

    assert (report.documents, report.chunks_inserted) == (1, 1)
    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None
    # Everything a reviewer needs to check the citation against the published PDF.
    assert stored.page_start == 42
    assert stored.recommendation_number == 12
    assert stored.citation_label.startswith("AFU Prostate")
    assert stored.locator_method == "pdf_text_alignment"
    # And the routing the filter will use.
    assert stored.disease_setting == "prostate_localized_low_risk"
    assert stored.therapy_line == "primary"
    assert stored.cancer_types == ["prostate"]
    # The model is recorded, because vectors from different models cannot be compared.
    assert stored.embedding_model == "fake-embed"
    assert len(stored.embedding) == DIMENSIONS

    parent = await session.get(EvidenceDocument, "afu.prostate.2024-2026")
    assert parent is not None and parent.release_id == "corpus-v2.0"
    assert parent.version_label == "2024-2026"


async def test_the_contextual_text_is_what_gets_embedded(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A passage embedded without its section path floats free of its own context."""
    directory = release(tmp_path, [document()], [chunk()])
    embedder = FakeEmbedder()

    await ingest_release(session, directory=directory, embedder=embedder)

    assert embedder.embedded == [chunk()["contextual_text"]]


# --------------------------------------------------------------------------------------
# Re-running
# --------------------------------------------------------------------------------------


async def test_an_unchanged_passage_is_not_embedded_again(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Embedding the corpus costs money and minutes. Paying it again for an unchanged
    release is the reason nobody would update the corpus."""
    directory = release(tmp_path, [document()], [chunk()])
    first = FakeEmbedder()
    await ingest_release(session, directory=directory, embedder=first)

    second = FakeEmbedder()
    report = await ingest_release(session, directory=directory, embedder=second)

    assert report.chunks_unchanged == 1
    assert report.embedded == 0
    assert second.embedded == []


async def test_an_edited_passage_is_re_embedded_and_replaced(
    session: AsyncSession, tmp_path: Path
) -> None:
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    edited = chunk(
        text="La surveillance active est une option pour le risque tres faible.",
        contextual_text="Source: AFU. Section: Risque faible. Risque tres faible.",
        content_sha256="b" * 64,
    )
    embedder = FakeEmbedder()
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [edited]),
        embedder=embedder,
    )

    assert (report.chunks_updated, report.chunks_inserted) == (1, 0)
    assert len(embedder.embedded) == 1
    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None and "tres faible" in stored.text
    # Still one row: a re-ingest replaces a passage rather than duplicating it.
    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 1


async def test_only_changed_passages_are_embedded_in_a_mixed_release(
    session: AsyncSession, tmp_path: Path
) -> None:
    unchanged = chunk("afu.prostate.2024-2026.localise.01.0001")
    other = chunk(
        "afu.prostate.2024-2026.localise.01.0002",
        contextual_text="Source: AFU. Section: Risque faible. Autre passage.",
        content_sha256="c" * 64,
    )
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [unchanged, other]),
        embedder=FakeEmbedder(),
    )

    edited = chunk(
        "afu.prostate.2024-2026.localise.01.0002",
        contextual_text="Source: AFU. Section: Risque faible. Texte revise.",
        content_sha256="d" * 64,
    )
    embedder = FakeEmbedder()
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [unchanged, edited]),
        embedder=embedder,
    )

    assert report.chunks_unchanged == 1
    assert report.chunks_updated == 1
    assert len(embedder.embedded) == 1


# --------------------------------------------------------------------------------------
# Diseases the corpus covers but RCPia does not
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section",
    [
        "Upper Tract Urothelial Carcinoma",
        "Upper GU Tract Tumors",
        "MANAGEMENT PER UTUC RISK GROUP",
        "Primary Carcinoma of the Urethra",
        "Table 3. American Joint Committee on Cancer (AJCC) TNM Staging System for "
        "Renal Pelvis and Ureter Cancer (8th ed., 2017)",
    ],
)
def test_a_passage_from_another_disease_is_refused(section: str) -> None:
    """The NCCN bladder guideline also documents upper tract and urethral cancers, and those
    sections arrived tagged `bladder`. They pass every bladder filter, so a bladder patient
    could be shown nephron-preserving criteria for a tumour of the renal pelvis."""
    problem = validate_chunk(chunk(section_path=["Bladder Cancer", section]))

    assert problem is not None and "does not cover" in problem


@pytest.mark.parametrize(
    "section",
    [
        "ABBREVIATIONS",
        "Adjuvant Systemic Therapy",
        "Primary Treatment",
        "Workup",
        "Urethrectomy",
        "PRINCIPLES OF SURGICAL MANAGEMENT",
    ],
)
def test_ordinary_bladder_content_is_not_refused(section: str) -> None:
    """The reason the rule reads headings and not passage text.

    38 chunks mention `UTUC` or `renal pelvis` somewhere in their body; only 16 sit in a
    section about those diseases, and the two rules overlap on only 14. `ABBREVIATIONS` defines
    the term, `Urethrectomy` is part of
    a cystectomy. Refusing these would be over-filtering, which is invisible — the system
    reports no relevant evidence, and that reads as a gap in the guideline.
    """
    assert names_an_out_of_scope_disease(["Bladder Cancer", section]) is None


# --------------------------------------------------------------------------------------
# A release replaces its own documents
# --------------------------------------------------------------------------------------


async def test_a_passage_dropped_from_a_release_is_removed(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Otherwise a passage the corpus agent withdrew stays citable and looks current."""
    keep = chunk("afu.prostate.2024-2026.localise.01.0001")
    drop = chunk("afu.prostate.2024-2026.localise.01.0002", content_sha256="b" * 64)
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [keep, drop]),
        embedder=FakeEmbedder(),
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [keep]),
        embedder=FakeEmbedder(),
    )

    assert report.chunks_removed == 1
    remaining = (await session.execute(sa.select(EvidenceChunk.chunk_id))).scalars().all()
    assert list(remaining) == ["afu.prostate.2024-2026.localise.01.0001"]


async def test_a_renamed_chunk_id_replaces_rather_than_duplicates(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The corpus agent's ids embed the section slug, and correcting section hierarchy
    changes them. Without removal a corrected release inserts a second copy of the corpus
    beside the first, and nothing errors."""
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk("old.id.0001")]),
        embedder=FakeEmbedder(),
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk("new.id.0001")]),
        embedder=FakeEmbedder(),
    )

    assert (report.chunks_inserted, report.chunks_removed) == (1, 1)
    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 1


async def test_a_release_does_not_touch_documents_it_does_not_name(
    session: AsyncSession, tmp_path: Path
) -> None:
    """We load slices. Deleting everything absent from a six-document slice would wipe the
    rest of the corpus."""
    other = document("eau.prostate.2026")
    await ingest_release(
        session,
        directory=release(
            tmp_path,
            [document(), other],
            [chunk(), chunk("eau.chunk.0001", document_id="eau.prostate.2026",
                            content_sha256="c" * 64)],
        ),
        embedder=FakeEmbedder(),
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    assert report.chunks_removed == 0
    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 2


async def test_a_document_marked_excluded_is_removed_with_its_passages(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Withdrawn guidance must not stay citable."""
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document(clinical_status="excluded")], []),
        embedder=FakeEmbedder(),
    )

    assert report.documents_removed == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 0
    assert await session.get(EvidenceDocument, "afu.prostate.2024-2026") is None


# --------------------------------------------------------------------------------------
# Metadata corrections, which change no text at all
# --------------------------------------------------------------------------------------


async def test_a_relabelled_passage_is_updated_without_re_embedding(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The failure that cost us corpus v3.

    A release that corrects routing metadata changes no passage text, so `content_sha256`
    stays the same. Keying the whole update on that hash discarded the corrections silently:
    481 passages kept a `clinical_purpose` the release had removed and 164 kept the wrong
    `disease_setting`, while the loader reported success.
    """
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    # Same text, same hash, corrected routing -- exactly what a relabelling release looks like.
    relabelled = chunk(
        disease_setting="prostate_metastatic_hormone_sensitive",
        clinical_purpose=["therapy_indication"],
        not_specified_reason=None,
    )
    embedder = FakeEmbedder()
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [relabelled]),
        embedder=embedder,
    )

    assert report.chunks_unchanged == 1
    assert report.metadata_updated == 1
    # The vector is untouched, because the embedded text is untouched.
    assert embedder.embedded == []

    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None
    assert stored.disease_setting == "prostate_metastatic_hormone_sensitive"
    assert stored.clinical_purpose == ["therapy_indication"]


async def test_a_changed_context_re_embeds_even_when_the_text_is_identical(
    session: AsyncSession, tmp_path: Path
) -> None:
    """`contextual_text` carries the section path and is what gets embedded. Correcting a
    wrong section heading changes the vector without changing a word of the passage."""
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk(embedding_sha256="a" * 64)]),
        embedder=FakeEmbedder(),
    )

    renested = chunk(
        contextual_text="Source: AFU. Section: Cancer localise > Risque faible. La surveillance...",
        embedding_sha256="e" * 64,
    )
    embedder = FakeEmbedder()
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [renested]),
        embedder=embedder,
    )

    assert report.chunks_updated == 1
    assert len(embedder.embedded) == 1


async def test_the_reason_a_passage_carries_no_setting_is_stored(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A general principle and an unlabelled treatment recommendation look identical through
    a filter, and together they are 40% of the corpus."""
    await ingest_release(
        session,
        directory=release(
            tmp_path,
            [document()],
            [chunk(disease_setting="not_specified", not_specified_reason="general_principle")],
        ),
        embedder=FakeEmbedder(),
    )

    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None and stored.not_specified_reason == "general_principle"


# --------------------------------------------------------------------------------------
# Review findings, reproduced. Each of these passed silently before.
# --------------------------------------------------------------------------------------


async def test_a_release_cannot_touch_a_document_it_does_not_declare(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Scope came from every document already in PostgreSQL, so a release for document A
    accepted a chunk belonging to B — and reconciled B's other chunks away."""
    other = document("eau.prostate.2026")
    await ingest_release(
        session,
        directory=release(
            tmp_path,
            [document(), other],
            [chunk(), chunk("eau.0001", document_id="eau.prostate.2026",
                            contextual_text="B one", content_sha256="c" * 64)],
        ),
        embedder=FakeEmbedder(),
    )

    # A release declaring only A, but smuggling a chunk for B.
    with pytest.raises(CorpusError, match="not declared by this release"):
        await ingest_release(
            session,
            directory=release(
                tmp_path,
                [document()],
                [chunk(), chunk("eau.0002", document_id="eau.prostate.2026",
                                contextual_text="B two", content_sha256="d" * 64)],
            ),
            embedder=FakeEmbedder(),
        )

    # B is untouched: both its state and A's survive the refused release.
    remaining = set(
        (await session.execute(sa.select(EvidenceChunk.chunk_id))).scalars().all()
    )
    assert "eau.0001" in remaining


async def test_a_declared_document_with_no_valid_chunks_is_still_emptied(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Reconciliation was built from chunks that survived validation, so a document declared
    with nothing valid was never reconciled and its old passages stayed citable."""
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], []),
        embedder=FakeEmbedder(),
    )

    assert report.chunks_removed == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 0


async def test_changing_the_embedding_model_replaces_every_vector(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Freshness compared the hash and ignored the model, so reingesting under a new model
    reported "unchanged" and kept the old model's vectors -- the exact failure the
    `embedding_model` column exists to prevent."""
    directory = release(tmp_path, [document()], [chunk()])
    await ingest_release(session, directory=directory, embedder=FakeEmbedder("model-a"))

    second = FakeEmbedder("model-b")
    report = await ingest_release(session, directory=directory, embedder=second)

    assert report.chunks_unchanged == 0
    assert len(second.embedded) == 1
    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None and stored.embedding_model == "model-b"


async def test_a_stale_declared_hash_cannot_pin_a_stale_vector(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The hash was taken from the release on trust. A release that edits `contextual_text`
    while leaving its declared hash unchanged would pin the old vector to new text, silently
    and permanently. We compute it from what we actually embed."""
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk(embedding_sha256="f" * 64)]),
        embedder=FakeEmbedder(),
    )

    lying = chunk(contextual_text="Un texte entierement different.", embedding_sha256="f" * 64)
    embedder = FakeEmbedder()
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [lying]),
        embedder=embedder,
    )

    assert report.chunks_updated == 1
    assert len(embedder.embedded) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disease_setting", "prostate_localised_low_risk"),   # British spelling typo
        ("therapy_line", "neo_adjuvant"),
        ("language", "fr-FR"),
        ("clinical_status", "unreviewed"),
        ("not_specified_reason", "unknown"),
        ("cancer_types", "prostate"),                          # scalar where an array belongs
        ("agent_roles", ["urology_oncology", "geriatrics"]),   # undeclared member
        ("content_sha256", "not-a-digest"),
        ("embedding_sha256", "z" * 64),
    ],
)
def test_a_value_the_release_does_not_declare_is_refused(field: str, value: object) -> None:
    """A typo is stored intact and then matches no filter, which is indistinguishable from a
    corpus that does not cover the topic."""
    schema = ReleaseSchema(
        enums={
            name: frozenset(
                (spec.get("enum") or (spec.get("items") or {}).get("enum")) or ()
            )
            for name, spec in SCHEMA["properties"].items()
        },
        required=frozenset(SCHEMA["required"]),
    )

    assert validate_chunk(chunk(**{field: value}), schema) is not None


async def test_one_refused_passage_stores_none_of_the_release(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A partially stored release is neither the old corpus nor the new one, and nothing
    records which passages are missing. The loader used to report success."""
    good = chunk("good.0001")
    bad = chunk("bad.0001", disease_setting="prostate_localised_low_risk",
                contextual_text="Autre", content_sha256="e" * 64)

    with pytest.raises(CorpusError, match="nothing was stored"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [good, bad]),
            embedder=FakeEmbedder(),
        )

    assert await session.scalar(sa.select(sa.func.count()).select_from(EvidenceChunk)) == 0


async def test_a_release_without_its_schema_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """An unvalidated release is how a typo becomes an unreachable passage."""
    with pytest.raises(CorpusError, match="chunk.schema.json"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()], schema=None),
            embedder=FakeEmbedder(),
        )


# --------------------------------------------------------------------------------------
# The text search configurations the stored vectors were built with
# --------------------------------------------------------------------------------------


async def test_an_altered_search_configuration_stops_ingestion(
    session: AsyncSession, tmp_path: Path
) -> None:
    """PostgreSQL's dependency tracking prevents DROP, not ALTER. An altered configuration
    leaves every stored vector built under the old rules -- terms that used to match stop
    matching, and nothing raises. We cannot prevent it, so we refuse to add new vectors to
    the mixture."""
    await session.execute(
        sa.text(
            "ALTER TEXT SEARCH CONFIGURATION public.rcpia_fr "
            "ALTER MAPPING FOR word WITH french_stem"
        )
    )

    with pytest.raises(CorpusError, match="altered since the migration"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()]),
            embedder=FakeEmbedder(),
        )


async def test_the_expected_configuration_passes(session: AsyncSession) -> None:
    """Both configurations fold accents before stemming, and the check knows it."""
    await verify_search_configurations(session)
