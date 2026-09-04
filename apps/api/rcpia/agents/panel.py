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

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.bundle import entries_from_version
from rcpia.agents.schemas import SYNTHESIS_ROLE
from rcpia.cases import completeness
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    AnalysisPanel,
    AnalysisPanelDispatch,
    AnalysisPanelJob,
    Case,
    CaseLedgerVersion,
    JobStatus,
)
from rcpia.jobs.models import LIVE_JOB_STATUSES, JobType
from rcpia.jobs.service import enqueue

logger = logging.getLogger(__name__)

# How old an extraction may be and still convene a panel by itself.
#
# One hour is comfortably longer than any healthy parse-and-extract, and far shorter than a
# backlog. It bounds the damage of the case this exists for: a worker fleet starting against a
# queue nobody has drained convenes nothing, and the history is replayed on purpose or not at
# all.
DEFAULT_MAX_EXTRACTION_AGE = timedelta(hours=1)

# A job in one of these has settled: it will not produce an opinion later.
#
# `queued` is deliberately absent. A failed attempt with budget left goes *back* to `queued`
# with a backoff (`jobs.service.fail`), so a retrying specialist looks failed for a moment and
# is not. Dispatching the synthesis then would convene the board without an opinion that was
# still coming.
TERMINAL_JOB_STATUSES = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)



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


async def convene_after_extraction(
    session: AsyncSession,
    *,
    case: Case,
    version: CaseLedgerVersion | None,
    roles: Sequence[str],
    job: AnalysisJob | None = None,
    max_age: timedelta | None = DEFAULT_MAX_EXTRACTION_AGE,
) -> PanelStart | None:
    """Start the panel for a case whose ledger version has just settled.

    **The step that was missing between an upload and an answer.** The chain ran parse, extract,
    record a version -- and stopped. Three worker replicas sat idle because nothing routed the
    case to them, so a dossier reached a panel only when somebody called the API by hand.

    Returns `None`, without enqueueing anything, when the panel should not run:

    - no roles are configured, which is how a deployment turns this off;
    - no ledger version exists yet, so there is nothing to pin the analysis to;
    - the case still has a blocking gap. The API route already refuses those at the door, and
      convening automatically has to apply the same gate -- otherwise an upload missing its
      histology quietly spends one Pi run per speciality producing one refusal per speciality.

    Repeat extraction is safe. A corrected field or a re-uploaded page records a new version and
    calls this again; `start_panel` reuses the live jobs when they are already answering the same
    question on the same version, and refuses when they are answering a different one.
    """
    if not roles or version is None:
        return None

    # **The guard that stops a first deploy spending hours.** Measured: the first genuine worker
    # ever started against the development database drained an extraction backlog that had been
    # accumulating since 17 August -- 111 jobs in about twenty seconds, each recording a version
    # and each correctly convening a panel, for 334 agent runs. Left alone those three workers
    # would have spent roughly five and a half hours of model time answering questions nobody
    # was waiting for.
    #
    # Nothing was wrong with convening; what was wrong was convening for work queued weeks ago.
    # A fresh extraction is a clinician waiting for an answer. A stale one is history, and
    # history is replayed deliberately or not at all.
    if job is not None and max_age is not None:
        age = datetime.now(UTC) - job.created_at
        if age > max_age:
            logger.info(
                "case %s: extraction job is %.0f h old, older than the %.0f h cutoff; "
                "not convening automatically",
                case.id,
                age.total_seconds() / 3600,
                max_age.total_seconds() / 3600,
            )
            return None

    entries = entries_from_version(version)
    report = completeness.assess(entries, cancer_type=case.cancer_type)
    if not report.can_run_analysis:
        logger.info(
            "case %s is not analysable yet; no panel convened (%s)",
            case.id,
            "; ".join(issue.message for issue in report.blocking)[:200],
        )
        return None

    try:
        started = await start_panel(
            session,
            case_id=case.id,
            organization_id=case.organization_id,
            roles=list(roles),
            ledger_version=version.version,
            clinical_question=case.clinical_question,
        )
    except PanelConflict as conflict:
        # A panel is already running on a different version of this case. Its answer is the one
        # in flight; starting a second would ask the same specialists two questions at once.
        logger.info("case %s already has a panel in flight: %s", case.id, conflict.detail)
        return None

    logger.info(
        "case %s convened %d specialists on ledger version %d",
        case.id,
        len(started.jobs),
        version.version,
    )
    return started


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


async def reconcile(session: AsyncSession) -> list[uuid.UUID]:
    """Dispatch every panel whose specialists have all settled and which has no synthesis yet.

    **The gate cannot live only in the agent handler.** That handler runs the fast path -- it
    knows the moment its own specialist finished -- but it is not the only way a job reaches a
    terminal state. An unexpected exception is caught by the worker, which records the failure
    itself and never calls back into the handler. A lease that expires with no attempts left is
    failed by `reap_expired_leases`. A worker killed mid-run leaves its job to be reclaimed and
    eventually exhausted. In each of those the specialist settles and the handler's gate never
    sees it, so the panel waits for a job that will never report again.

    This is the reconciliation the queue already applies to leases, applied to panels: cheap,
    idempotent, and independent of how a job came to be finished. It runs on the worker's poll,
    so a panel completed by a crash is dispatched within one poll interval rather than never.

    Returns the panels it dispatched, for logging.
    """
    live = (
        sa.select(AnalysisPanelJob.panel_id)
        .join(AnalysisJob, AnalysisJob.id == AnalysisPanelJob.job_id)
        .where(AnalysisJob.status.in_(LIVE_JOB_STATUSES))
    )
    dispatched_already = sa.select(AnalysisPanelDispatch.panel_id).where(
        AnalysisPanelDispatch.dispatch_kind == AnalysisPanelDispatch.SYNTHESIS
    )
    rows = await session.execute(
        sa.select(AnalysisPanel.id)
        .where(
            AnalysisPanel.id.not_in(live),
            AnalysisPanel.id.not_in(dispatched_already),
            # A panel that recorded no jobs at all is not "complete", it is malformed.
            AnalysisPanel.id.in_(sa.select(AnalysisPanelJob.panel_id)),
        )
        .order_by(AnalysisPanel.created_at)
    )

    dispatched: list[uuid.UUID] = []
    for panel_id in rows.scalars().all():
        if await dispatch_synthesis_if_ready(session, panel_id=panel_id) is not None:
            dispatched.append(panel_id)
    return dispatched


