"""Job worker.

Runs as its own container (`rcpia-worker`). It polls PostgreSQL for claimable jobs,
dispatches each to a registered handler, and records the outcome. Handlers are
registered by later phases — parsing, extraction, agent runs, PPS generation.

Delivery is at-least-once: a worker that dies after finishing its work but before
recording the outcome will have that job run again. Handlers must be idempotent.
"""

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.core.models import AnalysisJob, JobStatus
from rcpia.jobs import service
from rcpia.jobs.models import DEFAULT_LEASE, JobOutcome

logger = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, AnalysisJob], Awaitable[JobOutcome | None]]
# Work done every poll regardless of what is claimed. It shares the reaper's session and
# is committed with it, so a failure here fails the poll rather than half-applying.
Housekeeping = Callable[[AsyncSession], Awaitable[object]]


def default_worker_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        name: str | None = None,
        job_types: list[str] | None = None,
        lease: timedelta = DEFAULT_LEASE,
        poll_interval: float = 2.0,
        heartbeat_interval: float | None = None,
        housekeeping: "Housekeeping | None" = None,
    ) -> None:
        self.session_factory = session_factory
        self.name = name or default_worker_name()
        self.job_types = job_types
        self.lease = lease
        self.poll_interval = poll_interval
        # Run on every poll, before a job is claimed, in the same session as the lease reaper.
        # Reconciliation that must not depend on any particular handler having run belongs
        # here: a handler only observes the jobs it was given, and a job can reach a terminal
        # state without one -- a crash, an expired lease, a worker killed mid-run.
        self.housekeeping = housekeeping
        # Renew well before the lease lapses, so a slow handler keeps its job.
        self.heartbeat_interval = heartbeat_interval or lease.total_seconds() / 3
        self._handlers: dict[str, Handler] = {}

    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    async def run_once(self) -> uuid.UUID | None:
        """Claim and process at most one job. Returns its id, or None if the queue is idle.

        An id rather than the ORM object: each step runs in its own session, so a returned
        instance would be detached and unusable by the caller.

        The claim is committed before the handler runs, so a crash mid-handler leaves the
        job `running` with a lease that expires rather than lost or silently retried.
        """
        async with self.session_factory() as session:
            await service.reap_expired_leases(session)
            if self.housekeeping is not None:
                await self.housekeeping(session)
            job = await service.claim_next(
                session, worker=self.name, job_types=self.job_types, lease=self.lease
            )
            if job is None:
                await session.commit()
                return None
            await session.commit()
            job_id, job_type, claim_token = job.id, job.job_type, job.claim_token

        assert claim_token is not None  # a claimed job always carries its token

        handler = self._handlers.get(job_type)
        if handler is None:
            await self._record_failure(
                job_id, claim_token, f"No handler registered for job type {job_type!r}"
            )
            logger.error("job %s has unknown type %s", job_id, job_type)
            return job_id

        # The heartbeat runs on its own session: the handler's transaction is still open,
        # so a lease renewal written inside it would be invisible to other workers.
        heartbeat = asyncio.create_task(self._heartbeat(job_id, claim_token))
        try:
            async with self.session_factory() as session:
                claimed = await session.get(AnalysisJob, job_id)
                if claimed is None:  # pragma: no cover - the row cannot vanish under us
                    return None
                try:
                    outcome = await handler(session, claimed)
                except Exception as exc:  # noqa: BLE001 - any handler failure fails the job
                    await session.rollback()
                    await self._record_failure(job_id, claim_token, repr(exc))
                    logger.exception("job %s raised", job_id)
                    return job_id

                await self._apply_outcome(session, job_id, claim_token, outcome)
                await session.commit()
                return job_id
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _apply_outcome(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        outcome: JobOutcome | None,
    ) -> None:
        """Record what the handler reported. Returning None means success.

        This runs in the handler's own session, and the caller commits both together, so
        a handler that recorded why it failed keeps those writes instead of having them
        rolled back alongside the transition.
        """
        if outcome is None or outcome.status is JobStatus.completed:
            await service.complete(session, job_id, claim_token)
        elif outcome.status is JobStatus.waiting_for_user:
            await service.wait_for_user(
                session,
                job_id,
                claim_token,
                reason=outcome.error_message or "Waiting for user input",
            )
        elif outcome.status is JobStatus.failed:
            await service.fail(
                session,
                job_id,
                claim_token,
                error=outcome.error_message or "Handler reported failure",
                terminal=not outcome.retryable,
            )
        elif outcome.status is JobStatus.cancelled:
            await service.cancel(session, job_id, reason=outcome.error_message)
        else:
            raise ValueError(f"Handler returned an unusable outcome status: {outcome.status}")

    async def _record_failure(
        self, job_id: uuid.UUID, claim_token: uuid.UUID, error: str
    ) -> None:
        async with self.session_factory() as session:
            try:
                await service.fail(session, job_id, claim_token, error=error)
                await session.commit()
            except service.JobTransitionError:
                # The lease expired and someone else owns this job now; their result wins.
                await session.rollback()
                logger.warning("could not record failure for job %s: claim lost", job_id)

    async def _heartbeat(self, job_id: uuid.UUID, claim_token: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            async with self.session_factory() as session:
                try:
                    await service.extend_lease(session, job_id, claim_token, lease=self.lease)
                    await session.commit()
                except service.JobTransitionError:
                    await session.rollback()
                    logger.warning("lost the lease on job %s while working", job_id)
                    return

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        logger.info("worker %s started", self.name)
        while not stop.is_set():
            try:
                job_id = await self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive infrastructure errors
                logger.exception("worker %s failed to process a job", self.name)
                job_id = None

            if job_id is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    continue
        logger.info("worker %s stopped", self.name)
