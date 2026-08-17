"""PPS endpoints.

Two shapes of the same document: JSON for the web app to lay out, and Markdown for
download. Both are assembled on request from the stored ledger version and the stored
agent runs, so neither can drift from what the database holds.

Nothing is persisted. A PPS is a pure function of its inputs, and the run ids and ledger
version it was built from are printed in the document itself, so storing a copy would add
a second thing to keep in step without adding a guarantee. Phase 12 introduces the signed,
versioned package a clinician actually takes away; that one is stored, because at that
point it has been reviewed and the reviewed artifact is the record.
"""

import uuid

from fastapi import HTTPException, Response, status

from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import service as case_service
from rcpia.core.models import UserRole
from rcpia.pps import generator
from rcpia.pps.render import render_markdown
from rcpia.pps.schemas import DocumentAssertsADecision, PpsDocument

router = protected_router(
    UserRole.admin, UserRole.uploader, prefix="/cases/{case_id}/pps", tags=["pps"]
)

MARKDOWN = "text/markdown; charset=utf-8"


@router.get("", response_model=PpsDocument)
async def read_pps(
    case_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> PpsDocument:
    """The provisional PPS as structured data."""
    return await _build(session, user, case_id)


@router.get("/markdown")
async def download_pps(
    case_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Response:
    """The provisional PPS as a Markdown file."""
    document = await _build(session, user, case_id)
    body = render_markdown(document)

    return Response(
        content=body,
        media_type=MARKDOWN,
        headers={
            "Content-Disposition": (
                f'attachment; filename="PPS-provisoire-{document.case_reference}.md"'
            )
        },
    )


async def _build(
    session: SessionDep, user: CurrentUser, case_id: uuid.UUID
) -> PpsDocument:
    try:
        case = await case_service.get_case(session, user=user, case_id=case_id)
    except case_service.CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dossier introuvable.") from exc

    try:
        # The generator resolves its own ledger version — the one the lead agent read,
        # not the newest. Passing one in from here is how the two came apart.
        document = await generator.generate(session, case=case)
    except generator.NothingToReport as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except generator.PpsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    try:
        document.assert_provisional()
    except DocumentAssertsADecision as exc:  # pragma: no cover - unreachable by design
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Génération interrompue : le document produit se présentait comme validé.",
        ) from exc

    return document
