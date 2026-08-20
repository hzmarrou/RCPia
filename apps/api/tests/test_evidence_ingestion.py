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
    ingest_release,
    validate_chunk,
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
        "clinical_purpose": ["treatment_selection"],
        "agent_roles": ["urology_oncology"],
        "unit_type": "recommendation",
        "recommendation_number": 12,
        "retrieval": {"keyword_text": "surveillance active risque faible"},
        "content_sha256": "a" * 64,
        "clinical_status": "poc_unreviewed",
    }
    record.update(overrides)
    return record


def release(tmp_path: Path, documents: list[dict], chunks: list[dict]) -> Path:
    directory = tmp_path / "release"
    directory.mkdir(exist_ok=True)
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

    report = await ingest_release(session, directory=directory, embedder=FakeEmbedder())

    assert report.chunks_inserted == 0
    assert any("is not in this release" in line for line in report.rejected)


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
    other = chunk("afu.prostate.2024-2026.localise.01.0002", content_sha256="c" * 64)
    await ingest_release(
        session,
        directory=release(tmp_path, [document()], [unchanged, other]),
        embedder=FakeEmbedder(),
    )

    edited = chunk("afu.prostate.2024-2026.localise.01.0002", content_sha256="d" * 64)
    embedder = FakeEmbedder()
    report = await ingest_release(
        session,
        directory=release(tmp_path, [document()], [unchanged, edited]),
        embedder=embedder,
    )

    assert report.chunks_unchanged == 1
    assert report.chunks_updated == 1
    assert len(embedder.embedded) == 1
