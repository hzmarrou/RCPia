"""Case and dossier intake.

Every operation is scoped to the caller's organization: the case id alone is never
enough to reach a case, which is the same rule the schema's composite keys enforce.

Uploading a dossier writes three things in one transaction — the blob metadata row, the
audit event, and the parsing job — so a document can never exist without a job to parse
it, nor a job without its document.
"""

import contextlib
import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases.uploads import detect_dossier_type
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    Case,
    CaseStatus,
    ParseStatus,
    UploadedDocument,
    User,
)
from rcpia.jobs import service as jobs
from rcpia.jobs.models import LIVE_JOB_STATUSES, JobType
from rcpia.storage import keys
from rcpia.storage.base import BlobInfo, BlobStore, StorageError


class CaseError(Exception):
    """Base class for case-level failures."""


class DuplicateReference(CaseError):
    """Another case in this organization already uses that reference."""


class CaseNotFound(CaseError):
    """No such case in the caller's organization."""


async def create_case(
    session: AsyncSession,
    *,
    user: User,
    reference: str,
    cancer_type: str,
    clinical_question: str | None,
) -> Case:
    case = Case(
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        reference=reference,
        cancer_type=cancer_type,
        clinical_question=clinical_question,
        status=CaseStatus.draft,
    )
    session.add(case)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateReference(reference) from exc

    session.add(
        AuditEvent(
            organization_id=user.organization_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type="case.created",
            entity_type="cases",
            entity_id=case.id,
            payload={"reference": reference, "cancer_type": str(cancer_type)},
        )
    )
    await session.flush()
    return case


async def list_cases(session: AsyncSession, *, user: User) -> list[Case]:
    result = await session.execute(
        sa.select(Case)
        .where(Case.organization_id == user.organization_id)
        .order_by(Case.created_at.desc())
    )
    return list(result.scalars().all())


async def get_case(session: AsyncSession, *, user: User, case_id: uuid.UUID) -> Case:
    case = await session.scalar(
        sa.select(Case).where(
            Case.id == case_id, Case.organization_id == user.organization_id
        )
    )
    if case is None:
        # Deliberately indistinguishable from "belongs to another organization": the
        # caller learns nothing about cases they may not see.
        raise CaseNotFound(str(case_id))
    return case


async def list_documents(
    session: AsyncSession, *, case: Case
) -> list[UploadedDocument]:
    result = await session.execute(
        sa.select(UploadedDocument)
        .where(UploadedDocument.case_id == case.id)
        .order_by(UploadedDocument.created_at)
    )
    return list(result.scalars().all())


async def list_activity(session: AsyncSession, *, case: Case) -> list[AnalysisJob]:
    """The unfinished work on this case, oldest first.

    Scoped by organization like every other read here: the composite condition is what
    stops a case id from another organization returning that organization's queue.

    Ordered by creation so a display keeps a stable row order while jobs come and go —
    ordering by `milestone_at` would make rows jump around as each one reported.

    **And by id after it, because creation time alone is all ties here.** `created_at`
    defaults to `now()`, which is PostgreSQL's *transaction* timestamp, and a panel
    enqueues all of its roles in one transaction — so eight agent jobs carry one identical
    timestamp. With every row tied, the order is whatever the plan yields, and each
    milestone write rewrites the tuple and moves it: the rows would reshuffle between
    polls, which is exactly what ordering by creation was meant to prevent.
    """
    result = await session.execute(
        sa.select(AnalysisJob)
        .where(
            AnalysisJob.case_id == case.id,
            AnalysisJob.organization_id == case.organization_id,
            AnalysisJob.status.in_(LIVE_JOB_STATUSES),
        )
        .order_by(AnalysisJob.created_at, AnalysisJob.id)
    )
    return list(result.scalars().all())


async def add_document(
    session: AsyncSession,
    blobs: BlobStore,
    *,
    user: User,
    case: Case,
    filename: str,
    content: bytes,
) -> UploadedDocument:
    """Store an uploaded dossier and queue it for parsing, committing all of it together.

    The blob has to be written before the row that references it, which leaves a window:
    if the transaction then fails, the blob would linger with nothing pointing at it —
    unaudited clinical data. So this function owns the commit and deletes the blob it
    wrote if the commit does not happen.
    """
    detected = detect_dossier_type(filename, content)

    document_id = uuid.uuid4()
    key = keys.dossier_key(case.id, document_id, filename)
    stored = await blobs.put_bytes(key, content, detected.content_type)

    try:
        document = await _record_document(
            session, user=user, case=case, document_id=document_id, filename=filename, stored=stored
        )
        await session.commit()
    except Exception:
        await session.rollback()
        # Compensate: the row that would have justified this blob does not exist.
        with contextlib.suppress(StorageError):
            await blobs.delete(stored.key)
        raise

    return document


async def _record_document(
    session: AsyncSession,
    *,
    user: User,
    case: Case,
    document_id: uuid.UUID,
    filename: str,
    stored: BlobInfo,
) -> UploadedDocument:
    document = UploadedDocument(
        id=document_id,
        case_id=case.id,
        organization_id=case.organization_id,
        uploaded_by_user_id=user.id,
        blob_key=stored.key,
        original_filename=filename[:400],
        content_type=stored.content_type,
        checksum_sha256=stored.checksum_sha256,
        size_bytes=stored.size_bytes,
        parse_status=ParseStatus.pending,
    )
    session.add(document)

    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type="document.uploaded",
            entity_type="uploaded_documents",
            entity_id=document_id,
            payload={
                "original_filename": filename,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
                "checksum_sha256": stored.checksum_sha256,
            },
        )
    )

    await jobs.enqueue(
        session,
        case_id=case.id,
        organization_id=case.organization_id,
        job_type=JobType.PARSE_DOSSIER,
        payload={"document_id": str(document_id)},
    )

    case.status = CaseStatus.parsing
    await session.flush()
    return document


async def record_integrity_failure(
    session: AsyncSession, *, document: UploadedDocument, actor: User | None = None
) -> None:
    """Audit a stored document whose bytes no longer match their checksum.

    Silent corruption of a dossier would undermine every fact extracted from it, so it
    is recorded as an event rather than only reported to whoever hit it.
    """
    session.add(
        AuditEvent(
            organization_id=document.organization_id,
            case_id=document.case_id,
            actor_user_id=actor.id if actor else None,
            event_type="document.integrity_failed",
            entity_type="uploaded_documents",
            entity_id=document.id,
            payload={
                "blob_key": document.blob_key,
                "expected_checksum": document.checksum_sha256,
            },
        )
    )
    await session.flush()


async def get_document(
    session: AsyncSession, *, case: Case, document_id: uuid.UUID
) -> UploadedDocument:
    document = await session.scalar(
        sa.select(UploadedDocument).where(
            UploadedDocument.id == document_id, UploadedDocument.case_id == case.id
        )
    )
    if document is None:
        raise CaseNotFound(str(document_id))
    return document
