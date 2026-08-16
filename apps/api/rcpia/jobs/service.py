"""PostgreSQL-backed job queue.

There is no Redis, RabbitMQ, or NATS in v1: PostgreSQL is the queue as well as the
clinical store. Claiming relies on `FOR UPDATE SKIP LOCKED`, so several workers can poll
the same table concurrently and each row goes to exactly one of them.
"""

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.core.models import AnalysisJob, JobStatus
from rcpia.jobs.models import DEFAULT_LEASE, DEFAULT_MAX_ATTEMPTS, retry_delay

TERMINAL_STATUSES = (JobStatus.completed, JobStatus.cancelled, JobStatus.failed)


def _now() -> datetime:
    return datetime.now(UTC)


async def enqueue(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
    job_type: str,
    payload: dict | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    available_at: datetime | None = None,
) -> AnalysisJob:
    job = AnalysisJob(
        case_id=case_id,
        organization_id=organization_id,
        job_type=job_type,
        payload=payload or {},
        max_attempts=max_attempts,
        status=JobStatus.queued,
        available_at=available_at or _now(),
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

    `FOR UPDATE SKIP LOCKED` inside the subquery is what makes this safe: a row already
    locked by another worker's claim is skipped rather than waited on, so two workers
    never receive the same job and neither blocks the other.
    """
    now = _now()

    claimable = (
        sa.select(AnalysisJob.id)
        .where(
            sa.or_(
                sa.and_(
                    AnalysisJob.status == JobStatus.queued,
                    AnalysisJob.available_at <= now,
                ),
                # Reclaim work abandoned by a worker that died mid-run.
                sa.and_(
                    AnalysisJob.status == JobStatus.running,
                    AnalysisJob.lease_expires_at.is_not(None),
                    AnalysisJob.lease_expires_at < now,
                ),
            )
        )
        .order_by(AnalysisJob.available_at, AnalysisJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_types:
        claimable = claimable.where(AnalysisJob.job_type.in_(job_types))

    claimed = await session.execute(
        sa.update(AnalysisJob)
        .where(AnalysisJob.id == claimable.scalar_subquery())
        .values(
            status=JobStatus.running,
            claimed_by=worker,
            claimed_at=now,
            lease_expires_at=now + lease,
            started_at=sa.func.coalesce(AnalysisJob.started_at, now),
            attempts=AnalysisJob.attempts + 1,
        )
        .returning(AnalysisJob)
    )
    return claimed.scalars().one_or_none()


async def extend_lease(
    session: AsyncSession, job: AnalysisJob, *, lease: timedelta = DEFAULT_LEASE
) -> None:
    """Heartbeat for long-running work, so a live worker does not lose its job."""
    await session.execute(
        sa.update(AnalysisJob)
        .where(AnalysisJob.id == job.id)
        .values(lease_expires_at=_now() + lease)
    )


async def complete(session: AsyncSession, job: AnalysisJob) -> None:
    await _finish(session, job, JobStatus.completed)


async def cancel(session: AsyncSession, job: AnalysisJob, *, reason: str | None = None) -> None:
    await _finish(session, job, JobStatus.cancelled, error_message=reason)


async def wait_for_user(session: AsyncSession, job: AnalysisJob, *, reason: str) -> None:
    """Park a job that cannot proceed without a human — missing information, for example."""
    await session.execute(
        sa.update(AnalysisJob)
        .where(AnalysisJob.id == job.id)
        .values(
            status=JobStatus.waiting_for_user,
            error_message=reason,
            claimed_by=None,
            lease_expires_at=None,
        )
    )
    await session.refresh(job)


async def resume(session: AsyncSession, job: AnalysisJob) -> None:
    """Return a parked job to the queue once the human has responded."""
    await session.execute(
        sa.update(AnalysisJob)
        .where(AnalysisJob.id == job.id)
        .values(status=JobStatus.queued, available_at=_now(), error_message=None)
    )
    await session.refresh(job)


async def fail(session: AsyncSession, job: AnalysisJob, *, error: str) -> AnalysisJob:
    """Record a failure, retrying with backoff until `max_attempts` is exhausted.

    The error detail is always stored, including on the attempts that will be retried,
    so a job that eventually succeeds still leaves a trace of what went wrong.
    """
    exhausted = job.attempts >= job.max_attempts
    now = _now()

    if exhausted:
        values = {
            "status": JobStatus.failed,
            "error_message": error,
            "finished_at": now,
            "claimed_by": None,
            "lease_expires_at": None,
        }
    else:
        values = {
            "status": JobStatus.queued,
            "error_message": error,
            "available_at": now + retry_delay(job.attempts),
            "claimed_by": None,
            "lease_expires_at": None,
        }

    await session.execute(sa.update(AnalysisJob).where(AnalysisJob.id == job.id).values(**values))
    await session.refresh(job)
    return job


async def _finish(
    session: AsyncSession,
    job: AnalysisJob,
    status: JobStatus,
    *,
    error_message: str | None = None,
) -> None:
    await session.execute(
        sa.update(AnalysisJob)
        .where(AnalysisJob.id == job.id)
        .values(
            status=status,
            finished_at=_now(),
            error_message=error_message,
            claimed_by=None,
            lease_expires_at=None,
        )
    )
    await session.refresh(job)
