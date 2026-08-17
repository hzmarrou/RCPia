"""Case and dossier intake.

Every operation is scoped to the caller's organization: the case id alone is never
enough to reach a case, which is the same rule the schema's composite keys enforce.

Uploading a dossier writes three things in one transaction — the blob metadata row, the
audit event, and the parsing job — so a document can never exist without a job to parse
it, nor a job without its document.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases.uploads import detect_dossier_type
from rcpia.core.models import (
    AuditEvent,
    Case,
    CaseStatus,
    ParseStatus,
    UploadedDocument,
    User,
)
from rcpia.jobs import service as jobs
from rcpia.jobs.models import JobType
from rcpia.storage import keys
from rcpia.storage.base import BlobStore


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


async def add_document(
    session: AsyncSession,
    blobs: BlobStore,
    *,
    user: User,
    case: Case,
    filename: str,
    content: bytes,
) -> UploadedDocument:
    """Store an uploaded dossier and queue it for parsing.

    The blob is written before the row, so a failure leaves an unreferenced blob rather
    than a row pointing at nothing: an orphan blob is inert, a dangling reference is a
    fault every later phase has to handle.
    """
    detected = detect_dossier_type(filename, content)

    document_id = uuid.uuid4()
    key = keys.dossier_key(case.id, document_id, filename)
    stored = await blobs.put_bytes(key, content, detected.content_type)

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
