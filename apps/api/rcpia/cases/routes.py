"""Case and dossier endpoints.

Built with `protected_router`, so every route here requires an authenticated uploader or
administrator — including any route added later.
"""

import uuid
from typing import Annotated

from fastapi import Depends, File, HTTPException, Response, UploadFile, status

from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import service
from rcpia.cases.models import CaseCreate, CaseDetail, CaseRead, DocumentRead, JobActivity
from rcpia.cases.uploads import MAX_UPLOAD_BYTES, UploadRejected
from rcpia.core.models import UserRole
from rcpia.jobs.models import STEPS_BY_JOB_TYPE
from rcpia.storage.base import BlobStore, StorageError, checksum
from rcpia.storage.factory import get_blob_store

router = protected_router(UserRole.admin, UserRole.uploader, prefix="/cases", tags=["cases"])

Blobs = Annotated[BlobStore, Depends(get_blob_store)]


@router.post("", response_model=CaseRead, status_code=status.HTTP_201_CREATED)
async def create_case(payload: CaseCreate, session: SessionDep, user: CurrentUser) -> CaseRead:
    try:
        case = await service.create_case(
            session,
            user=user,
            reference=payload.reference,
            cancer_type=payload.cancer_type,
            clinical_question=payload.clinical_question,
        )
    except service.DuplicateReference as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Un dossier portant la référence {payload.reference!r} existe déjà.",
        ) from exc

    await session.commit()
    return CaseRead.model_validate(case)


@router.get("", response_model=list[CaseRead])
async def list_cases(session: SessionDep, user: CurrentUser) -> list[CaseRead]:
    cases = await service.list_cases(session, user=user)
    return [CaseRead.model_validate(case) for case in cases]


@router.get("/{case_id}", response_model=CaseDetail)
async def read_case(case_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> CaseDetail:
    case = await _load_case(session, user, case_id)
    documents = await service.list_documents(session, case=case)
    return CaseDetail(
        **CaseRead.model_validate(case).model_dump(),
        documents=[DocumentRead.model_validate(document) for document in documents],
    )


@router.get("/{case_id}/activity", response_model=list[JobActivity])
async def read_activity(
    case_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[JobActivity]:
    """What is still running on this case, and which step each piece is on.

    A separate route rather than a field on `CaseDetail`: this is what a progress display
    polls, several times a minute, and it has no use for the document list it would drag
    along each time.
    """
    case = await _load_case(session, user, case_id)
    jobs = await service.list_activity(session, case=case)
    return [
        JobActivity(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            # One named key, never the payload: it also carries the clinical question.
            agent_role=_text_or_none(job.payload.get("agent_role")),
            milestone=job.milestone,
            milestone_at=job.milestone_at,
            steps=list(STEPS_BY_JOB_TYPE.get(job.job_type, ())),
            started_at=job.started_at,
            created_at=job.created_at,
        )
        for job in jobs
    ]


@router.post(
    "/{case_id}/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    case_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    blobs: Blobs,
    file: Annotated[UploadFile, File()],
) -> DocumentRead:
    """Upload a `.docx` or `.pdf` dossier and queue it for parsing."""
    case = await _load_case(session, user, case_id)

    content = await _read_within_limit(file)
    try:
        document = await service.add_document(
            session,
            blobs,
            user=user,
            case=case,
            filename=file.filename or "dossier",
            content=content,
        )
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Stockage indisponible, réessayez."
        ) from exc

    # add_document owns the commit, so that a failure can delete the blob it wrote.
    return DocumentRead.model_validate(document)


@router.get("/{case_id}/documents/{document_id}/content")
async def download_document(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    blobs: Blobs,
) -> Response:
    """Return the original dossier exactly as uploaded.

    The uploaded file stays the primary source, so it must remain retrievable unchanged
    for anyone reviewing what the system read.
    """
    case = await _load_case(session, user, case_id)
    try:
        document = await service.get_document(session, case=case, document_id=document_id)
        content = await blobs.get_bytes(document.blob_key)
    except service.CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document introuvable.") from exc
    except StorageError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contenu introuvable.") from exc

    # The dossier is evidence: hand back only bytes that still match what was uploaded.
    if checksum(content) != document.checksum_sha256:
        await service.record_integrity_failure(session, document=document, actor=user)
        await session.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Le document stocké ne correspond plus à son empreinte enregistrée.",
        )

    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{document.id}"',
            "Cache-Control": "no-store",
        },
    )


def _text_or_none(value: object) -> str | None:
    """A payload is JSON a caller once supplied, so a key is not a promise of a string."""
    return value if isinstance(value, str) else None


async def _load_case(session: SessionDep, user: CurrentUser, case_id: uuid.UUID):
    try:
        return await service.get_case(session, user=user, case_id=case_id)
    except service.CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dossier introuvable.") from exc


async def _read_within_limit(file: UploadFile) -> bytes:
    """Read the upload, refusing anything over the limit without buffering it all first."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Le fichier dépasse {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo.",
            )
        chunks.append(chunk)
    return b"".join(chunks)
