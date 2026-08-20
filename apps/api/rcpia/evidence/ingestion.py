"""Loading a prepared corpus release into PostgreSQL.

The corpus arrives already parsed, chunked and labelled — `documents.jsonl`,
`chunks.jsonl`, and a schema describing them. This module validates it and stores it. It
deliberately does **not** parse or chunk: the corpus agent produces better provenance than
we would, including page numbers aligned back to the source PDFs, and re-deriving any of it
here would mean two systems disagreeing about what a passage is.

Three properties matter more than speed.

**A malformed record is refused, not stored quietly.** A passage missing its
`disease_setting` would be invisible to every filter, which reads exactly like a corpus
that does not cover the topic.

**Re-running is cheap, but never lossy.** Metadata is written for every passage in the
release, unconditionally. Only the *embedding* is skipped when nothing changed.

Those are two different questions and conflating them cost us a release. Corpus v3 corrected
routing metadata on 975 passages without altering a word of their text, so their
`content_sha256` was unchanged. Keying the whole update on that hash meant the corrections
were silently discarded: 481 passages kept a `clinical_purpose` value the release had
removed, and 164 kept the wrong `disease_setting`. Nothing failed, and the loader reported
success.

**A passage is stored with the model that embedded it.** Vectors from different models are
not comparable, so a table holding both would rank nonsense with nothing to show for it.

**A release replaces its own documents, rather than adding to them.** A passage the corpus
agent removed must disappear here too, and a passage whose id changed must replace the old
row rather than sit beside it. Only documents named in the release are touched, so loading a
six-document slice does not delete the rest of the corpus.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evidence.embedding import BATCH_SIZE, Embedder
from rcpia.evidence.models import EvidenceChunk, EvidenceDocument

# Every field the loader needs. A record missing one of these cannot be routed or cited,
# so it is refused rather than stored with a hole in it.
REQUIRED_CHUNK_FIELDS = (
    "chunk_id",
    "document_id",
    "text",
    "contextual_text",
    "section_path",
    "cancer_types",
    "disease_setting",
    "therapy_line",
    "clinical_purpose",
    "agent_roles",
    "language",
    "clinical_status",
    "content_sha256",
)

# Sections about diseases RCPia does not cover. The NCCN "Bladder Cancer" guideline also
# documents upper tract urothelial carcinoma and carcinoma of the urethra, and those sections
# arrived tagged `cancer_types = ['bladder']` — passing every bladder filter we can write, so
# a patient with a bladder tumour could be shown nephron-preserving criteria for a tumour of
# the renal pelvis.
#
# **Matched against section headings, never against the passage text.** A text rule looks
# obvious and is wrong: 38 chunks mention `UTUC` or `renal pelvis` somewhere in their body,
# but only 16 sit in a section *about* those diseases; the two rules overlap on 14. The
# other 24 are ordinary bladder
# content — `ABBREVIATIONS`, which defines the term; `Adjuvant Systemic Therapy`;
# `Urethrectomy`, which is part of a cystectomy. Dropping them would be over-filtering, and
# over-filtering is invisible: the system reports no relevant evidence, which reads as a gap
# in the guideline rather than a bug in ours.
#
# This is defence in depth. The corpus agent is excluding these sections at source in v3,
# where the document structure is still available; we only have the flattened path. If they
# rename a section this stops matching, which is the correct failure — by then the section is
# already gone.
OUT_OF_SCOPE_SECTIONS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^upper tract urothelial carcinoma$",
        r"^upper gu tract tumors$",
        r"utuc risk group",
        r"^primary carcinoma of the urethra$",
        r"renal pelvis and ureter cancer",
        r"therapy for upper tract tumors$",
        r"intraurethral therapy for primary carcinoma",
    )
)


def names_an_out_of_scope_disease(section_path: list[str]) -> str | None:
    """The heading that puts this passage outside the corpus scope, or None."""
    for heading in section_path:
        text = str(heading).strip()
        if any(pattern.search(text) for pattern in OUT_OF_SCOPE_SECTIONS):
            return text
    return None


def claims_exclusive_settings(setting: str) -> bool:
    """Whether one setting string claims two states that cannot both hold.

    The corpus schema now makes `disease_setting` a single enum value, so this is a second
    lock on the same door — but it caught 13% of the previous release, and no filter can be
    written correctly while a passage is both metastatic and not.

    **Read as tokens, never as substrings.** `metastatic` is a substring of
    `non_metastatic`, and `mibc` of `nmibc`, so a substring test rejects every correct
    non-metastatic and NMIBC passage. That mistake has now been made three times in this
    codebase in different files; the tokens are the meaning, the characters are not.
    """
    tokens = setting.lower().split("_")

    def occurrences(word: str, *, after_non: bool) -> bool:
        """Whether `word` appears with — or without — `non` immediately before it.

        Both directions are asked separately, so one negated occurrence cannot mask a bare
        one: `metastatic_non_metastatic` claims both states and must be refused.
        """
        return any(
            token == word
            and ((index > 0 and tokens[index - 1] == "non") is after_non)
            for index, token in enumerate(tokens)
        )

    claims_metastatic = occurrences("metastatic", after_non=False)
    claims_non_metastatic = (
        occurrences("metastatic", after_non=True) or "localized" in tokens
    )
    return (claims_metastatic and claims_non_metastatic) or (
        "nmibc" in tokens and "mibc" in tokens
    )


class CorpusError(Exception):
    """The release cannot be loaded."""


@dataclass
class IngestionReport:
    """What a run did, so a re-run can be compared with the one before it."""

    release_id: str | None = None
    documents: int = 0
    chunks_seen: int = 0
    chunks_inserted: int = 0
    chunks_updated: int = 0
    chunks_unchanged: int = 0
    metadata_updated: int = 0
    chunks_removed: int = 0
    documents_removed: int = 0
    embedded: int = 0
    rejected: list[str] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return self.chunks_inserted + self.chunks_updated


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise CorpusError(f"Missing corpus file: {path}")
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{path.name} line {number} is not valid JSON: {exc}") from exc
    return records


def validate_chunk(record: dict[str, Any]) -> str | None:
    """Why this record cannot be stored, or None if it can."""
    missing = [name for name in REQUIRED_CHUNK_FIELDS if not record.get(name)]
    if missing:
        return f"missing {', '.join(missing)}"

    setting = str(record["disease_setting"])
    if claims_exclusive_settings(setting):
        return f"disease_setting {setting!r} claims two exclusive settings"

    if not isinstance(record.get("cancer_types"), list):
        return "cancer_types is not a list"

    heading = names_an_out_of_scope_disease(list(record.get("section_path") or []))
    if heading is not None:
        return f"section {heading!r} is about a disease RCPia does not cover"
    return None


def _document_row(record: dict[str, Any], release_id: str | None) -> dict[str, Any]:
    version = record.get("version") or {}
    source = record.get("source") or {}
    licence = record.get("licence") or {}
    return {
        "document_id": record["document_id"],
        "ref_id": record.get("ref_id"),
        "title": record.get("title") or record["document_id"],
        "short_title": record.get("short_title"),
        "publisher": record.get("publisher"),
        "authority": record.get("authority"),
        "document_type": record.get("document_type"),
        "language": record.get("language"),
        "version_label": version.get("label"),
        "source_url": source.get("source_url"),
        "clinical_status": record.get("clinical_status") or "unknown",
        "licence_status": licence.get("status"),
        "ai_use_allowed": licence.get("ai_use_allowed"),
        "release_id": release_id,
    }


def _chunk_row(record: dict[str, Any]) -> dict[str, Any]:
    location = record.get("source_location") or {}
    citation = record.get("citation") or {}
    retrieval = record.get("retrieval") or {}
    return {
        "chunk_id": record["chunk_id"],
        "document_id": record["document_id"],
        "text": record["text"],
        "contextual_text": record["contextual_text"],
        "section_path": list(record.get("section_path") or []),
        "cancer_types": list(record.get("cancer_types") or []),
        "disease_setting": record["disease_setting"],
        "therapy_line": record["therapy_line"],
        "clinical_purpose": list(record.get("clinical_purpose") or []),
        "agent_roles": list(record.get("agent_roles") or []),
        "unit_type": record.get("unit_type"),
        "language": record["language"],
        "clinical_status": record["clinical_status"],
        "recommendation_number": record.get("recommendation_number"),
        "page_start": location.get("page_start"),
        "page_end": location.get("page_end"),
        "locator_method": location.get("locator_method"),
        "locator_confidence": location.get("locator_confidence"),
        "citation_label": citation.get("label"),
        "source_url": citation.get("source_url"),
        # Fall back to the passage itself: a release without a prepared keyword field is
        # still searchable, just less well.
        "keyword_text": retrieval.get("keyword_text") or record["text"],
        "content_sha256": record["content_sha256"],
        # Falls back to the content hash for a release that predates the field: the
        # worst case is re-embedding a passage that did not need it.
        "embedding_sha256": record.get("embedding_sha256") or record["content_sha256"],
        "not_specified_reason": record.get("not_specified_reason"),
    }


async def ingest_release(
    session: AsyncSession,
    *,
    directory: Path,
    embedder: Embedder,
    release_id: str | None = None,
) -> IngestionReport:
    """Load `documents.jsonl` and `chunks.jsonl` from a corpus release directory."""
    documents = read_jsonl(directory / "documents.jsonl")
    chunks = read_jsonl(directory / "chunks.jsonl")
    report = IngestionReport(release_id=release_id, chunks_seen=len(chunks))

    if not documents:
        raise CorpusError(f"{directory} holds no documents")

    # ------------------------------------------------------------------ documents
    excluded: list[str] = []
    for record in documents:
        # A document the release itself excluded should not be loaded, however tidy the
        # record looks — and if a previous release loaded it, it must now be removed.
        # Skipping it would leave withdrawn guidance in place, citable and looking current.
        if record.get("clinical_status") == "excluded":
            excluded.append(record["document_id"])
            continue
        row = _document_row(record, release_id)
        existing = await session.get(EvidenceDocument, row["document_id"])
        if existing is None:
            session.add(EvidenceDocument(**row))
        else:
            for key, value in row.items():
                setattr(existing, key, value)
        report.documents += 1

    for document_id in excluded:
        # The chunks go with it: the foreign key cascades.
        existing_excluded = await session.get(EvidenceDocument, document_id)
        if existing_excluded is not None:
            await session.delete(existing_excluded)
            report.documents_removed += 1

    await session.flush()
    known = {
        document_id
        for (document_id,) in (
            await session.execute(sa.select(EvidenceDocument.document_id))
        ).all()
    }

    # ------------------------------------------------------------------ passages
    # What we already hold, and whether its vector is current. `embedding_sha256` is NULL
    # for anything loaded before that column existed, which correctly forces a re-embed.
    stored = {
        chunk_id: (embedding_sha, embedding is not None)
        for chunk_id, embedding_sha, embedding in (
            await session.execute(
                sa.select(
                    EvidenceChunk.chunk_id,
                    EvidenceChunk.embedding_sha256,
                    EvidenceChunk.embedding,
                )
            )
        ).all()
    }

    # Every chunk this release declares for a document it also declares. Anything else
    # belonging to those documents is stale and removed at the end.
    release_documents: set[str] = set()
    seen_chunks: set[str] = set()

    pending: list[tuple[dict[str, Any], bool]] = []
    metadata_only: list[dict[str, Any]] = []
    for record in chunks:
        problem = validate_chunk(record)
        if problem is not None:
            report.rejected.append(f"{record.get('chunk_id', '?')}: {problem}")
            continue
        if record["document_id"] not in known:
            report.rejected.append(
                f"{record['chunk_id']}: document {record['document_id']} is not in this "
                "release"
            )
            continue

        row = _chunk_row(record)
        release_documents.add(row["document_id"])
        seen_chunks.add(row["chunk_id"])

        previous_hash, has_vector = stored.get(row["chunk_id"], (None, False))
        exists = row["chunk_id"] in stored
        # Re-embed when the embedded text changed, or when there is no vector to keep.
        if exists and has_vector and previous_hash == row["embedding_sha256"]:
            report.chunks_unchanged += 1
            # Metadata still goes in: a release that only corrects routing changes no text.
            metadata_only.append(row)
            continue
        pending.append((row, exists))

    # Passages whose vector is current but whose routing may not be. This is the path that
    # was missing, and its absence was invisible: the loader reported these as "unchanged".
    for row in metadata_only:
        await session.execute(
            sa.update(EvidenceChunk)
            .where(EvidenceChunk.chunk_id == row["chunk_id"])
            .values(**row)
        )
        report.metadata_updated += 1
    if metadata_only:
        await session.flush()

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        vectors = await embedder.embed([row["contextual_text"] for row, _ in batch])
        report.embedded += len(vectors)

        for (row, is_update), vector in zip(batch, vectors, strict=True):
            row = {**row, "embedding": vector, "embedding_model": embedder.model}
            if is_update:
                await session.execute(
                    sa.update(EvidenceChunk)
                    .where(EvidenceChunk.chunk_id == row["chunk_id"])
                    .values(**row)
                )
                report.chunks_updated += 1
            else:
                session.add(EvidenceChunk(**row))
                report.chunks_inserted += 1
        await session.flush()

    # A rejected chunk is deliberately absent from `seen_chunks`: if it was stored by an
    # earlier release and has since become invalid, it should go rather than linger.
    if release_documents:
        removal = (
            sa.delete(EvidenceChunk)
            .where(
                EvidenceChunk.document_id.in_(release_documents),
                sa.not_(EvidenceChunk.chunk_id.in_(seen_chunks)),
            )
            .returning(EvidenceChunk.chunk_id)
        )
        removed = (await session.execute(removal)).scalars().all()
        report.chunks_removed = len(removed)

    return report
