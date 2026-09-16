"""Agent analysis endpoints.

Starting an analysis enqueues a job; it never runs an agent inside the request. A Pi run
takes minutes, and a request that blocked for minutes would time out somewhere between
the browser and the database while the model kept thinking.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError

from rcpia.agents import panel as panel_service
from rcpia.agents.bundle import entries_from_version
from rcpia.agents.pi_adapter import SAFE_AGENT_NAME
from rcpia.agents.pps_fiche import CURRENT_SYNTHESIS_CONTRACT
from rcpia.agents.schemas import SYNTHESIS_ROLE
from rcpia.agents.service import DEFAULT_AGENT_ROLE
from rcpia.auth.dependencies import CurrentUser, SessionDep, protected_router
from rcpia.cases import completeness
from rcpia.cases import ledger as ledger_service
from rcpia.cases import service as case_service
from rcpia.core.jsonio import load_json
from rcpia.core.models import AgentRun, AnalysisJob, AuditEvent, JobStatus, UserRole
from rcpia.corpus.vocabulary import AGENT_ROLES
from rcpia.jobs import service as job_service
from rcpia.jobs.models import LIVE_JOB_STATUSES, JobType
from rcpia.jobs.service import enqueue
from rcpia.query.decision import RETRIEVAL_CONTRACT, RetrievalQuery, decision_search, pin_query

router = protected_router(
    UserRole.admin, UserRole.uploader, prefix="/cases/{case_id}/analysis", tags=["analysis"]
)



class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # `agent_role` becomes a directory name, so it is constrained at the edge as well as
    # in the adapter. Two checks, because this value crosses a process boundary in a job
    # payload and the second reader cannot assume the first one ran.
    agent_role: str = Field(default=DEFAULT_AGENT_ROLE, pattern=SAFE_AGENT_NAME.pattern)
    clinical_question: str | None = Field(default=None, max_length=2000)
    retrieval_query: RetrievalQuery | None = None


def dedupe_key_for(case_id: uuid.UUID, agent_role: str) -> str:
    """Names the work: this agent, on this case. Enforced by a partial unique index."""
    return f"{JobType.RUN_AGENT}:{case_id}:{agent_role}"


class AnalysisJobRead(BaseModel):
    job_id: uuid.UUID
    status: str
    agent_role: str
    created_at: datetime


class PanelRequest(BaseModel):
    """Which specialists to convene, and on what question.

    `rcp_synthesis` is refused rather than filtered out. It is not a specialist -- it reads the
    others and cannot run beside them -- and a request naming it has misunderstood what a panel
    is, which is worth saying rather than silently correcting.
    """

    model_config = ConfigDict(extra="forbid")

    roles: list[str] = Field(min_length=1, max_length=12)
    clinical_question: str | None = Field(default=None, max_length=2000)
    retrieval_query: RetrievalQuery | None = None

    @model_validator(mode="after")
    def unique_targeted_roles(self):
        if self.retrieval_query is not None and len(self.roles) != len(set(self.roles)):
            raise ValueError("Duplicate targeted specialist roles.")
        return self


async def unique_request_json(request: Request) -> None:
    try:
        load_json(await request.body())
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid or duplicate JSON.",
        ) from exc


class PanelJobRead(BaseModel):
    agent_role: str
    job_id: uuid.UUID
    status: str
    # True when this panel is relying on a run that was already under way for an identical
    # question at the same ledger version, rather than having started one.
    reused: bool


class PanelRead(BaseModel):
    panel_id: uuid.UUID
    ledger_version: int
    jobs: list[PanelJobRead]


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


@router.post(
    "", response_model=AnalysisJobRead, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(unique_request_json)],
)
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
    snapshot_report = completeness.assess(
        entries_from_version(version), cancer_type=case.cancer_type,
    )
    if not snapshot_report.can_run_analysis:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            " ; ".join(issue.message for issue in snapshot_report.blocking),
        )
    try:
        retrieval_query = pin_query(
            payload.retrieval_query, cancer_type=str(case.cancer_type),
            case_question=case.clinical_question,
        )
        if retrieval_query is not None:
            if payload.agent_role not in AGENT_ROLES or payload.agent_role == SYNTHESIS_ROLE:
                raise ValueError("Targeted retrieval requires a known specialist.")
        if payload.agent_role != SYNTHESIS_ROLE:
            decision_search(
                cancer_type=str(case.cancer_type), agent_role=payload.agent_role,
                entries=entries_from_version(version), request=retrieval_query,
                operator_question=payload.clinical_question,
            )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    # Rollback expires ORM rows; compare a concurrent job to the original pinned version.
    requested_version = version.version

    def compatible(existing: AnalysisJob) -> bool:
        if (
            payload.agent_role == SYNTHESIS_ROLE
            and existing.payload.get("pps_proposal_contract") != CURRENT_SYNTHESIS_CONTRACT
        ):
            return False
        if existing.payload.get("retrieval_contract") != RETRIEVAL_CONTRACT:
            return False
        if retrieval_query is None and existing.payload.get("retrieval_query") is None:
            # Only a parked job is re-pinned below. A queued/running job still reads its
            # original snapshot, even if the live ledger now passes the gate.
            return (
                existing.status is JobStatus.waiting_for_user
                or existing.payload.get("ledger_version") == requested_version
            )
        return panel_service.job_is_compatible(
            existing, ledger_version=requested_version, question=payload.clinical_question,
            retrieval_query=retrieval_query,
        )

    # One live run per agent and case. The lookup below is a fast path only; the
    # guarantee is the partial unique index on `dedupe_key`, because between this SELECT
    # and the INSERT a concurrent request can do exactly the same thing. The
    # IntegrityError branch after the insert is what actually holds the line.
    dedupe_key = dedupe_key_for(case.id, payload.agent_role)
    existing = await _live_job(session, dedupe_key)
    if existing is not None:
        if not compatible(existing):
            raise HTTPException(status.HTTP_409_CONFLICT, "A different retrieval request is live.")
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
                        **({"retrieval_query": retrieval_query.model_dump(mode="json")}
                           if retrieval_query else {}),
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

    try:
        job = await enqueue(
            session,
            case_id=case.id,
            organization_id=case.organization_id,
            job_type=JobType.RUN_AGENT,
            payload={
                "agent_role": payload.agent_role,
                "clinical_question": payload.clinical_question,
                "retrieval_contract": RETRIEVAL_CONTRACT,
                **({"pps_proposal_contract": CURRENT_SYNTHESIS_CONTRACT}
                   if payload.agent_role == SYNTHESIS_ROLE else {}),
                # Pinned, so a correction made while the job waits does not silently
                # change the question the agent was asked.
                "ledger_version": version.version,
                **({"retrieval_query": retrieval_query.model_dump(mode="json")}
                   if retrieval_query else {}),
            },
            dedupe_key=dedupe_key,
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
    except IntegrityError:
        # A concurrent request won the race. Its job is the one live job, and returning
        # it is the same answer this request would have given had it arrived a moment
        # later.
        await session.rollback()
        concurrent = await _live_job(session, dedupe_key)
        if concurrent is None:  # pragma: no cover - it settled between the two queries
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Une analyse est déjà en cours pour ce dossier.",
            ) from None
        if not compatible(concurrent):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A different retrieval request is live.",
            ) from None
        return AnalysisJobRead(
            job_id=concurrent.id,
            status=str(concurrent.status),
            agent_role=payload.agent_role,
            created_at=concurrent.created_at,
        )

    # `created_at` is a server default, and an async session cannot load it lazily.
    await session.refresh(job)

    return AnalysisJobRead(
        job_id=job.id,
        status=str(job.status),
        agent_role=payload.agent_role,
        created_at=job.created_at,
    )


@router.post(
    "/panel", response_model=PanelRead, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(unique_request_json)],
)
async def start_panel(
    case_id: uuid.UUID,
    payload: PanelRequest,
    session: SessionDep,
    user: CurrentUser,
) -> PanelRead:
    """Convene a panel of specialists on one version of this case.

    Every job is enqueued in a single transaction and pinned to the same ledger version, so a
    correction landing mid-panel cannot leave half the specialists analysing one patient and
    half another. The synthesis is **not** enqueued here: it is dispatched once, by whichever
    worker finishes the last specialist, from `rcpia.agents.panel`.

    Concurrency is a matter of deployment, not of this request. These jobs are ordinary rows;
    running them at the same time means running more than one `rcpia-worker`.
    """
    case = await _load_case(session, user, case_id)

    roles = list(dict.fromkeys(payload.roles))
    unknown = [role for role in roles if role not in AGENT_ROLES]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Spécialités inconnues : {', '.join(sorted(unknown))}.",
        )
    if SYNTHESIS_ROLE in roles:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La synthèse RCP n'est pas une spécialité du panel : elle lit les avis rendus et "
            "est lancée automatiquement une fois toutes les spécialités terminées.",
        )

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
    snapshot_report = completeness.assess(
        entries_from_version(version), cancer_type=case.cancer_type,
    )
    if not snapshot_report.can_run_analysis:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            " ; ".join(issue.message for issue in snapshot_report.blocking),
        )

    try:
        started = await panel_service.start_panel(
            session,
            case_id=case.id,
            organization_id=case.organization_id,
            roles=roles,
            ledger_version=version.version,
            clinical_question=payload.clinical_question,
            requested_by_user_id=user.id,
            retrieval_query=payload.retrieval_query,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except panel_service.PanelConflict as conflict:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, conflict.detail) from conflict
    except IntegrityError as clash:
        # A concurrent panel-start enqueued the same role between the compatibility read and
        # the insert. Both requests wanted the same work; neither should create a second job.
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Une demande concurrente vient de lancer ces analyses ; relancez la requête pour "
            "obtenir les identifiants des travaux en cours.",
        ) from clash

    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type="agent.panel_requested",
            entity_type="analysis_panels",
            entity_id=started.panel.id,
            payload={
                "roles": roles,
                "ledger_version": version.version,
                "reused": sorted(started.reused),
            },
        )
    )
    await session.commit()

    for job in started.jobs.values():
        await session.refresh(job)

    return PanelRead(
        panel_id=started.panel.id,
        ledger_version=version.version,
        jobs=[
            PanelJobRead(
                agent_role=role,
                job_id=started.jobs[role].id,
                status=str(started.jobs[role].status),
                reused=role in started.reused,
            )
            for role in roles
        ],
    )


async def _live_job(session: SessionDep, dedupe_key: str) -> AnalysisJob | None:
    """The one job the partial unique index permits to be live for this key."""
    return await session.scalar(
        sa.select(AnalysisJob).where(
            AnalysisJob.dedupe_key == dedupe_key,
            AnalysisJob.status.in_(LIVE_JOB_STATUSES),
        )
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
