"""Storing extracted facts, and the job that produces them.

Facts are append-only, so this module never rewrites one. Re-running extraction after a
re-parse, or after another dossier arrives, produces a new generation of facts citing the
new chunks; the ledger reads the newest generation, and the older ones remain as the
record of what the machine said before.

The whole case is read at once. A dossier and its addendum are two documents describing
one patient, so a value read from each must be compared, not stored twice as though each
document were its own case — that is how a creatinine of 17.33 in one and 173.3 in the
other would otherwise be silently reduced to whichever was seen first.
"""

import hashlib
import re
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases.extraction import FactCandidate, ResolvedFact, extract_from_text, resolve
from rcpia.cases.fact_schema import FIELDS_BY_KEY
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    Case,
    DocumentChunk,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    JobStatus,
    ParseStatus,
    UploadedDocument,
)
from rcpia.jobs.models import JobOutcome

# Fields whose value is itself a date, worth recording as one.
DATE_FIELDS = frozenset({"diagnosis_date", "pathology_date"})
DATE_PATTERNS = (
    (re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b"), ("day", "month", "year")),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("year", "month", "day")),
)


async def extract_case_facts(session: AsyncSession, job: AnalysisJob) -> JobOutcome | None:
    """Extract facts for the whole case, reading every parsed document together."""
    case = await session.scalar(
        sa.select(Case).where(
            Case.id == job.case_id, Case.organization_id == job.organization_id
        )
    )
    if case is None:
        return JobOutcome(
            status=JobStatus.failed, error_message="Dossier introuvable", retryable=False
        )

    documents = list(
        (
            await session.execute(
                sa.select(UploadedDocument)
                .where(
                    UploadedDocument.case_id == case.id,
                    UploadedDocument.parse_status == ParseStatus.parsed,
                )
                .order_by(UploadedDocument.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not documents:
        return JobOutcome(
            status=JobStatus.waiting_for_user,
            error_message="Aucun document analysé : déposez un dossier lisible.",
        )

    signature = extraction_signature(documents)
    if await _already_extracted(session, case.id, signature):
        # At-least-once delivery: this exact set of documents and parse runs has already
        # been read, and facts are append-only, so repeating it would duplicate the
        # machine's reading.
        return None

    candidates: list[FactCandidate] = []
    for document in documents:
        candidates.extend(await _candidates_for(session, document))

    resolved = resolve(candidates, cancer_type=case.cancer_type)
    for fact in resolved:
        for row in _to_rows(case, fact, signature):
            session.add(row)

    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            event_type="case.facts_extracted",
            entity_type="cases",
            entity_id=case.id,
            payload={
                "documents": len(documents),
                "facts": len(resolved),
                "extraction_signature": signature,
            },
        )
    )
    await session.flush()
    return None


def extraction_signature(documents: list[UploadedDocument]) -> str:
    """Identify exactly which reading of the case this is.

    Two documents at parse run 0 is a different reading from the same two where one has
    been re-parsed, and from either one alone.
    """
    material = ";".join(
        sorted(f"{document.id}:{document.current_parse_run}" for document in documents)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _candidates_for(
    session: AsyncSession, document: UploadedDocument
) -> list[FactCandidate]:
    chunks = list(
        (
            await session.execute(
                sa.select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.parse_run == document.current_parse_run,
                )
                .order_by(DocumentChunk.ordinal)
            )
        )
        .scalars()
        .all()
    )

    candidates: list[FactCandidate] = []
    for position, chunk in enumerate(chunks):
        # A label can end one chunk with its report opening the next, so each chunk is
        # given a look at the one after it.
        following = chunks[position + 1] if position + 1 < len(chunks) else None
        candidates.extend(
            extract_from_text(
                chunk.text,
                chunk_id=chunk.id,
                document_id=document.id,
                following_text=following.text if following else None,
                following_chunk_id=following.id if following else None,
            )
        )
    return candidates


def _to_rows(case: Case, fact: ResolvedFact, signature: str) -> list[ExtractedFact]:
    """One row per distinct reading, so every conflicting value keeps its source."""
    spec = FIELDS_BY_KEY.get(fact.key)
    unit = spec.unit if spec else None

    if fact.status is FactStatus.missing:
        return [
            ExtractedFact(
                case_id=case.id,
                organization_id=case.organization_id,
                fact_type=fact.key,
                value=None,
                unit=unit,
                status=FactStatus.missing,
                origin=FactOrigin.machine,
                extraction_signature=signature,
            )
        ]

    note = None
    if fact.conflicting_values:
        note = "Valeurs divergentes dans le dossier : " + " / ".join(fact.conflicting_values)

    readings = fact.readings or ()
    return [
        ExtractedFact(
            case_id=case.id,
            organization_id=case.organization_id,
            fact_type=fact.key,
            value=reading.value,
            unit=unit,
            observed_date=_observed_date(fact.key, reading.value),
            status=fact.status,
            origin=FactOrigin.machine,
            source_document_id=reading.document_id,
            source_chunk_id=reading.chunk_id,
            source_excerpt=reading.excerpt,
            note=note,
            extraction_signature=signature,
        )
        for reading in readings
    ]


def _observed_date(key: str, value: str | None) -> datetime | None:
    """Read a date out of a date-valued field, e.g. `19/05/2025`."""
    if key not in DATE_FIELDS or not value:
        return None

    for pattern, order in DATE_PATTERNS:
        match = pattern.search(value)
        if match is None:
            continue
        parts = dict(zip(order, match.groups(), strict=True))
        try:
            return datetime(
                int(parts["year"]), int(parts["month"]), int(parts["day"]), tzinfo=UTC
            )
        except ValueError:
            return None
    return None


async def _already_extracted(
    session: AsyncSession, case_id: uuid.UUID, signature: str
) -> bool:
    existing = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ExtractedFact)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.extraction_signature == signature,
        )
    )
    return bool(existing)


async def latest_machine_facts(
    session: AsyncSession, *, case_id: uuid.UUID
) -> list[ExtractedFact]:
    """Every fact from the most recent machine reading of this case.

    A conflicting field contributes several rows — one per value read — because that is
    what the disagreement consists of.
    """
    newest_signature = (
        sa.select(ExtractedFact.extraction_signature)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.extraction_signature.is_not(None),
        )
        .order_by(ExtractedFact.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )

    result = await session.execute(
        sa.select(ExtractedFact)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.extraction_signature == newest_signature,
        )
        .order_by(ExtractedFact.fact_type, ExtractedFact.created_at)
    )
    return list(result.scalars().all())
