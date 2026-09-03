"""Loading a guideline corpus into PostgreSQL.

The properties that matter are about what does *not* get stored. A passage missing its
routing metadata would be invisible to every filter, which reads exactly like a corpus that
does not cover the topic — so it is refused rather than stored with a hole in it.
"""

import hashlib
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
from rcpia.evidence.local_blocks import LOCALLY_BLOCKED, Block
from rcpia.evidence.manifest import (
    MANIFEST_SCHEMA_VERSION,
    SOURCE_ROLES_VERSION,
    ManifestError,
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
        # Required of anything entering the corpus, and mirrored into the fixture manifest so
        # the two agree by construction. A test that wants them to disagree says so.
        "source_family": "TEST",
        "source_role": "treatment_guideline",
        "position_domains": ["therapy_indication"],
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


# Sentinel: `manifest=None` means "write no manifest", which is itself a test case, so the
# default cannot be None.
_DERIVE = object()

def manifest_for(documents: list[dict], chunks: list[dict]) -> dict:
    """The coverage proof a release must carry, derived from what it delivers.

    Agrees with the release by construction, so a test that wants a *disagreeing* manifest
    passes one explicitly and the disagreement is the thing under test.
    """
    counts: dict[str, int] = {}
    for record in chunks:
        owner = record.get("document_id")
        if isinstance(owner, str):
            counts[owner] = counts.get(owner, 0) + 1
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release_id": "test-release",
        "source_roles_version": SOURCE_ROLES_VERSION,
        "reference_folders": [{"folder": "ref-01", "status": "accounted"}],
        "references": [
            {
                "reference_id": record["document_id"],
                "files": [f"ref-01/{record['document_id']}.md"],
                # A document the release withdraws is still accounted for -- that is what the
                # `excluded` disposition exists for, and dropping it from the manifest would
                # leave its folder claimed by nothing.
                **(
                    {}
                    if record.get("clinical_status") == "excluded"
                    else {
                        "source_family": record.get("source_family", "TEST"),
                        "source_role": record.get("source_role", "treatment_guideline"),
                        "position_domains": record.get("position_domains", ["therapy_indication"]),
                    }
                ),
                "dispositions": [
                    {"kind": "excluded", "reason": "withdrawn by this release"}
                    if record.get("clinical_status") == "excluded"
                    else {
                        "kind": "corpus",
                        "document_id": record["document_id"],
                        "chunks": counts.get(record["document_id"], 0),
                    }
                ],
            }
            for record in documents
        ],
    }


