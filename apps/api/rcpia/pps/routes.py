"""PPS endpoints.

Four shapes of the same document: JSON for the web app to lay out, Markdown for a plain-text
download, and two Word files -- the fiche a board reads, and the probatoire it consults only
when a claim is challenged. All four are assembled on request from the stored ledger version
and the stored agent runs, so none of them can drift from what the database holds, or from
one another.

Nothing is persisted. A PPS is a pure function of its inputs, and the run ids and ledger
version it was built from are printed in the document itself, so storing a copy would add
a second thing to keep in step without adding a guarantee. Phase 12 introduces the signed,
versioned package a clinician actually takes away; that one is stored, because at that
point it has been reviewed and the reviewed artifact is the record.
"""

import re
import uuid
from urllib.parse import quote

from fastapi import HTTPException, Response, status

from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import service as case_service
from rcpia.core.models import UserRole
from rcpia.pps import generator
from rcpia.pps.docx import render_fiche_docx, render_probatoire_docx, to_bytes
from rcpia.pps.render import render_markdown
from rcpia.pps.schemas import DocumentAssertsADecision, PpsDocument

router = protected_router(
    UserRole.admin, UserRole.uploader, prefix="/cases/{case_id}/pps", tags=["pps"]
)

MARKDOWN = "text/markdown; charset=utf-8"
WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Anything outside this set is replaced in the ASCII fallback filename. Deliberately a
# whitelist: a blacklist of the characters known to break a header today is a list that
# stops being complete the moment a new one matters.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _attachment(stem: str, reference: str, suffix: str) -> dict[str, str]:
    """A `Content-Disposition` header that a case reference cannot break out of.

    `reference` is supplied by whoever created the case -- a free `String(100)` column, not
    a generated identifier -- so it reaches this function as untrusted input, and it used to
    be interpolated into the header directly. Three ways that failed, all reproduced against
    the real `Response` before this fix:

    * `AZ-";foo="bar` closed the quoted string early and appended parameters of the
      caller's choosing to the header.
    * `AZ-01

X-Evil: yes` survived into the raw header bytes as a CR/LF sequence --
      response splitting, bounded only by whatever the server in front happens to reject.
    * `AZ-患者` raised `UnicodeEncodeError` while constructing the response, because HTTP
      header values are latin-1: a legitimate non-Latin reference turned every download of
      that case into a 500.

    So the header is built to RFC 6266 rather than by interpolation: a sanitised ASCII
    `filename` that every client understands, and `filename*=UTF-8''…` carrying the real
    reference percent-encoded, which is what a modern client actually uses. Percent-encoding
    is what makes the second form safe -- CR, LF and quotes leave `quote()` as `%0D`, `%0A`
    and `%22`, and cannot reconstitute themselves into header syntax.
    """
    raw = f"{stem}-{reference}{suffix}"

    # Sanitise the reference alone, then compose. Running the substitution over the whole
    # assembled name instead leaves the debris of a stripped reference stranded between the
    # stem and the extension -- a reference of `////` produced `Fiche-RCP--.docx`. `stem`
    # and `suffix` are our own constants and need no sanitising.
    safe_reference = _UNSAFE_IN_FILENAME.sub("-", reference).strip("-.")[:80]
    ascii_name = f"{stem}-{safe_reference}{suffix}" if safe_reference else f"{stem}{suffix}"

    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(raw, safe='')}"
        )
    }


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
        headers=_attachment("PPS-provisoire", document.case_reference, ".md"),
    )


@router.get("/docx")
async def download_pps_docx(
    case_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Response:
    """The fiche as a Word document: sections 1 to 6, and what is missing.

    Not the sources register or the reasoning annexes -- those routinely make up half or
    more of the rendered content, and a board reading this in the minutes it has per
    patient should not scroll past them to reach the six sections it discusses. They are
    at `/pps/docx/probatoire`, identified by the same case reference and ledger version.
    """
    document = await _build(session, user, case_id)
    body = to_bytes(render_fiche_docx(document))

    return Response(
        content=body,
        media_type=WORD,
        headers=_attachment("Fiche-RCP", document.case_reference, ".docx"),
    )


@router.get("/docx/probatoire")
async def download_pps_probatoire_docx(
    case_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Response:
    """The sources register and every specialist's full reasoning, as a Word document.

    Opened only when a claim in the fiche is challenged: the same case reference and
    ledger version identify it as the fiche's companion.
    """
    document = await _build(session, user, case_id)
    body = to_bytes(render_probatoire_docx(document))

    return Response(
        content=body,
        media_type=WORD,
        headers=_attachment("Dossier-probatoire", document.case_reference, ".docx"),
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
    except (generator.NothingToReport, generator.SynthesisRequired) as exc:
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
