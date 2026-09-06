"""Worker entrypoint.

Runs the job loop in its own process (the `rcpia-worker` container):

    uv run python -m rcpia.worker

Handlers are registered here so the set of work this deployment performs is visible in
one place.
"""

import asyncio
import logging
import os
import signal
import sys

from rcpia.agents import panel
from rcpia.agents.factory import get_pi_adapter
from rcpia.agents.pi_adapter import PiNotConfigured
from rcpia.agents.service import run_agent
from rcpia.cases.facts import extract_case_facts
from rcpia.cases.ledger import record_version
from rcpia.cases.lifecycle import reconcile_analysis_statuses, refresh_analysis_status
from rcpia.core.config import get_settings
from rcpia.core.db import get_session_factory
from rcpia.core.models import AnalysisJob, Case, JobStatus
from rcpia.evidence.embedding import EmbeddingError, build_embedder
from rcpia.evidence.rerank import build_reranker
from rcpia.jobs.milestones import MilestoneReporter, MilestoneWriter, no_milestones
from rcpia.jobs.models import JobOutcome, JobType
from rcpia.jobs.service import enqueue
from rcpia.jobs.worker import Worker
from rcpia.parsing.base import ParserNotConfigured
from rcpia.parsing.factory import get_parser
from rcpia.parsing.service import all_documents_settled, parse_document
from rcpia.storage.factory import get_blob_store

if sys.platform == "win32":
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)


def _settles(job: AnalysisJob, outcome: JobOutcome | None) -> bool:
    """Whether this attempt ends the job, rather than sending it back for another try.

    A retryable failure returns the job to `queued` with a backoff, so the speciality is still
    coming and the panel must keep waiting. Treating that as terminal would convene the board
    without an opinion that was minutes away.
    """
    if outcome is None or outcome.status is JobStatus.completed:
        return True
    if outcome.status is JobStatus.failed:
        # `jobs.service.fail` retries while the budget lasts unless the handler said not to.
        return not outcome.retryable or job.attempts >= job.max_attempts
    if outcome.status is JobStatus.cancelled:
        return True
    # `waiting_for_user` is a live job: something is still expected of it.
    return False