def release(
    tmp_path: Path,
    documents: list[dict],
    chunks: list[dict],
    *,
    schema: dict | None = SCHEMA,
    manifest: object = _DERIVE,
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
    if manifest is _DERIVE:
        manifest = manifest_for(documents, chunks)
    if manifest is not None:
        assert isinstance(manifest, dict)
        # Digests over the files just written. A checksum block naming none of the release's
        # own files verifies nothing while looking as though it verified everything.
        manifest.setdefault(
            "checksums",
            {
                "algorithm": "sha256",
                "release": "0" * 64,
                "files": {
                    name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    for name in ("documents.jsonl", "chunks.jsonl", "chunk.schema.json")
                    if (directory / name).exists()
                },
            },
        )
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
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
    # The caller's name must agree with the manifest's -- checked in `ingest_release`, since
    # storing a name the validated manifest does not carry records a release nobody proved.
    named = manifest_for([document()], [chunk()])
    named["release_id"] = "corpus-v2.0"
    directory = release(tmp_path, [document()], [chunk()], manifest=named)
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


async def test_a_decision_table_is_stored_and_indexed_but_never_embedded(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A table of bands and cutoffs is exact tokens. Full-text search matches `PSA < 10`; a
    vector cannot tell it from `PSA > 20`, so the dense leg is given nothing to match."""
    directory = release(
        tmp_path, [document()], [chunk(unit_type="decision_table", embedding_eligible=False)]
    )
    embedder = FakeEmbedder()

    report = await ingest_release(session, directory=directory, embedder=embedder)

    assert embedder.embedded == [], "a decision table was sent to the embedder"
    assert report.embedded == 0
    assert report.chunks_inserted == 1
    stored = (await session.execute(sa.select(EvidenceChunk))).scalars().all()
    assert len(stored) == 1
    # Stored and answerable -- keyword-only is not exclusion.
    assert stored[0].embedding is None
    assert stored[0].retrieval_eligible is True
    assert stored[0].keyword_text


async def test_a_keyword_only_passage_is_not_re_embedded_on_every_ingest(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A passage with no vector is never `fresh` by the usual test, so deciding this after
    freshness would send every decision table to the embedder on every single ingest."""
    directory = release(
        tmp_path, [document()], [chunk(unit_type="decision_table", embedding_eligible=False)]
    )
    await ingest_release(session, directory=directory, embedder=FakeEmbedder())

    second = FakeEmbedder()
    await ingest_release(session, directory=directory, embedder=second)

    assert second.embedded == []


async def test_a_passage_that_becomes_keyword_only_loses_its_vector(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Otherwise a table embedded under an earlier release keeps answering the dense leg
    forever, and the change would be invisible."""
    await ingest_release(
        session, directory=release(tmp_path, [document()], [chunk()]), embedder=FakeEmbedder()
    )
    before = (await session.execute(sa.select(EvidenceChunk))).scalars().all()
    assert before[0].embedding is not None

    directory = release(
        tmp_path, [document()], [chunk(unit_type="decision_table", embedding_eligible=False)]
    )
    await ingest_release(session, directory=directory, embedder=FakeEmbedder())

    after = (await session.execute(sa.select(EvidenceChunk))).scalars().all()
    assert after[0].embedding is None
    assert after[0].embedding_model is None


async def test_a_release_that_says_nothing_about_embedding_is_embedded(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Every release before v5.0 omits the field, and omitting it must not silently strip the
    corpus of its vectors."""
    directory = release(tmp_path, [document()], [chunk()])
    embedder = FakeEmbedder()

    await ingest_release(session, directory=directory, embedder=embedder)

    assert len(embedder.embedded) == 1


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


async def test_a_release_carrying_a_field_we_do_not_implement_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Three times the corpus agent has encoded something that matters in a field this loader
    discarded without a word. A field we do not implement is a decision nobody has made."""
    with pytest.raises(CorpusError, match="does not implement"):
        await ingest_release(
            session,
            directory=release(
                tmp_path, [document()], [{**chunk(), "supersedes_guidance": "x"}]
            ),
            embedder=FakeEmbedder(),
        )


async def test_retrieval_eligibility_is_stored(
    session: AsyncSession, tmp_path: Path
) -> None:
    await ingest_release(
        session,
        directory=release(
            tmp_path,
            [document()],
            [
                chunk(
                    retrieval_eligible=False,
                    retrieval_exclusion_reason="historical_treatment_guidance_superseded",
                )
            ],
        ),
        embedder=FakeEmbedder(),
    )

    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None
    assert stored.retrieval_eligible is False
    assert stored.retrieval_exclusion_reason == "historical_treatment_guidance_superseded"


async def test_a_release_that_says_nothing_leaves_every_passage_retrievable(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Which is how every release before v3.1 behaved."""
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None and stored.retrieval_eligible is True


@pytest.mark.parametrize(
    "field",
    ["disease_setting", "therapy_line", "language", "cancer_types",
     "clinical_purpose", "agent_roles"],
)
async def test_a_schema_that_constrains_nothing_is_refused(
    session: AsyncSession, tmp_path: Path, field: str
) -> None:
    """A schema that constrains nothing is worse than no schema, because it looks like
    validation. Without an enum on a routing field, `TYPO_SETTING` and `fr-FR` load intact
    and then match no filter -- indistinguishable from a corpus that does not cover the
    topic."""
    weakened = {
        "required": SCHEMA["required"],
        "properties": {k: v for k, v in SCHEMA["properties"].items() if k != field},
    }

    with pytest.raises(CorpusError, match="no permitted values"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()], schema=weakened),
            embedder=FakeEmbedder(),
        )


async def test_an_empty_schema_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    with pytest.raises(CorpusError, match="no permitted values"):
        await ingest_release(
            session,
            directory=release(
                tmp_path, [document()], [chunk()], schema={"required": [], "properties": {}}
            ),
            embedder=FakeEmbedder(),
        )


async def test_the_configuration_check_covers_ascii_tokens(
    session: AsyncSession,
) -> None:
    """Ordinary English words are `asciiword`, not `word`. Checking only `word`, `hword` and
    `hword_part` meant altering `asciiword` to `simple` broke English stemming across 81% of
    the corpus while the check passed."""
    await session.execute(
        sa.text(
            "ALTER TEXT SEARCH CONFIGURATION public.rcpia_en "
            "ALTER MAPPING FOR asciiword WITH simple"
        )
    )

    with pytest.raises(CorpusError, match="has been altered"):
        await verify_search_configurations(session)


async def test_locally_blocked_passages_survive_a_reingest(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The stopgap has to outlast the release that keeps re-asserting the passage is fine.

    Metadata is written unconditionally on every load, so a block applied by hand would be
    undone by the next ingest -- silently, since nothing would report it.
    """
    # The block's own declared owner, not the default fixture document. This test passed
    # before only because the blocking UPDATE was unscoped -- it reached rows belonging to
    # documents the release had never declared, which was itself the bug.
    blocked_id, blocked = next(iter(LOCALLY_BLOCKED.items()))
    owned = [
        chunk_id
        for chunk_id, block in LOCALLY_BLOCKED.items()
        if block.document_id == blocked.document_id
    ]
    # Every block the document owns, because a release that delivers only some of them has
    # lost the rest -- which the loader refuses, correctly.
    directory = release(
        tmp_path,
        [document(document_id=blocked.document_id)],
        [chunk(chunk_id, document_id=blocked.document_id) for chunk_id in owned],
    )

    first = await ingest_release(session, directory=directory, embedder=FakeEmbedder())
    assert first.locally_blocked == len(owned)

    second = await ingest_release(session, directory=directory, embedder=FakeEmbedder())
    assert second.locally_blocked == len(owned)

    stored = await session.scalar(
        sa.select(EvidenceChunk).where(EvidenceChunk.chunk_id == blocked_id)
    )
    assert stored is not None and stored.retrieval_eligible is False


async def test_a_passage_not_on_the_list_stays_retrievable(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Over-filtering is the invisible failure, so the list is read as a list, not a rule."""
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk("some.other.chunk.0001")]),
        embedder=FakeEmbedder(),
    )

    assert report.locally_blocked == 0
    stored = await session.scalar(sa.select(EvidenceChunk))
    assert stored is not None and stored.retrieval_eligible is True


