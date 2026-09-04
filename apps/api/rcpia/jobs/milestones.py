"""Reporting which step a running job is on.

**The write has to happen outside the handler's transaction.** A handler is given one
session and the worker commits it once, after the handler returns; anything written on
that session becomes visible at the moment the job ends. A milestone committed then says
"assembling the bundle" about a run that finished — worse than no milestone, because a
reader cannot tell a stale claim from a live one.

So a reporter opens its own short transaction per write, the same way the worker's
heartbeat renews its lease, and commits immediately.

**And a failed write is never a failed job.** Progress is cosmetic; an opinion is not.
Losing the lease mid-run, or losing the database for a moment, must cost the display and
nothing else, so every failure here is logged and swallowed.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.jobs import service

logger = logging.getLogger(__name__)

# What a handler is given. A plain callable, so a handler that wants to report progress
# does not have to know that a database is behind it -- and so tests can pass a list.
MilestoneWriter = Callable[[str], Awaitable[None]]

__all__ = ["MilestoneWriter", "MilestoneReporter", "no_milestones"]


async def no_milestones(step: str) -> None:
    """The default: report nowhere.

    Handlers take an optional writer, so a caller with no job queue behind it -- an
    evaluation harness, a test, a one-off script -- runs the same code path.
    """


class MilestoneReporter:
    """Writes a claimed job's current step, in its own transaction, and never raises."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
    ) -> None:
        self.session_factory = session_factory
        self.job_id = job_id
        self.claim_token = claim_token

    async def __call__(self, step: str) -> None:
        async with self.session_factory() as session:
            try:
                await service.record_milestone(session, self.job_id, self.claim_token, step)
                await session.commit()
            except service.JobTransitionError:
                # The lease expired and another worker owns this job; its progress is the
                # one a reader should see. This run is finishing work nobody is waiting for.
                await session.rollback()
                logger.warning(
                    "could not record milestone %r on job %s: claim lost", step, self.job_id
                )
            except Exception:  # noqa: BLE001 - a progress write must never fail a run
                await session.rollback()
                logger.exception("could not record milestone %r on job %s", step, self.job_id)