def _worker_job_types() -> list[str] | None:
    """Optional per-worker restriction, e.g. `WORKER_JOB_TYPES=run_agent`.

    Useful when agent runs and document parsing want different scaling: parsing is cheap and
    IO-bound, an agent run holds a Pi process for minutes and counts against the model
    provider's concurrency limit. Unset means this worker takes any job.
    """
    raw = os.environ.get("WORKER_JOB_TYPES", "").strip()
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_worker() -> Worker:
    """Build the worker, resolving its dependencies now rather than per job.

    A missing LLAMAPARSE_API_KEY is a deployment mistake. Discovered inside a handler it
    becomes a generic error on every job, one document at a time; discovered here the
    worker refuses to start and says why.
    """
    parser = get_parser()
    blobs = get_blob_store()
    adapter = get_pi_adapter()
    # **The retriever, which the deployed worker was running without.** `run_agent` accepts it
    # optionally and says so in the constraints when it is absent -- honest, but it meant every
    # agent in a deployed worker answered with no guideline passages at all, while every
    # measurement harness passed one and saw a grounded bundle. Built here with the parser and
    # the adapter, so a missing Azure configuration stops the worker at startup with a message
    # rather than quietly producing ungrounded clinical opinions.
    settings = get_settings()
    embedder = build_embedder(settings)
    reranker = build_reranker(settings)
    async def reconcile_panels(session) -> None:
        """Catch any panel whose last specialist settled without the handler's gate running."""
        for panel_id in await panel.reconcile(session):
            logger.info("panel %s reconciled; synthesis queued", panel_id)
        await reconcile_analysis_statuses(session)

    # Bound once and shared with the milestone reporters below, which need a session of
    # their own: they write while a handler still holds its transaction open.
    session_factory = get_session_factory()
    worker = Worker(
        session_factory,
        job_types=_worker_job_types(),
        housekeeping=reconcile_panels,
    )

    async def handle_parse(session, job: AnalysisJob) -> JobOutcome | None:
        outcome = await parse_document(session, job, parser=parser, blobs=blobs)
        if outcome is None and await all_documents_settled(session, case_id=job.case_id):
            # Extraction reads the whole case at once, so it waits until no document is
            # still being parsed. Starting earlier would build a ledger from half the
            # evidence and call it complete.
            await enqueue(
                session,
                case_id=job.case_id,
                organization_id=job.organization_id,
                job_type=JobType.EXTRACT_FACTS,
                payload={},
            )
        return outcome

    async def handle_extract(session, job: AnalysisJob) -> JobOutcome | None:
        # **The narrative pass, which the deployed worker was running without.** `adapter`
        # is optional so that an environment with no model runtime still produces a ledger,
        # and omitting it here made the second pass return empty on every dossier: fifteen
        # extractions recorded `narrative_wanted: 0`, which reads as "nothing needed
        # filling" and meant "nothing was asked". The same shape as the agents that ran with
        # no brain and the worker that retrieved no passages -- built, tested, and never
        # connected to the code that runs.
        outcome = await extract_case_facts(session, job, adapter=adapter)
        if outcome is None:
            case = await session.get(Case, job.case_id)
            if case is not None:
                version = await record_version(
                    session, case=case, change_reason="Extraction automatique"
                )
                # The step that closes the chain. Until now it ended here: parse, extract,
                # record a version, and stop -- with three worker replicas idle because nothing
                # routed the case to them. Convening refuses on its own when the case still has
                # a blocking gap, or when no roles are configured.
                await panel.convene_after_extraction(
                    session,
                    case=case,
                    version=version,
                    roles=get_settings().auto_panel_role_list,
                    # The job carries its own age, which is what the backlog guard reads.
                    job=job,
                )
        return outcome

    async def handle_agent(session, job: AnalysisJob) -> JobOutcome | None:
        # The claim token travels on the row the worker handed us, so the reporter is
        # fenced like every other write against this job: a worker that lost its lease
        # cannot report progress over the run that replaced it.
        milestones: MilestoneWriter = no_milestones
        if job.claim_token is not None:
            milestones = MilestoneReporter(session_factory, job.id, job.claim_token)

        outcome = await run_agent(
            session, job, adapter=adapter, blobs=blobs,
            embedder=embedder, reranker=reranker, milestones=milestones,
        )

        # A specialist that has just settled may have been the last one a panel was waiting
        # for. The check runs here, in the handler's own transaction, so the dispatch commits
        # with the run it followed from: a synthesis queued against a run that then rolled back
        # would read an opinion the database does not hold.
        #
        # This job's own row still says `running` -- the worker applies the outcome after the
        # handler returns -- so the gate is told to treat it as settled. Otherwise the last
        # specialist could never see itself finish and no panel would ever dispatch.
        if _settles(job, outcome):
            for panel_id in await panel.panels_awaiting(session, job_id=job.id):
                dispatched = await panel.dispatch_synthesis_if_ready(
                    session, panel_id=panel_id, finishing_job_id=job.id
                )
                if dispatched is not None:
                    logger.info(
                        "panel %s complete; synthesis queued as job %s", panel_id, dispatched.id
                    )
            await refresh_analysis_status(
                session, case_id=job.case_id, finishing_job_id=job.id
            )
        return outcome

    worker.register(JobType.PARSE_DOSSIER, handle_parse)
    worker.register(JobType.EXTRACT_FACTS, handle_extract)
    worker.register(JobType.RUN_AGENT, handle_agent)
    return worker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        worker = build_worker()
    except (ParserNotConfigured, PiNotConfigured, EmbeddingError) as exc:
        logger.error("worker cannot start: %s", exc)
        raise SystemExit(2) from exc

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        received = getattr(signal, signal_name, None)
        if received is not None:
            try:
                loop.add_signal_handler(received, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                signal.signal(received, lambda *_: stop.set())

    await worker.run_forever(stop=stop)


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    asyncio.run(main())