async def test_an_altered_dictionary_stops_ingestion(session: AsyncSession) -> None:
    """`ALTER TEXT SEARCH DICTIONARY french_stem (StopWords = ...)` changes what every stored
    vector should have contained without touching a single token mapping, so a fingerprint of
    the mappings alone would still match."""
    await session.execute(
        sa.text("ALTER TEXT SEARCH DICTIONARY french_stem (StopWords = 'english')")
    )

    with pytest.raises(CorpusError, match="dictionary has been altered"):
        await verify_search_configurations(session)


async def test_a_release_whose_manifest_omits_a_delivered_document_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A coverage proof that omits delivered content proves nothing about its release."""
    documents = [document(), document(document_id="doc-2")]
    chunks = [chunk(), chunk(chunk_id="c2", document_id="doc-2")]
    partial = manifest_for([document()], [chunk()])

    with pytest.raises(ManifestError, match="does not account for: doc-2"):
        await ingest_release(
            session,
            directory=release(tmp_path, documents, chunks, manifest=partial),
            embedder=FakeEmbedder(),
        )


async def test_a_manifest_declaring_a_document_that_did_not_arrive_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The EAU bladder case, end to end: two parsed guidelines in the same folder and
    edition as one that was loaded, and they never came."""
    overreaching = manifest_for([document(), document(document_id="doc-2")], [chunk()])

    with pytest.raises(ManifestError, match="does not deliver: doc-2"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()], manifest=overreaching),
            embedder=FakeEmbedder(),
        )


