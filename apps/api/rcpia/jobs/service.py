"""PostgreSQL-backed job queue.

There is no Redis, RabbitMQ, or NATS in v1: PostgreSQL is the queue as well as the
clinical store. Claiming relies on `FOR UPDATE SKIP LOCKED`, so several workers can poll
the same table concurrently and each row is handed to one of them at a time.

**Delivery is at-least-once, not exactly-once.** A worker can finish its work and die
before recording the outcome; the lease then expires and another worker runs the same job
again. Handlers must therefore be idempotent — write results keyed by job or case so a
repeated run overwrites rather than duplicates.

What the queue does guarantee is that a stale worker cannot corrupt a newer run. Every
claim mints a `claim_token`, and every transition must present the token it was claimed
with, so a worker whose lease expired can no longer complete, fail, or extend the job it
has lost.

All time comparisons use the database clock (`now()`), never the worker's, so clock skew
between workers cannot cause early reclamation.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.core.models import AnalysisJob, JobStatus
from rcpia.jobs.models import DEFAULT_LEASE, DEFAULT_MAX_ATTEMPTS, retry_delay

TERMINAL_STATUSES = (JobStatus.completed, JobStatus.cancelled, JobStatus.failed)

# now() is the transaction start time on PostgreSQL, which is what we want: every
# comparison inside one transaction sees a single consistent instant.
# Two clocks, and the difference matters.
#
# `now()` is PostgreSQL's *transaction* timestamp: every statement in one transaction sees the
# same value, however long the transaction runs. That is what makes it right for the comparisons
# below -- a job's runnability should not shift underneath a query -- and wrong for recording
# when something happened. A worker holds one transaction open across the whole handler, so a
# fifty-four second agent run wrote `finished_at` equal to the instant the handler began.
# Measured in the end-to-end verification: every job reported finishing within twenty
# milliseconds of starting, including runs that took a minute.
#
# `clock_timestamp()` reads the wall clock at the statement, which is what a timestamp of an
# event has to mean. Used for the moments -- claimed, started, finished -- and for deadlines
# computed from now, since a lease measured from a transaction that opened minutes ago is
# already partly spent.
DB_NOW = sa.func.now()
DB_CLOCK = sa.func.clock_timestamp()


class JobTransitionError(RuntimeError):
    """A transition was refused: wrong claim token, or the job left the expected state."""


async def enqueue(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
    job_type: str,
    payload: dict | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    available_at: datetime | None = None,
    dedupe_key: str | None = None,
) -> AnalysisJob:
    """Queue a job.

    `dedupe_key` names work that must not be queued twice while a previous request is
    still live. A partial unique index enforces it, so a second caller raises
    `IntegrityError` rather than quietly creating a duplicate.
    """
    job = AnalysisJob(
        case_id=case_id,
        organization_id=organization_id,
        job_type=job_type,
        payload=payload or {},
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
        status=JobStatus.queued,
        available_at=available_at or DB_NOW,
    )
    session.add(job)
    await session.flush()
    return job


async def claim_next(
    session: AsyncSession,
    *,
    worker: str,
    job_types: list[str] | None = None,
    lease: timedelta = DEFAULT_LEASE,
) -> AnalysisJob | None:
    """Claim one runnable job, or return None.

    `FOR UPDATE SKIP LOCKED` inside the subquery is what makes this safe under
    concurrency: a row already locked by another worker's claim is skipped rather than
    waited on, so two workers never receive the same job and neither blocks the other.

    A job is runnable when it is queued and due, or when it is running under an expired
    lease — but in both cases only while attempts remain. Exhausted jobs with a dead
    lease are not reclaimed; `reap_expired_leases` fails them instead.
    """
    runnable = sa.or_(
        sa.and_(AnalysisJob.status == JobStatus.queued, AnalysisJob.available_at <= DB_NOW),
        sa.and_(
            AnalysisJob.status == JobStatus.running,
            AnalysisJob.lease_expires_at.is_not(None),
            AnalysisJob.lease_expires_at < DB_NOW,
        ),
    )

    claimable = (
        sa.select(AnalysisJob.id)
        .where(runnable, AnalysisJob.attempts < AnalysisJob.max_attempts)
        .order_by(AnalysisJob.available_at, AnalysisJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_types is not None:
        # An empty list means "no types are acceptable", not "every type".
        claimable = claimable.where(AnalysisJob.job_type.in_(job_types))

    claimed = await session.execute(
        sa.update(AnalysisJob)
        .where(AnalysisJob.id == claimable.scalar_subquery())
        .values(
            status=JobStatus.running,
            claim_token=uuid.uuid4(),
            claimed_by=worker,
            claimed_at=DB_CLOCK,
            lease_expires_at=DB_CLOCK + lease,
            started_at=sa.func.coalesce(AnalysisJob.started_at, DB_CLOCK),
            attempts=AnalysisJob.attempts + 1,
        )
        .returning(AnalysisJob)
    )
    return claimed.scalars().one_or_none()


async def reap_expired_leases(session: AsyncSession) -> int:
    """Recover jobs no worker can be holding. Returns how many were failed.

    Two shapes, both meaning "running, but nobody is running it".

    The first is the ordinary one: a lease that expired with no attempts left. The worker died,
    the job was reclaimed as often as its budget allowed, and there is nothing further to try.

    The second is a job in `running` that **holds no lease at all**, and it was a hole. Claiming
    sets the status and the lease in one statement, so a running row without a lease was never
    claimed by anyone -- yet `claim_next` will not take it, because its expired-lease branch
    requires a lease to be set, and this reaper would not touch it either. Meanwhile
    `start_analysis` and `start_panel` both count it as live, so that `(case, role)` could never
    be analysed again. Measured in the working database: 342 such rows across 118 cases, the
    oldest three weeks old, every one with `claimed_by`, `claim_token` and `lease_expires_at`
    null. They were left by a measurement driver that marked jobs running itself instead of
    claiming them -- not shipped code, but the queue having no way back from that state is this
    module's own gap, and it blocked a real panel.

    **Failed rather than requeued.** Nothing ever ran, so requeueing looks tempting; but silently
    starting work that was abandoned weeks ago would spend real model time answering questions
    nobody is waiting for. Failing it unblocks the case, says why, and asking again is one
    request.
    """
    orphaned = sa.and_(
        AnalysisJob.status == JobStatus.running,
        AnalysisJob.lease_expires_at.is_(None),
    )
    exhausted = sa.and_(
        AnalysisJob.status == JobStatus.running,
        AnalysisJob.lease_expires_at.is_not(None),
        AnalysisJob.lease_expires_at < DB_NOW,
        AnalysisJob.attempts >= AnalysisJob.max_attempts,
    )
    result = cast(
        sa.CursorResult[Any],
        await session.execute(
            sa.update(AnalysisJob)
            .where(sa.or_(exhausted, orphaned))
            .values(
                status=JobStatus.failed,
                error_message=sa.func.coalesce(
                    AnalysisJob.error_message,
                    sa.case(
                        (
                            AnalysisJob.lease_expires_at.is_(None),
                            "Running with no lease: no worker ever claimed this job",
                        ),
                        else_="Lease expired with no attempts remaining",
                    ),
                ),
                finished_at=DB_CLOCK,
                claim_token=None,
                claimed_by=None,
                lease_expires_at=None,
            )
        ),
    )
    return result.rowcount or 0


async def extend_lease(
    session: AsyncSession,
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    lease: timedelta = DEFAULT_LEASE,
) -> None:
    """Heartbeat for long-running work, so a live worker does not lose its job."""
    await _transition(
        session,
        job_id,
        claim_token,
        from_statuses=(JobStatus.running,),
        values={"lease_expires_at": DB_CLOCK + lease},
        action="extend the lease on",
    )


async def record_milestone(
    session: AsyncSession,
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    step: str,
) -> None:
    """Record which step a running job is on, for a progress display to read.

    Fenced like every other transition: a worker whose lease expired cannot rewrite the
    progress of the run that replaced it, which would show the newer run going backwards.

    This never changes `status`. A milestone is a claim about the present, and the only
    thing that decides whether an opinion exists is the outcome the handler returns.
    """
    await _transition(
        session,
        job_id,
        claim_token,
        from_statuses=(JobStatus.running,),
        values={"milestone": step, "milestone_at": DB_CLOCK},
        action="record a milestone on",
    )


async def complete(session: AsyncSession, job_id: uuid.UUID, claim_token: uuid.UUID) -> None:
    """Mark a job finished. The last error message is kept as attempt history."""
    await _transition(
        session,
        job_id,
        claim_token,
        from_statuses=(JobStatus.running,),
        values={
            "status": JobStatus.completed,
            "finished_at": DB_CLOCK,
            "claim_token": None,
            "claimed_by": None,
            "lease_expires_at": None,
        },
        action="complete",
    )


async def wait_for_user(
    session: AsyncSession, job_id: uuid.UUID, claim_token: uuid.UUID, *, reason: str
) -> None:
    """Park a job that cannot proceed without a human — missing information, for example."""
    await _transition(
        session,
        job_id,
        claim_token,
        from_statuses=(JobStatus.running,),
        values={
            "status": JobStatus.waiting_for_user,
            "error_message": reason,
            "claim_token": None,
            "claimed_by": None,
            "lease_expires_at": None,
        },
        action="park",
    )


async def fail(
    session: AsyncSession,
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    error: str,
    terminal: bool = False,
) -> None:
    """Record a failure, retrying with backoff until `max_attempts` is exhausted.

    `terminal=True` skips the retries entirely, for failures that cannot succeed on a
    later attempt — a missing API key, a malformed payload, a document that does not
    exist. Retrying those wastes the attempt budget and delays the human who has to fix
    the cause.

    The error detail is stored on every attempt, including retried ones, so a job that
    eventually succeeds still carries the last thing that went wrong.
    """
    job = await session.get(AnalysisJob, job_id)
    if job is None:
        raise JobTransitionError(f"Cannot fail job {job_id}: it does not exist")

    if terminal or job.attempts >= job.max_attempts:
        values: dict[str, object] = {
            "status": JobStatus.failed,
            "error_message": error,
            "finished_at": DB_CLOCK,
        }
    else:
        values = {
            "status": JobStatus.queued,
            "error_message": error,
            "available_at": DB_CLOCK + retry_delay(job.attempts),
        }

    await _transition(
        session,
        job_id,
        claim_token,
        from_statuses=(JobStatus.running,),
        values={**values, "claim_token": None, "claimed_by": None, "lease_expires_at": None},
        action="fail",
    )


async def cancel(session: AsyncSession, job_id: uuid.UUID, *, reason: str | None = None) -> None:
    """Cancel a job. Administrative, so it needs no claim token — but a finished job
    cannot be cancelled retroactively."""
    await _transition(
        session,
        job_id,
        claim_token=None,
        from_statuses=(JobStatus.queued, JobStatus.running, JobStatus.waiting_for_user),
        values={
            "status": JobStatus.cancelled,
            "error_message": reason,
            "finished_at": DB_CLOCK,
            "claim_token": None,
            "claimed_by": None,
            "lease_expires_at": None,
        },
        action="cancel",
    )


async def resume(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Return a parked job to the queue once the human has responded."""
    await _transition(
        session,
        job_id,
        claim_token=None,
        from_statuses=(JobStatus.waiting_for_user,),
        values={"status": JobStatus.queued, "available_at": DB_CLOCK, "error_message": None},
        action="resume",
    )


async def _transition(
    session: AsyncSession,
    job_id: uuid.UUID,
    claim_token: uuid.UUID | None,
    *,
    from_statuses: tuple[JobStatus, ...],
    values: dict[str, object],
    action: str,
) -> None:
    """Apply a state change only from an expected state, and only to the current claim.

    The status and token conditions live in the UPDATE itself, so the check and the write
    are one atomic statement. Zero rows updated means the job moved on without us.
    """
    conditions = [AnalysisJob.id == job_id, AnalysisJob.status.in_(from_statuses)]
    if claim_token is not None:
        conditions.append(AnalysisJob.claim_token == claim_token)

    result = cast(
        sa.CursorResult[Any],
        await session.execute(sa.update(AnalysisJob).where(*conditions).values(**values)),
    )

    if (result.rowcount or 0) == 0:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            raise JobTransitionError(f"Cannot {action} job {job_id}: it does not exist")
        raise JobTransitionError(
            f"Cannot {action} job {job_id}: it is {job.status.value} and the claim is "
            "no longer valid (the lease most likely expired and another worker took over)"
        )
