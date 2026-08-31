"""Running a panel of specialists concurrently, and deciding when the synthesis may start.

**What concurrency means here.** Every specialist job is an ordinary row in `analysis_jobs`,
and several `rcpia-worker` containers claim them at once through the queue's existing
`FOR UPDATE SKIP LOCKED`. Nothing in this module runs agents in parallel inside one process:
a worker still takes one job at a time, and concurrency comes from there being more than one
worker. That keeps the lease, the fencing token and the retry budget doing exactly what they
already did.

**What a panel adds** is the two things a set of independent jobs cannot express on its own:
which jobs were asked for together, and when they have all finished. Both are recorded rather
than inferred, because both decide what a tumour board is told.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.schemas import SYNTHESIS_ROLE
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    AnalysisPanel,
    AnalysisPanelDispatch,
    AnalysisPanelJob,
    JobStatus,
)
from rcpia.jobs.models import JobType
from rcpia.jobs.service import enqueue

# A job in one of these has settled: it will not produce an opinion later.
#
# `queued` is deliberately absent. A failed attempt with budget left goes *back* to `queued`
# with a backoff (`jobs.service.fail`), so a retrying specialist looks failed for a moment and
# is not. Dispatching the synthesis then would convene the board without an opinion that was
# still coming.
TERMINAL_JOB_STATUSES = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)

# Statuses in which a job is still going to produce an answer.
LIVE_JOB_STATUSES = (JobStatus.queued, JobStatus.running, JobStatus.waiting_for_user)


class PanelConflict(Exception):
    """A live job for one of the requested roles is answering a different question.

    Carries the roles so the caller can name them: "the analysis already running is reading
    version 3" is actionable, and "conflict" is not.
    """

    def __init__(self, roles: Sequence[str], detail: str) -> None:
        super().__init__(detail)
        self.roles = list(roles)
        self.detail = detail


def normalised_question(question: str | None) -> str:
    """The clinical question reduced to what makes two of them the same request.

    **Conservative on purpose.** Two questions are treated as the same only when they are
    identical once case and whitespace stop mattering. Nothing here can judge whether two
    differently worded questions mean the same thing, and guessing wrong reuses an opinion
    that answered something else — so anything not identical is treated as different, which
    costs a Pi run and never costs a wrong answer.
    """
    return " ".join((question or "").split()).casefold()


def job_is_compatible(job: AnalysisJob, *, ledger_version: int, question: str | None) -> bool:
    """Whether this live job is already answering the panel's question.

    The dedupe key names only the case and the role, which is the right identity for "do not
    run this agent twice at once" and the wrong one for "may this panel rely on that run". A
    correction between two panels creates a new ledger version, and an agent that read the old
    one did not analyse the patient the new panel is describing.
    """
    payload = job.payload or {}
    if payload.get("ledger_version") != ledger_version:
        return False
    return normalised_question(payload.get("clinical_question")) == normalised_question(question)


@dataclass(frozen=True)
class PanelStart:
    panel: AnalysisPanel
    jobs: dict[str, AnalysisJob]
    # Roles whose job was already live and compatible, so no second run was started.
    reused: frozenset[str]


async def live_jobs_for(
    session: AsyncSession, *, case_id: uuid.UUID, roles: Sequence[str]
) -> dict[str, AnalysisJob]:
    """The one live job the partial unique index permits per role, keyed by role."""
    rows = await session.execute(
        sa.select(AnalysisJob).where(
            AnalysisJob.case_id == case_id,
            AnalysisJob.job_type == JobType.RUN_AGENT,
            AnalysisJob.status.in_(LIVE_JOB_STATUSES),
        )
    )
    found: dict[str, AnalysisJob] = {}
    wanted = set(roles)
    for job in rows.scalars().all():
        role = (job.payload or {}).get("agent_role")
        if role in wanted:
            found[role] = job
    return found


async def start_panel(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
    roles: Sequence[str],
    ledger_version: int,
    clinical_question: str | None,
    requested_by_user_id: uuid.UUID | None = None,
) -> PanelStart:
    """Record the panel and enqueue its specialists, in one transaction.

    Every job is pinned to `ledger_version`, and a job that is already live for one of these
    roles is reused **only when it is reading that same version and question**. When it is not,
    the request is refused rather than served: the alternative is to re-pin a running agent,
    which cannot un-read what it has read, or to record a panel whose jobs analysed something
    else. Both produce a fiche that misstates what it is about.

    The caller commits. Nothing is enqueued unless the whole panel is.
    """
    if SYNTHESIS_ROLE in roles:
        raise ValueError(
            f"{SYNTHESIS_ROLE} is not a specialist: it reads the panel and cannot run beside it."
        )

    live = await live_jobs_for(session, case_id=case_id, roles=roles)
    incompatible = [
        role
        for role, job in live.items()
        if not job_is_compatible(job, ledger_version=ledger_version, question=clinical_question)
    ]
    if incompatible:
        versions = ", ".join(
            f"{role} (version {(live[role].payload or {}).get('ledger_version')})"
            for role in sorted(incompatible)
        )
        raise PanelConflict(
            sorted(incompatible),
            "Une analyse est déjà en cours pour ces spécialités sur une autre version du "
            f"tableau de données : {versions}. Le panel demandé porte sur la version "
            f"{ledger_version} ; attendez la fin de ces analyses avant de le relancer.",
        )

    panel = AnalysisPanel(
        case_id=case_id,
        organization_id=organization_id,
        ledger_version=ledger_version,
        requested_roles=list(roles),
        clinical_question=clinical_question,
        requested_by_user_id=requested_by_user_id,
    )
    session.add(panel)
    await session.flush()

    jobs: dict[str, AnalysisJob] = {}
    reused: set[str] = set()
    for role in roles:
        existing = live.get(role)
        if existing is not None:
            jobs[role] = existing
            reused.add(role)
        else:
            jobs[role] = await enqueue(
                session,
                case_id=case_id,
                organization_id=organization_id,
                job_type=JobType.RUN_AGENT,
                payload={
                    "agent_role": role,
                    "clinical_question": clinical_question,
                    "ledger_version": ledger_version,
                },
                dedupe_key=f"{JobType.RUN_AGENT}:{case_id}:{role}",
            )
        session.add(
            AnalysisPanelJob(panel_id=panel.id, agent_role=role, job_id=jobs[role].id)
        )

    await session.flush()
    return PanelStart(panel=panel, jobs=jobs, reused=frozenset(reused))


@dataclass(frozen=True)
class PanelState:
    panel_id: uuid.UUID
    # Roles whose linked job has settled, and roles still to come.
    terminal: frozenset[str]
    pending: frozenset[str]
    # Roles that settled without leaving a usable opinion. Never silently dropped.
    failed: frozenset[str]

    @property
    def complete(self) -> bool:
        return not self.pending


async def panel_state(
    session: AsyncSession, *, panel_id: uuid.UUID, assume_terminal: uuid.UUID | None = None
) -> PanelState:
    """How far this panel has got, judged only from the jobs it actually linked.

    `assume_terminal` names a job whose outcome is decided but not yet written — the one the
    calling worker is finishing. Without it the last specialist could never observe itself
    completing, and a panel would sit forever one job short of dispatching.
    """
    rows = await session.execute(
        sa.select(AnalysisPanelJob, AnalysisJob)
        .join(AnalysisJob, AnalysisJob.id == AnalysisPanelJob.job_id)
        .where(AnalysisPanelJob.panel_id == panel_id)
    )
    terminal: set[str] = set()
    pending: set[str] = set()
    settled_jobs: dict[str, uuid.UUID] = {}
    for membership, job in rows.all():
        is_terminal = job.status in TERMINAL_JOB_STATUSES or job.id == assume_terminal
        if is_terminal:
            terminal.add(membership.agent_role)
            settled_jobs[membership.agent_role] = job.id
        else:
            pending.add(membership.agent_role)

    # A role is failed when its job settled and left no successful run behind it. Read from
    # `agent_runs` rather than from the job's status, because a job can complete while the run
    # it performed did not produce a usable opinion.
    failed: set[str] = set()
    if settled_jobs:
        succeeded = set(
            (
                await session.execute(
                    sa.select(AgentRun.agent_role).where(
                        AgentRun.job_id.in_(settled_jobs.values()),
                        AgentRun.status == AgentRunStatus.succeeded,
                    )
                )
            )
            .scalars()
            .all()
        )
        failed = {role for role in settled_jobs if role not in succeeded}

    return PanelState(
        panel_id=panel_id,
        terminal=frozenset(terminal),
        pending=frozenset(pending),
        failed=frozenset(failed),
    )


async def panels_awaiting(session: AsyncSession, *, job_id: uuid.UUID) -> list[uuid.UUID]:
    """Panels that linked this job and have not dispatched their synthesis yet."""
    rows = await session.execute(
        sa.select(AnalysisPanelJob.panel_id)
        .outerjoin(
            AnalysisPanelDispatch,
            sa.and_(
                AnalysisPanelDispatch.panel_id == AnalysisPanelJob.panel_id,
                AnalysisPanelDispatch.dispatch_kind == AnalysisPanelDispatch.SYNTHESIS,
            ),
        )
        .where(AnalysisPanelJob.job_id == job_id, AnalysisPanelDispatch.id.is_(None))
    )
    return list(rows.scalars().all())


async def dispatch_synthesis_if_ready(
    session: AsyncSession, *, panel_id: uuid.UUID, finishing_job_id: uuid.UUID | None = None
) -> AnalysisJob | None:
    """Start the synthesis, if and only if every specialist this panel named has settled.

    **Two workers can reach this line at the same instant** — two specialists finishing
    together, each the other's last. The panel row is locked first, which serialises the two
    evaluations so the second sees the first's completion; without it both would see a pending
    sibling and no synthesis would ever be dispatched, which is the failure that loses a fiche
    rather than duplicating one.

    The lock orders the decision; the unique constraint on `(panel_id, dispatch_kind)` is what
    guarantees the outcome. Belt and braces, because they fail differently: the lock cannot
    help across a crash, and the constraint cannot stop the never-dispatched case.
    """
    panel = await session.scalar(
        sa.select(AnalysisPanel).where(AnalysisPanel.id == panel_id).with_for_update()
    )
    if panel is None:  # pragma: no cover - the row cannot vanish under its own membership
        return None

    state = await panel_state(
        session, panel_id=panel_id, assume_terminal=finishing_job_id
    )
    if not state.complete:
        return None

    # The synthesis is told which panel it is reconciling, so it reads exactly those opinions
    # and not whatever else the case has accumulated, and which of the requested specialities
    # produced nothing, so it cannot present a partial panel as a whole one.
    #
    # **Both inserts sit inside one savepoint**, because either can be the one that loses the
    # race and they fail at different constraints. Two panels over the same case reach the same
    # synthesis dedupe key, so the *job* insert is refused; one panel dispatched twice is
    # refused by `uq_analysis_panel_dispatches_panel_kind`. Enqueuing outside the savepoint
    # would let the first of those poison the transaction and discard the agent run this
    # worker had just recorded.
    savepoint = await session.begin_nested()
    try:
        job = await enqueue(
            session,
            case_id=panel.case_id,
            organization_id=panel.organization_id,
            job_type=JobType.RUN_AGENT,
            payload={
                "agent_role": SYNTHESIS_ROLE,
                "clinical_question": panel.clinical_question,
                "ledger_version": panel.ledger_version,
                "panel_id": str(panel_id),
                "requested_roles": list(panel.requested_roles or []),
                "missing_roles": sorted(state.failed),
            },
            dedupe_key=f"{JobType.RUN_AGENT}:{panel.case_id}:{SYNTHESIS_ROLE}",
        )
        session.add(
            AnalysisPanelDispatch(
                panel_id=panel_id,
                dispatch_kind=AnalysisPanelDispatch.SYNTHESIS,
                job_id=job.id,
            )
        )
        await savepoint.commit()
    except IntegrityError:
        # Someone else already dispatched a synthesis this panel is covered by. Theirs is the
        # one synthesis; rolling back to the savepoint keeps this worker's own writes.
        await savepoint.rollback()
        return None
    return job