async def test_a_release_is_refused_before_anything_is_embedded(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Checked before the embedding loop, because a release that does not match its manifest
    should cost nothing and every passage below that line is billed per thousand tokens."""
    embedder = FakeEmbedder()
    with pytest.raises(ManifestError):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()], manifest=None),
            embedder=embedder,
        )

    assert embedder.embedded == []


async def test_a_block_whose_passage_vanished_from_the_release_is_refused(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A block is an `UPDATE ... WHERE chunk_id IN (...)`, and a re-chunked release changes
    chunk ids. A missing id matches nothing, updates nothing and raises nothing: the count
    falls to zero and the line stops printing.

    One of the ten is the passage a review reproduced leaking at relevance 0.774 — androgen
    deprivation for biochemical recurrence, offered to a question about early low-risk
    disease. A silent re-chunk would put it back.
    """
    monkeypatch.setattr(
        "rcpia.evidence.ingestion.LOCALLY_BLOCKED",
        {
            "afu.prostate.2024-2026.moved.99.9999": Block(
                document_id="afu.prostate.2024-2026", reason="local_setting_mislabelled"
            )
        },
    )

    with pytest.raises(CorpusError, match="not in this release"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()]),
            embedder=FakeEmbedder(),
        )


async def test_a_block_for_a_document_this_release_does_not_carry_is_ignored(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading one document must not fail over a block on another. The corpus is loaded in
    slices and always has been."""
    monkeypatch.setattr(
        "rcpia.evidence.ingestion.LOCALLY_BLOCKED",
        {
            "nccn.prostate.6.2026.elsewhere.01.0001": Block(
                document_id="nccn.prostate.6.2026", reason="local_not_guidance"
            )
        },
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    assert report.chunks_inserted == 1
    assert report.locally_blocked == 0


async def test_a_document_carrying_a_field_we_do_not_implement_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Chunks have had this guard since v3; documents never did.

    `_document_row` builds an explicit dictionary and drops everything it does not name, so a
    document field this loader does not implement disappears without a word. It came within
    one release of mattering: v5 adds `source_family`, `source_role` and `position_domains`
    to `documents.jsonl`, and a dry-run before those columns existed would have reported
    success while discarding all three.
    """
    with pytest.raises(CorpusError, match="does not implement: jurisdiction"):
        await ingest_release(
            session,
            directory=release(
                tmp_path, [{**document(), "jurisdiction": "MA"}], [chunk()]
            ),
            embedder=FakeEmbedder(),
        )


async def test_source_metadata_is_stored(session: AsyncSession, tmp_path: Path) -> None:
    """`source_family` was derived by splitting `document_id` on a dot, which worked by
    accident of the corpus agent's naming. Retrieval stratifies by family, so it has to be a
    declared property."""
    record = {
        **document(),
        "source_family": "AFU",
        "source_role": "treatment_guideline",
        "position_domains": ["therapy_indication"],
    }
    await ingest_release(
        session,
        directory=release(tmp_path, [record], [chunk()]),
        embedder=FakeEmbedder(),
    )

    stored = await session.get(EvidenceDocument, "afu.prostate.2024-2026")
    assert stored is not None
    assert (stored.source_family, stored.source_role, stored.position_domains) == (
        "AFU",
        "treatment_guideline",
        ["therapy_indication"],
    )


async def test_a_manifest_disagreeing_with_the_document_record_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The same three facts live in two files, and a disagreement would be invisible:
    retrieval reads the document record, while the comparison step's correctness rests on the
    manifest's declared roles."""
    record = {
        **document(),
        "source_family": "AFU",
        "source_role": "specialist_guidance",
        "position_domains": ["supportive_care"],
    }
    claims_otherwise = manifest_for([record], [chunk()])
    claims_otherwise["references"][0]["source_role"] = "treatment_guideline"
    claims_otherwise["references"][0]["position_domains"] = ["therapy_indication"]

    with pytest.raises(ManifestError, match="disagree"):
        await ingest_release(
            session,
            directory=release(
                tmp_path, [record], [chunk()], manifest=claims_otherwise
            ),
            embedder=FakeEmbedder(),
        )


async def test_a_rechunk_that_moves_a_blocked_passage_is_refused(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The case the first version of this guard did not catch.**

    It read the rows the blocking UPDATE had touched. On a re-chunk the *old* row is still in
    the database when that UPDATE runs, so it is blocked, satisfies the check, and is then
    deleted as stale a few lines later — while its replacement, carrying a new id, is never
    blocked at all. The guard written to stop a silent unblocking silently permitted one.

    Here the passage is loaded and blocked, then the same document is re-delivered with the
    passage under a new id. Nothing about the database has changed at the moment of the check.
    """
    blocked = Block(
        document_id="afu.prostate.2024-2026", reason="local_setting_mislabelled"
    )
    monkeypatch.setattr(
        "rcpia.evidence.ingestion.LOCALLY_BLOCKED",
        {"afu.prostate.2024-2026.original.01.0001": blocked},
    )

    first = await ingest_release(
        session,
        directory=release(
            tmp_path,
            [document()],
            [chunk(chunk_id="afu.prostate.2024-2026.original.01.0001")],
        ),
        embedder=FakeEmbedder(),
    )
    assert first.locally_blocked == 1

    # Same document, same text, new chunk id -- exactly what a re-chunk produces.
    with pytest.raises(CorpusError, match="not in this release"):
        await ingest_release(
            session,
            directory=release(
                tmp_path,
                [document()],
                [chunk(chunk_id="afu.prostate.2024-2026.rechunked.01.0001")],
            ),
            embedder=FakeEmbedder(),
        )


async def test_a_slice_does_not_touch_blocks_it_does_not_own(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The blocking UPDATE was unscoped, so loading an AFU slice would update NCCN and SIOG
    rows it never touched and report them as this release's work."""
    monkeypatch.setattr(
        "rcpia.evidence.ingestion.LOCALLY_BLOCKED",
        {
            "nccn.prostate.6.2026.elsewhere.01.0001": Block(
                document_id="nccn.prostate.6.2026", reason="local_not_guidance"
            )
        },
    )
    # A row belonging to a document this release does not declare.
    await ingest_release(
        session,
        directory=release(
            tmp_path,
            [document(document_id="nccn.prostate.6.2026")],
            [
                chunk(
                    chunk_id="nccn.prostate.6.2026.elsewhere.01.0001",
                    document_id="nccn.prostate.6.2026",
                )
            ],
        ),
        embedder=FakeEmbedder(),
    )

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()]),
        embedder=FakeEmbedder(),
    )

    assert report.locally_blocked == 0


