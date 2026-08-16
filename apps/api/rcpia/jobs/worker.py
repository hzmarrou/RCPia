"""Job worker.

Runs as its own container (`rcpia-worker`). It polls PostgreSQL for claimable jobs,
dispatches each to a registered handler, and records the outcome. Handlers are
registered by later phases — parsing, extraction, agent runs, PPS generation.
"""

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.core.models import AnalysisJob
from rcpia.jobs import service
from rcpia.jobs.models import DEFAULT_LEASE, JobOutcome

logger = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, AnalysisJob], Awaitable[JobOutcome | None]]


class UnknownJobType(Exception):
    """Raised when a job has no registered handler."""


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
    ) -> None:
        self.session_factory = session_factory
        self.name = name or default_worker_name()
        self.job_types = job_types
        self.lease = lease
        self.poll_interval = poll_interval
        self._handlers: dict[str, Handler] = {}

    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    async def run_once(self) -> AnalysisJob | None:
        """Claim and process at most one job. Returns the job, or None if the queue is idle.

        Claiming commits before the handler runs, so a crash mid-handler leaves the job
        `running` with a lease that expires rather than lost or silently retried.
        """
        async with self.session_factory() as session:
            job = await service.claim_next(
                session, worker=self.name, job_types=self.job_types, lease=self.lease
            )
            if job is None:
                await session.rollback()
                return None
            await session.commit()
            job_id, job_type = job.id, job.job_type

        async with self.session_factory() as session:
            claimed = await session.get(AnalysisJob, job_id)
            if claimed is None:  # pragma: no cover - the row cannot vanish under us
                return None

            handler = self._handlers.get(job_type)
            if handler is None:
                await service.fail(
                    session, claimed, error=f"No handler registered for job type {job_type!r}"
                )
                await session.commit()
                logger.error("job %s has unknown type %s", job_id, job_type)
                return claimed

            try:
                outcome = await handler(session, claimed)
            except Exception as exc:  # noqa: BLE001 - any handler failure is a job failure
                await session.rollback()
                async with self.session_factory() as failure_session:
                    reloaded = await failure_session.get(AnalysisJob, job_id)
                    if reloaded is not None:
                        await service.fail(failure_session, reloaded, error=repr(exc))
                        await failure_session.commit()
                logger.exception("job %s raised", job_id)
                return claimed

            if outcome is None:
                await service.complete(session, claimed)
            await session.commit()
            return claimed

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        logger.info("worker %s started", self.name)
        while not stop.is_set():
            try:
                job = await self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive infrastructure errors
                logger.exception("worker %s failed to process a job", self.name)
                job = None

            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    continue
        logger.info("worker %s stopped", self.name)
