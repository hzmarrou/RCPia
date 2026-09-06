"""Case status describes panels over the current ledger, never an older fiche."""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    AnalysisPanel,
    AnalysisPanelDispatch,
    Case,
    CaseLedgerVersion,
    CaseStatus,
)
from rcpia.jobs.models import LIVE_JOB_STATUSES


async def refresh_analysis_status(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    finishing_job_id: uuid.UUID | None = None,
    skip_locked: bool = False,
) -> CaseStatus | None:
    """Reconcile recorded work while serialising with convening and ledger corrections.

    The handler can name its terminal job before the queue writes its outcome. Both writes
    commit together, so losing the claim rolls the status back along with the agent run.
    Housekeeping uses committed job states instead, including failures caused by a crash.
    """
    case = await session.scalar(
        sa.select(Case)
        .where(Case.id == case_id)
        # Agent-run inserts hold foreign-key KEY SHARE locks on the case. NO KEY UPDATE
        # serialises status writers without deadlocking those concurrent inserts.
        .with_for_update(key_share=True, skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    if case is None:
        if skip_locked:
            return None
        raise ValueError(f"Cannot reconcile missing case {case_id}")
    if case.status in (CaseStatus.draft, CaseStatus.parsing):
        return case.status

    version = await session.scalar(
        sa.select(sa.func.max(CaseLedgerVersion.version)).where(
            CaseLedgerVersion.case_id == case_id
        )
    )
    rows = (
        await session.execute(
            sa.select(AnalysisPanel.id, AnalysisJob.id, AnalysisJob.status)
            .outerjoin(
                AnalysisPanelDispatch,
                sa.and_(
                    AnalysisPanelDispatch.panel_id == AnalysisPanel.id,
                    AnalysisPanelDispatch.dispatch_kind == AnalysisPanelDispatch.SYNTHESIS,
                ),
            )
            .outerjoin(AnalysisJob, AnalysisJob.id == AnalysisPanelDispatch.job_id)
            .where(
                AnalysisPanel.case_id == case_id,
                AnalysisPanel.ledger_version == version,
            )
            .order_by(AnalysisPanel.created_at.desc(), AnalysisPanel.id.desc())
        )
    ).all()

    if not rows:
        status = CaseStatus.ready_for_review
    elif any(
        job_id is None or (job_status in LIVE_JOB_STATUSES and job_id != finishing_job_id)
        for _, job_id, job_status in rows
    ):
        status = CaseStatus.analyzing
    else:
        # An older panel finishing late must not override the outcome of the newest request.
        # A completed queue job alone is not proof that citation-attested output was recorded.
        latest_job_id = rows[0][1]
        answered = await session.scalar(
            sa.select(AgentRun.id)
            .where(
                AgentRun.job_id == latest_job_id,
                AgentRun.status == AgentRunStatus.succeeded,
            )
            .limit(1)
        )
        status = CaseStatus.completed if answered is not None else CaseStatus.blocked

    if case.status != status:
        case.status = status
        await session.flush()
    return status


async def reconcile_analysis_statuses(session: AsyncSession) -> None:
    """Repair historical and crash-completed panels without rewriting clinical records."""
    case_ids = await session.scalars(
        sa.select(Case.id)
        .where(
            Case.status.not_in((CaseStatus.draft, CaseStatus.parsing)),
            Case.id.in_(sa.select(AnalysisPanel.case_id)),
        )
        .order_by(Case.id)
    )
    for case_id in case_ids:
        await refresh_analysis_status(session, case_id=case_id, skip_locked=True)