async def _synthesis_dedupe_key(
    session: AsyncSession, *, panel_id: uuid.UUID, case_id: uuid.UUID
) -> str:
    """Names the synthesis work: *reconciling these particular opinions*.

    **Not `case:rcp_synthesis`.** That key said "one synthesis per case", which is wrong in both
    directions once panels can share jobs. Two compatible panels reuse one set of specialists by
    design; under a case-wide key the second panel's enqueue was refused, its dispatch row was
    never written, and nothing would ever re-evaluate it -- its jobs were already terminal, so
    that panel waited forever. And two panels convened over *different* role sets are two
    different comparisons that both deserve an answer, which a case-wide key silently refused.

    Keying on the job set gets both right: identical sets collapse to one synthesis that both
    panels attach to, different sets each get their own. The fingerprint is short and stable --
    the same jobs in any order produce the same key.
    """
    rows = await session.execute(
        sa.select(AnalysisPanelJob.job_id).where(AnalysisPanelJob.panel_id == panel_id)
    )
    fingerprint = hashlib.sha256(
        ",".join(sorted(str(job_id) for job_id in rows.scalars().all())).encode()
    ).hexdigest()[:16]
    return f"{JobType.RUN_AGENT}:{case_id}:{SYNTHESIS_ROLE}:{fingerprint}"


async def _best_synthesis(session: AsyncSession, *, key: str) -> AnalysisJob | None:
    """The synthesis a panel should attach to among the jobs sharing this key.

    **Newest is not best.** A dedupe key can hold several jobs once none of them is live, because
    the unique index only forbids a live duplicate -- so a key can carry an older synthesis that
    succeeded beside a newer one that failed. Taking the newest would hand an arriving panel a
    failure while the answer to its own question sat one row away, and an environment that ran an
    earlier revision of this code is exactly where such a pair could come from.

    The order is what the panel actually needs: an answer, then something still coming, then
    nothing. Succeeded is read from `agent_runs` rather than from the job's status, because a job
    can complete while the run it performed produced no usable opinion -- the same distinction
    `panel_state` draws for specialists.
    """
    rows = await session.execute(
        sa.select(AnalysisJob)
        .where(AnalysisJob.dedupe_key == key)
        .order_by(AnalysisJob.created_at.desc())
    )
    candidates = list(rows.scalars().all())
    if not candidates:
        return None

    answered = set(
        (
            await session.execute(
                sa.select(AgentRun.job_id).where(
                    AgentRun.job_id.in_([job.id for job in candidates]),
                    AgentRun.status == AgentRunStatus.succeeded,
                )
            )
        )
        .scalars()
        .all()
    )
    for job in candidates:
        if job.id in answered:
            return job
    for job in candidates:
        if job.status in LIVE_JOB_STATUSES:
            return job
    return candidates[0]


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
    key = await _synthesis_dedupe_key(session, panel_id=panel_id, case_id=panel.case_id)

    # Any existing synthesis over the *same* opinions already answers this panel. Attaching to it
    # is the whole reason the key is the job set: two compatible panels share their specialists
    # by design, so reconciling those same opinions twice would spend a second Pi run to produce
    # the same document.
    #
    # **Status is deliberately not filtered.** Restricting this to live jobs looked right and was
    # not: the partial unique index only forbids a *live* duplicate, so once the shared synthesis
    # completed the lookup stopped matching and the index stopped objecting, and a panel
    # reconciled a moment later queued a second run over identical opinions. The second panel can
    # easily arrive after the first has finished — a reconciliation sweep on another worker is
    # exactly that path.
    #
    # A finished synthesis is a legitimate answer for this panel because the fingerprint is the
    # job set: identical jobs mean identical opinions, identical roles, and therefore the same
    # document. A *failed* one is attached to as well, and that is also right — it is the same
    # failure this panel would have had, and re-running it is a retry decision governed by the
    # job's own attempt budget, not something a second panel should trigger silently. The same
    # holds more strongly for a cancelled one: someone stopped that work, and a panel arriving
    # afterwards must not quietly restart it. Either way the panel is left visibly without a
    # synthesis rather than invisibly given a second one.
    existing = await _best_synthesis(session, key=key)

    # **The insert sits inside a savepoint**, because it can lose a race and this worker has
    # already recorded an agent run in this transaction. Letting the constraint fire outside
    # would roll that back along with the dispatch.
    savepoint = await session.begin_nested()
    try:
        job = existing
        if job is None:
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
                dedupe_key=key,
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
        # This panel was dispatched between the lock and the insert. Its dispatch row exists,
        # which is all this call had to guarantee; rolling back to the savepoint keeps this
        # worker's own writes.
        await savepoint.rollback()
        return None
    return job
