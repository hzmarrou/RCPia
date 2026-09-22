"""Creator-isolated source inspection and append-only clinician attestations."""

import uuid
from typing import Annotated

from fastapi import HTTPException, Query

from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import service, sources
from rcpia.cases.source_schemas import SourceDocumentRead, SourceIndex, SourceReviewRequest
from rcpia.core.models import Case, UserRole

router = protected_router(
    UserRole.admin, UserRole.uploader, prefix="/cases/{case_id}/sources", tags=["sources"],
)


async def _case(session: SessionDep, user: CurrentUser, case_id: uuid.UUID) -> Case:
    try:
        return await service.get_case(session, user=user, case_id=case_id)
    except service.CaseNotFound as exc:
        raise HTTPException(404, "Dossier introuvable.") from exc


async def _context(session: SessionDep, case: Case) -> sources.ReviewContext:
    try:
        return await sources.load_context(session, case=case)
    except sources.StaleSourceReview as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("", response_model=SourceIndex)
async def index(case_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> SourceIndex:
    case = await _case(session, user, case_id)
    return (await _context(session, case)).index


@router.post("/review", response_model=SourceIndex)
async def review(
    case_id: uuid.UUID, payload: SourceReviewRequest, session: SessionDep, user: CurrentUser,
) -> SourceIndex:
    case = await _case(session, user, case_id)
    try:
        return await sources.record_review(session, case=case, user=user, payload=payload)
    except sources.StaleSourceReview as exc:
        raise HTTPException(409, str(exc)) from exc
    except sources.InvalidSourceReview as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{document_id}", response_model=SourceDocumentRead)
async def document(
    case_id: uuid.UUID, document_id: uuid.UUID, session: SessionDep, user: CurrentUser,
    ledger_version: Annotated[int, Query(ge=1)],
    source_fingerprint: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
) -> SourceDocumentRead:
    case = await _case(session, user, case_id)
    context = await _context(session, case)
    try:
        sources.check_binding(context, ledger_version, source_fingerprint)
    except sources.StaleSourceReview as exc:
        raise HTTPException(409, str(exc)) from exc
    result = sources.read_document(context, document_id)
    if result is None:
        raise HTTPException(404, "Document introuvable.")
    return result
