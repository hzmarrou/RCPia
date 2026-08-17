"""Parsing job handler.

Turns an uploaded dossier into `document_chunks`, which are the units extracted facts
cite. Runs in the worker, not in the request: LlamaParse takes minutes.

Three rules shape this handler:

- **The job's own scope decides what it may touch.** A document is looked up by its id
  *and* the job's case and organization, so a payload naming another organization's
  document finds nothing rather than parsing it.
- **Failures are reported, not raised.** Returning a `JobOutcome` keeps the writes that
  explain the failure — the worker applies the transition in the same transaction and
  commits both. Raising would roll the explanation back and leave the document looking
  untouched.
- **Chunks are never deleted.** Job delivery is at-least-once, so a re-parse happens; it
  writes a new parse run and points the document at it, because extracted facts cite
  chunks and that provenance must outlive a retry.
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
from rcpia.storage.base import BlobStore, StorageError, checksum


def _terminal(message: str) -> JobOutcome:
    return JobOutcome(status=JobStatus.failed, error_message=message, retryable=False)


def _retryable(message: str) -> JobOutcome:
    return JobOutcome(status=JobStatus.failed, error_message=message, retryable=True)


async def parse_document(
    session: AsyncSession,
    job: AnalysisJob,
    *,
    parser: DocumentParser,
    blobs: BlobStore,
) -> JobOutcome | None:
    """Parse the document named in the job payload. Returns None on success."""
    raw_id = str(job.payload.get("document_id", ""))
    try:
        document_id = uuid.UUID(raw_id)
    except ValueError:
        return _terminal(f"Payload invalide : document_id={raw_id!r}")

    # Scoped by the job's own case and organization: a payload naming someone else's
    # document must find nothing, not parse it.
    document = await session.scalar(
        sa.select(UploadedDocument).where(
            UploadedDocument.id == document_id,
            UploadedDocument.case_id == job.case_id,
            UploadedDocument.organization_id == job.organization_id,
        )
    )
    if document is None:
        return _terminal(
            "Document introuvable pour ce dossier : la tâche ne correspond à aucun document."
        )

    document.parse_status = ParseStatus.parsing
    await session.flush()

    try:
        content = await blobs.get_bytes(document.blob_key)
    except StorageError as exc:
        return await _record_failure(session, document, f"Document illisible : {exc}")

    # The checksum was recorded at upload; the bytes analysed must be those bytes.
    actual = checksum(content)
    if actual != document.checksum_sha256:
        return await _record_failure(
            session,
            document,
            "Le contenu stocké ne correspond pas à son empreinte enregistrée "
            f"(attendu {document.checksum_sha256[:12]}…, obtenu {actual[:12]}…).",
            retryable=False,
        )

    request = ParseRequest(
        filename=document.original_filename,
        content=content,
        content_type=document.content_type,
        metadata={"case_id": str(document.case_id), "document_id": str(document.id)},
    )

    try:
        parsed = await parser.parse(request)
    except ParserNotConfigured as exc:
        # Configuration will not fix itself by retrying.
        return await _record_failure(session, document, str(exc), retryable=False)
    except ParserError as exc:
        return await _record_failure(session, document, str(exc), retryable=True)

    await _store_chunks(session, document, parsed)
    return None


async def _store_chunks(
    session: AsyncSession, document: UploadedDocument, parsed: ParsedDocument
) -> None:
    """Write a new parse run and make it the current one.

    Earlier runs stay in place. They cost little, and once Phase 5 facts cite chunk ids
    through restrictive foreign keys, deleting them would make a retry impossible.
    """
    highest = await session.scalar(
        sa.select(sa.func.max(DocumentChunk.parse_run)).where(
            DocumentChunk.document_id == document.id
        )
    )
    parse_run = 0 if highest is None else int(highest) + 1

    for ordinal, block in enumerate(parsed.blocks):
        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_run=parse_run,
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
    document.current_parse_run = parse_run

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
                "parse_run": parse_run,
                "blocks": len(parsed.blocks),
                "characters": parsed.text_length,
                "warnings": warnings,
            },
        )
    )

    await _advance_case_if_ready(session, document.case_id)
    await session.flush()


async def all_documents_settled(session: AsyncSession, *, case_id: uuid.UUID) -> bool:
    """True when no document of this case is still waiting to be parsed."""
    outstanding = await session.scalar(
        sa.select(sa.func.count())
        .select_from(UploadedDocument)
        .where(
            UploadedDocument.case_id == case_id,
            UploadedDocument.parse_status.in_([ParseStatus.pending, ParseStatus.parsing]),
        )
    )
    return not outstanding


async def _advance_case_if_ready(session: AsyncSession, case_id: uuid.UUID) -> None:
    """Move the case on once every document is parsed.

    The case row is locked first. Two documents finishing at the same moment would
    otherwise each see the other as still outstanding, and the case would stay in
    `parsing` for ever with nothing left to move it.
    """
    case = await session.scalar(sa.select(Case).where(Case.id == case_id).with_for_update())
    if case is None or case.status is not CaseStatus.parsing:
        return

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

    case.status = CaseStatus.ready_for_review


async def _record_failure(
    session: AsyncSession,
    document: UploadedDocument,
    message: str,
    *,
    retryable: bool = True,
) -> JobOutcome:
    """Mark the document failed and report whether retrying could help.

    Both the document state and the job transition are written by the caller's
    transaction, so they land together or not at all.
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

    return _retryable(message) if retryable else _terminal(message)
