"""Parsing job handler.

Turns an uploaded dossier into `document_chunks`, which are the units extracted facts
cite. Runs in the worker, not in the request: LlamaParse takes minutes.

The handler is idempotent because job delivery is at-least-once — a worker can die after
parsing but before recording the outcome, and the job then runs again. Re-parsing a
document replaces its chunks rather than appending a second copy.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    Case,
    CaseStatus,
    DocumentChunk,
    JobStatus,
    ParseStatus,
    UploadedDocument,
)
from rcpia.jobs.models import JobOutcome
from rcpia.parsing.base import (
    DocumentParser,
    ParsedDocument,
    ParseRequest,
    ParserError,
    ParserNotConfigured,
)
from rcpia.parsing.quality import quality_warnings
from rcpia.storage.base import BlobStore, StorageError


async def parse_document(
    session: AsyncSession,
    job: AnalysisJob,
    *,
    parser: DocumentParser,
    blobs: BlobStore,
) -> JobOutcome | None:
    """Parse the document named in the job payload. Returns None on success."""
    document_id = uuid.UUID(str(job.payload["document_id"]))
    document = await session.get(UploadedDocument, document_id)
    if document is None:
        # The job outlived its document, which should be impossible while rows and jobs
        # are written together — and retrying cannot conjure it back.
        return JobOutcome(status=JobStatus.failed, error_message="Document introuvable")

    await _set_status(session, document, ParseStatus.parsing)

    try:
        content = await blobs.get_bytes(document.blob_key)
    except StorageError as exc:
        return await _record_failure(session, document, f"Document illisible : {exc}")

    request = ParseRequest(
        filename=document.original_filename,
        content=content,
        content_type=document.content_type,
        metadata={"case_id": str(document.case_id), "document_id": str(document.id)},
    )

    try:
        parsed = await parser.parse(request)
    except ParserNotConfigured as exc:
        # Configuration will not fix itself by retrying; stop and tell a human.
        return await _record_failure(session, document, str(exc), retryable=False)
    except ParserError as exc:
        raise RuntimeError(str(exc)) from exc  # retryable: the worker records and retries

    await _store_chunks(session, document, parsed)
    return None


async def _store_chunks(
    session: AsyncSession, document: UploadedDocument, parsed: ParsedDocument
) -> None:
    # Idempotency: a re-run replaces the previous parse of this document.
    await session.execute(
        sa.delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
    )

    for ordinal, block in enumerate(parsed.blocks):
        session.add(
            DocumentChunk(
                document_id=document.id,
                ordinal=ordinal,
                text=block.text,
                page=block.page,
                section=block.section,
                bbox=block.bbox,
                block_type=block.block_type,
            )
        )

    warnings = [*parsed.warnings, *quality_warnings(parsed)]
    document.parse_status = ParseStatus.parsed
    document.parser_provider = parsed.provider
    document.parser_provider_version = parsed.provider_version
    document.parse_warnings = warnings

    session.add(
        AuditEvent(
            organization_id=document.organization_id,
            case_id=document.case_id,
            actor_user_id=None,
            event_type="document.parsed",
            entity_type="uploaded_documents",
            entity_id=document.id,
            payload={
                "provider": parsed.provider,
                "provider_version": parsed.provider_version,
                "blocks": len(parsed.blocks),
                "characters": parsed.text_length,
                "warnings": warnings,
            },
        )
    )

    await _advance_case_if_ready(session, document.case_id)
    await session.flush()


async def _advance_case_if_ready(session: AsyncSession, case_id: uuid.UUID) -> None:
    """A case waits for every document before a human is asked to review the ledger."""
    outstanding = await session.scalar(
        sa.select(sa.func.count())
        .select_from(UploadedDocument)
        .where(
            UploadedDocument.case_id == case_id,
            UploadedDocument.parse_status.in_([ParseStatus.pending, ParseStatus.parsing]),
        )
    )
    if outstanding:
        return

    case = await session.get(Case, case_id)
    if case is not None and case.status is CaseStatus.parsing:
        case.status = CaseStatus.ready_for_review


async def _set_status(
    session: AsyncSession, document: UploadedDocument, status: ParseStatus
) -> None:
    document.parse_status = status
    await session.flush()


async def _record_failure(
    session: AsyncSession,
    document: UploadedDocument,
    message: str,
    *,
    retryable: bool = True,
) -> JobOutcome:
    """Mark the document failed and tell the caller whether retrying could help.

    A missing blob or an unreachable parser may succeed later; a missing API key will
    not, and retrying it three times only delays the message a human has to act on.
    """
    document.parse_status = ParseStatus.failed
    document.parse_warnings = [*document.parse_warnings, message]
    session.add(
        AuditEvent(
            organization_id=document.organization_id,
            case_id=document.case_id,
            event_type="document.parse_failed",
            entity_type="uploaded_documents",
            entity_id=document.id,
            payload={"error": message, "retryable": retryable},
        )
    )
    await session.flush()

    if retryable:
        raise RuntimeError(message)
    return JobOutcome(status=JobStatus.failed, error_message=message)