async def test_an_unknown_field_inside_a_nested_document_object_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """`version.get("label")` reads one key and ignores the rest, so a release adding
    `version.superseded_by` would be accepted in full and understood in part."""
    record = {**document(), "version": {"label": "2024-2026", "superseded_by": "2027"}}
    with pytest.raises(CorpusError, match="version carries fields"):
        await ingest_release(
            session,
            directory=release(tmp_path, [record], [chunk()]),
            embedder=FakeEmbedder(),
        )


async def test_a_nested_document_object_that_is_not_an_object_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    record = {**document(), "licence": "unknown"}
    with pytest.raises(CorpusError, match="licence must be an object"):
        await ingest_release(
            session,
            directory=release(tmp_path, [record], [chunk()]),
            embedder=FakeEmbedder(),
        )


async def test_a_release_named_differently_by_caller_and_manifest_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    """**Checked in ingestion, not in the command that usually calls it.**

    It was enforced in `load.py`, so `ingest_release(..., release_id="wrong")` stored a name
    differing from the manifest whose coverage had just been validated. The CLI is one caller;
    the property is about ingestion.
    """
    named = manifest_for([document()], [chunk()])
    named["release_id"] = "rcpia-corpus-v5.0"

    with pytest.raises(ManifestError, match="caller names this release 'rcpia-corpus-v4.0'"):
        await ingest_release(
            session,
            directory=release(tmp_path, [document()], [chunk()], manifest=named),
            embedder=FakeEmbedder(),
            release_id="rcpia-corpus-v4.0",
        )


async def test_the_manifest_name_is_what_gets_stored(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The manifest is the file whose coverage was validated, so it is the name recorded."""
    named = manifest_for([document()], [chunk()])
    named["release_id"] = "rcpia-corpus-v5.0"

    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [chunk()], manifest=named),
        embedder=FakeEmbedder(),
    )

    assert report.release_id == "rcpia-corpus-v5.0"
    stored = await session.get(EvidenceDocument, "afu.prostate.2024-2026")
    assert stored is not None and stored.release_id == "rcpia-corpus-v5.0"
