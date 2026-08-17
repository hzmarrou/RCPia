"""Agent analysis endpoints.

Starting an analysis enqueues a job; it never runs an agent inside the request. A Pi run
takes minutes, and a request that blocked for minutes would time out somewhere between
the browser and the database while the model kept thinking.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from rcpia.agents.service import DEFAULT_AGENT_ROLE
from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import completeness
from rcpia.cases import ledger as ledger_service
from rcpia.cases import service as case_service
from rcpia.core.models import AgentRun, AnalysisJob, AuditEvent, JobStatus, UserRole
from rcpia.jobs import service as job_service
from rcpia.jobs.models import JobType
from rcpia.jobs.service import enqueue

router = protected_router(
    UserRole.admin, UserRole.uploader, prefix="/cases/{case_id}/analysis", tags=["analysis"]
)

# Statuses in which a job is still going to produce an answer.
LIVE_JOB_STATUSES = (JobStatus.queued, JobStatus.running, JobStatus.waiting_for_user)


class AnalysisRequest(BaseModel):
    agent_role: str = Field(default=DEFAULT_AGENT_ROLE, min_length=1, max_length=64)
    clinical_question: str | None = Field(default=None, max_length=2000)


class AnalysisJobRead(BaseModel):
    job_id: uuid.UUID
    status: str
    agent_role: str
    created_at: datetime


class AgentRunRead(BaseModel):
    id: uuid.UUID
    agent_role: str
    status: str
    ledger_version: int | None
    provider: str | None
    model: str | None
    output: dict | None
    error_message: str | None
    total_tokens: int
    duration_ms: int
    created_at: datetime


@router.post("", response_model=AnalysisJobRead, status_code=status.HTTP_202_ACCEPTED)
async def start_analysis(
    case_id: uuid.UUID,
    payload: AnalysisRequest,
    session: SessionDep,
    user: CurrentUser,
) -> AnalysisJobRead:
    """Queue an agent run for this case.

    Blocking gaps are refused here rather than being discovered by the worker: the
    clinician is at the screen now, and telling them which field is missing is more use
    than a job that fails a minute later.
    """
    case = await _load_case(session, user, case_id)

    entries = await ledger_service.build_entries(session, case=case)
    report = completeness.assess(entries, cancer_type=case.cancer_type)
    if not report.can_run_analysis:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            " ; ".join(issue.message for issue in report.blocking),
        )

    version = await ledger_service.latest_version(session, case_id=case.id)
    if version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Aucune version du tableau de données : l'extraction n'a pas encore abouti.",
        )

    # One live run per agent and case. Pressing the button twice must not buy two
    # opinions on the same question.
    existing = await session.scalar(
        sa.select(AnalysisJob)
        .where(
            AnalysisJob.case_id == case.id,
            AnalysisJob.organization_id == case.organization_id,
            AnalysisJob.job_type == JobType.RUN_AGENT,
            AnalysisJob.status.in_(LIVE_JOB_STATUSES),
            AnalysisJob.payload["agent_role"].astext == payload.agent_role,
        )
        .order_by(AnalysisJob.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        if existing.status is JobStatus.waiting_for_user:
            # The job was parked for a missing field, and the checks above have just
            # established the field is no longer missing. Nothing else would ever return
            # this job to the queue, so asking again has to — otherwise the clinician
            # fills in what was asked for, presses the button, and gets back a job that
            # never runs.
            #
            # Re-pinned to the current version: the correction that unblocked the case
            # created a new one, and analysing the version that was blocked would answer
            # the wrong question.
            existing.payload = {**existing.payload, "ledger_version": version.version}
            await job_service.resume(session, existing.id)
            session.add(
                AuditEvent(
                    organization_id=case.organization_id,
                    case_id=case.id,
                    actor_user_id=user.id,
                    event_type="agent.run_resumed",
                    entity_type="analysis_jobs",
                    entity_id=existing.id,
                    payload={
                        "agent_role": payload.agent_role,
                        "ledger_version": version.version,
                    },
                )
            )
        await session.commit()
        await session.refresh(existing)
        return AnalysisJobRead(
            job_id=existing.id,
            status=str(existing.status),
            agent_role=payload.agent_role,
            created_at=existing.created_at,
        )

    job = await enqueue(
        session,
        case_id=case.id,
        organization_id=case.organization_id,
        job_type=JobType.RUN_AGENT,
        payload={
            "agent_role": payload.agent_role,
            "clinical_question": payload.clinical_question,
            # Pinned, so a correction made while the job waits does not silently change
            # the question the agent was asked.
            "ledger_version": version.version,
        },
    )
    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type="agent.run_requested",
            entity_type="analysis_jobs",
            entity_id=job.id,
            payload={
                "agent_role": payload.agent_role,
                "ledger_version": version.version,
            },
        )
    )
    await session.commit()
    # `created_at` is a server default, and an async session cannot load it lazily.
    await session.refresh(job)

    return AnalysisJobRead(
        job_id=job.id,
        status=str(job.status),
        agent_role=payload.agent_role,
        created_at=job.created_at,
    )


@router.get("", response_model=list[AgentRunRead])
async def list_runs(
    case_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[AgentRunRead]:
    """Every attempt made on this case, newest first — failures included."""
    case = await _load_case(session, user, case_id)

    rows = await session.execute(
        sa.select(AgentRun)
        .where(
            AgentRun.case_id == case.id,
            AgentRun.organization_id == case.organization_id,
        )
        .order_by(AgentRun.created_at.desc())
    )
    return [
        AgentRunRead(
            id=run.id,
            agent_role=run.agent_role,
            status=str(run.status),
            ledger_version=run.ledger_version,
            provider=run.provider,
            model=run.model,
            output=run.output,
            error_message=run.error_message,
            total_tokens=run.total_tokens,
            duration_ms=run.duration_ms,
            created_at=run.created_at,
        )
        for run in rows.scalars().all()
    ]


async def _load_case(session: SessionDep, user: CurrentUser, case_id: uuid.UUID):
    try:
        return await case_service.get_case(session, user=user, case_id=case_id)
    except case_service.CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dossier introuvable.") from exc
