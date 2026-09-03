"""Case ledger endpoints."""

import uuid
from dataclasses import asdict
from datetime import datetime

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, field_validator

from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import completeness
from rcpia.cases import ledger as ledger_service
from rcpia.cases import service as case_service
from rcpia.core.models import UserRole

router = protected_router(
    UserRole.admin, UserRole.uploader, prefix="/cases/{case_id}/ledger", tags=["ledger"]
)


class LedgerReadingRead(BaseModel):
    """One value read for a field, with where it was written."""

    value: str | None
    source_excerpt: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_chunk_ids: list[str] = []
    fact_id: str | None = None


class LedgerEntryRead(BaseModel):
    key: str
    label: str
    category: str
    value: str | None
    status: str
    origin: str
    highlight: str
    unit: str | None = None
    note: str | None = None
    source_excerpt: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_chunk_ids: list[str] = []
    fact_id: str | None = None
    corrected: bool = False
    # Several when the dossier contradicts itself; the disagreement is the information.
    readings: list[LedgerReadingRead] = []


class CompletenessIssueRead(BaseModel):
    key: str
    label: str
    severity: str
    highlight: str
    message: str


class LedgerRead(BaseModel):
    case_id: uuid.UUID
    version: int | None
    entries: list[LedgerEntryRead]
    missing_fields: list[str]
    uncertain_fields: list[str]
    issues: list[CompletenessIssueRead] = []
    can_run_analysis: bool = True
    updated_at: datetime | None = None


class CorrectionRequest(BaseModel):
    fact_type: str = Field(min_length=1, max_length=100)
    # None clears a value: a clinician may state that a field is genuinely unknown.
    value: str | None = Field(default=None, max_length=4000)
    # Required by the schema, and by good sense: a correction without a reason is
    # unreviewable.
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("value", "note")
    @classmethod
    def strip_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None


@router.get("", response_model=LedgerRead)
async def read_ledger(case_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> LedgerRead:
    """The live ledger, plus the version number of the last stored snapshot."""
    case = await _load_case(session, user, case_id)
    entries = await ledger_service.build_entries(session, case=case)
    version = await ledger_service.latest_version(session, case_id=case.id)
    report = completeness.assess(entries, cancer_type=case.cancer_type)

    return LedgerRead(
        case_id=case.id,
        version=version.version if version else None,
        entries=[LedgerEntryRead(**asdict(entry)) for entry in entries],
        missing_fields=ledger_service.missing_keys(entries),
        uncertain_fields=ledger_service.uncertain_keys(entries),
        issues=[CompletenessIssueRead(**asdict(issue)) for issue in report.issues],
        can_run_analysis=report.can_run_analysis,
        updated_at=version.created_at if version else None,
    )


@router.post("/corrections", response_model=LedgerRead, status_code=status.HTTP_201_CREATED)
async def correct_field(
    case_id: uuid.UUID, payload: CorrectionRequest, session: SessionDep, user: CurrentUser
) -> LedgerRead:
    """Correct one field, then snapshot the ledger as a new version."""
    case = await _load_case(session, user, case_id)

    try:
        await ledger_service.apply_correction(
            session,
            case=case,
            user=user,
            fact_type=payload.fact_type,
            value=payload.value,
            note=payload.note or "",
        )
    except ledger_service.UnknownField as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Champ inconnu : {payload.fact_type}"
        ) from exc
    except ledger_service.FieldNotInScope as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Le champ {payload.fact_type} ne s'applique pas à ce type de cancer.",
        ) from exc

    version = await ledger_service.record_version(
        session, case=case, change_reason=f"Correction : {payload.fact_type}", user=user
    )
    await session.commit()

    entries = await ledger_service.build_entries(session, case=case)
    report = completeness.assess(entries, cancer_type=case.cancer_type)
    return LedgerRead(
        case_id=case.id,
        version=version.version,
        entries=[LedgerEntryRead(**asdict(entry)) for entry in entries],
        missing_fields=ledger_service.missing_keys(entries),
        uncertain_fields=ledger_service.uncertain_keys(entries),
        issues=[CompletenessIssueRead(**asdict(issue)) for issue in report.issues],
        can_run_analysis=report.can_run_analysis,
        updated_at=version.created_at,
    )


async def _load_case(session: SessionDep, user: CurrentUser, case_id: uuid.UUID):
    try:
        return await case_service.get_case(session, user=user, case_id=case_id)
    except case_service.CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dossier introuvable.") from exc
