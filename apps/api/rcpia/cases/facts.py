"""Storing extracted facts, and the job that produces them.

Facts are append-only, so this module never rewrites one. Re-running extraction after a
re-parse produces a new generation of facts citing the new chunks; the ledger reads the
newest generation, and the older ones remain as the record of what the machine said
before.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases.extraction import ResolvedFact, extract_from_text, resolve
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


async def extract_case_facts(session: AsyncSession, job: AnalysisJob) -> JobOutcome | None:
    """Extract facts for every parsed document of the job's case."""
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
                sa.select(UploadedDocument).where(
                    UploadedDocument.case_id == case.id,
                    UploadedDocument.parse_status == ParseStatus.parsed,
                )
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

    if await _already_extracted(session, case.id, documents):
        # At-least-once delivery: the work is done and facts are append-only, so
        # repeating it would duplicate the machine's reading of the dossier.
        return None

    created = 0
    for document in documents:
        created += await _extract_document(session, case=case, document=document)

    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            event_type="case.facts_extracted",
            entity_type="cases",
            entity_id=case.id,
            payload={"documents": len(documents), "facts": created},
        )
    )
    await session.flush()
    return None


async def _extract_document(
    session: AsyncSession, *, case: Case, document: UploadedDocument
) -> int:
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

    candidates = []
    for position, chunk in enumerate(chunks):
        # A label can end one chunk with its report opening the next, so each chunk is
        # given a look at the one after it.
        following = chunks[position + 1].text if position + 1 < len(chunks) else None
        candidates.extend(
            extract_from_text(
                chunk.text,
                chunk_id=chunk.id,
                document_id=document.id,
                following_text=following,
            )
        )

    resolved = resolve(candidates, cancer_type=case.cancer_type)
    for fact in resolved:
        session.add(_to_row(case, document, fact))

    await session.flush()
    return len(resolved)


def _to_row(case: Case, document: UploadedDocument, fact: ResolvedFact) -> ExtractedFact:
    note = None
    if fact.conflicting_values:
        note = "Valeurs divergentes dans le dossier : " + " / ".join(fact.conflicting_values)

    # A missing fact has no source by definition, and the schema's traceability
    # constraint permits exactly that case.
    if fact.status is FactStatus.missing:
        return ExtractedFact(
            case_id=case.id,
            organization_id=case.organization_id,
            fact_type=fact.key,
            value=None,
            status=FactStatus.missing,
            origin=FactOrigin.machine,
            note=note,
        )

    return ExtractedFact(
        case_id=case.id,
        organization_id=case.organization_id,
        fact_type=fact.key,
        value=fact.value,
        status=fact.status,
        origin=FactOrigin.machine,
        source_document_id=document.id,
        source_chunk_id=fact.chunk_id,
        source_excerpt=fact.excerpt,
        note=note,
    )


async def _already_extracted(
    session: AsyncSession, case_id: uuid.UUID, documents: list[UploadedDocument]
) -> bool:
    """True when machine facts already cite the current parse run of every document.

    Facts that cite an earlier run do not count: a re-parse means the dossier was read
    again, and the ledger should reflect the newer reading.
    """
    current_chunk_ids = sa.select(DocumentChunk.id).where(
        sa.or_(
            *[
                sa.and_(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.parse_run == document.current_parse_run,
                )
                for document in documents
            ]
        )
    )

    existing = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ExtractedFact)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.source_chunk_id.in_(current_chunk_ids),
        )
    )
    return bool(existing)


async def latest_machine_facts(
    session: AsyncSession, *, case_id: uuid.UUID
) -> list[ExtractedFact]:
    """The most recent machine reading of each field for this case."""
    newest = (
        sa.select(
            ExtractedFact.id,
            sa.func.row_number()
            .over(
                partition_by=ExtractedFact.fact_type,
                order_by=ExtractedFact.created_at.desc(),
            )
            .label("rank"),
        )
        .where(
            ExtractedFact.case_id == case_id, ExtractedFact.origin == FactOrigin.machine
        )
        .subquery()
    )

    result = await session.execute(
        sa.select(ExtractedFact)
        .join(newest, newest.c.id == ExtractedFact.id)
        .where(newest.c.rank == 1)
        .order_by(ExtractedFact.fact_type)
    )
    return list(result.scalars().all())
