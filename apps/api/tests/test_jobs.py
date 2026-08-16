"""Job queue tests.

The queue is PostgreSQL, so the properties that matter — one job to one worker, retry
with backoff, lease recovery — are properties of the SQL, not of Python. These tests use
real concurrent connections rather than a rolled-back single session.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.core.models import AnalysisJob, CancerType, Case, JobStatus, Organization
from rcpia.jobs import service
from rcpia.jobs.models import JobOutcome, JobType, retry_delay
from rcpia.jobs.worker import Worker


async def make_case(factory: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:10]}")
        session.add(organization)
        await session.flush()
        case = Case(
            organization_id=organization.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
        )
        session.add(case)
        await session.commit()
        return case.id, organization.id


async def enqueue_job(
    factory: async_sessionmaker[AsyncSession],
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
    **kwargs: object,
) -> uuid.UUID:
    async with factory() as session:
        job = await service.enqueue(
            session,
            case_id=case_id,
            organization_id=organization_id,
            job_type=kwargs.pop("job_type", JobType.PARSE_DOSSIER),  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )
        await session.commit()
        return job.id


async def read_job(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> AnalysisJob:
    async with factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        return job


# --------------------------------------------------------------------------------------
# Claiming
# --------------------------------------------------------------------------------------


async def test_enqueued_job_starts_queued(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    job = await read_job(session_factory, job_id)
    assert job.status is JobStatus.queued
    assert job.attempts == 0
    assert job.claimed_by is None


async def test_two_workers_cannot_claim_the_same_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    async def claim(worker: str) -> AnalysisJob | None:
        async with session_factory() as session:
            job = await service.claim_next(session, worker=worker)
            await session.commit()
            return job

    first, second = await asyncio.gather(claim("worker-a"), claim("worker-b"))

    claimed = [job for job in (first, second) if job is not None]
    assert len(claimed) == 1, "exactly one worker must win the job"
    assert claimed[0].status is JobStatus.running
    assert claimed[0].attempts == 1
    assert claimed[0].claimed_by in {"worker-a", "worker-b"}


async def test_concurrent_workers_split_the_queue_without_duplication(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_ids = {
        await enqueue_job(session_factory, case_id, organization_id) for _ in range(12)
    }

    async def drain(worker: str) -> list[uuid.UUID]:
        claimed: list[uuid.UUID] = []
        while True:
            async with session_factory() as session:
                job = await service.claim_next(session, worker=worker)
                await session.commit()
            if job is None:
                return claimed
            claimed.append(job.id)

    results = await asyncio.gather(*(drain(f"worker-{n}") for n in range(4)))
    claimed = [job_id for batch in results for job_id in batch]

    assert len(claimed) == len(set(claimed)), "no job may be claimed twice"
    assert set(claimed) == job_ids


async def test_claim_returns_none_when_queue_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await service.claim_next(session, worker="idle") is None


async def test_claim_respects_job_type_filter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id, job_type=JobType.RUN_AGENT)

    async with session_factory() as session:
        other_type = await service.claim_next(
            session, worker="w", job_types=[JobType.GENERATE_PPS]
        )
        assert other_type is None
        job = await service.claim_next(session, worker="w", job_types=[JobType.RUN_AGENT])
        await session.commit()

    assert job is not None
    assert job.job_type == JobType.RUN_AGENT


async def test_job_scheduled_for_later_is_not_claimable_yet(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(
        session_factory,
        case_id,
        organization_id,
        available_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async with session_factory() as session:
        assert await service.claim_next(session, worker="w") is None


# --------------------------------------------------------------------------------------
# Leases
# --------------------------------------------------------------------------------------


async def test_expired_lease_makes_a_running_job_claimable_again(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        first = await service.claim_next(session, worker="dying-worker", lease=timedelta(seconds=1))
        await session.commit()
    assert first is not None

    # Simulate the worker dying: the lease lapses without the job finishing.
    async with session_factory() as session:
        await session.execute(
            sa.update(AnalysisJob)
            .where(AnalysisJob.id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    async with session_factory() as session:
        second = await service.claim_next(session, worker="rescuer")
        await session.commit()

    assert second is not None
    assert second.id == job_id
    assert second.claimed_by == "rescuer"
    assert second.attempts == 2, "the reclaimed run counts as another attempt"


async def test_live_lease_protects_a_running_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        first = await service.claim_next(session, worker="busy-worker")
        await session.commit()
    assert first is not None

    async with session_factory() as session:
        assert await service.claim_next(session, worker="other") is None


async def test_extend_lease_pushes_the_deadline_out(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        job = await service.claim_next(session, worker="w", lease=timedelta(minutes=1))
        assert job is not None
        original = job.lease_expires_at
        await service.extend_lease(session, job, lease=timedelta(minutes=30))
        await session.commit()

    refreshed = await read_job(session_factory, job.id)
    assert original is not None and refreshed.lease_expires_at is not None
    assert refreshed.lease_expires_at > original


# --------------------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------------------


async def test_failure_is_retried_with_backoff_and_keeps_the_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id, max_attempts=3)

    async with session_factory() as session:
        job = await service.claim_next(session, worker="w")
        assert job is not None
        await service.fail(session, job, error="LlamaParse timed out")
        await session.commit()

    retried = await read_job(session_factory, job.id)
    assert retried.status is JobStatus.queued
    assert retried.error_message == "LlamaParse timed out"
    assert retried.attempts == 1
    assert retried.claimed_by is None
    assert retried.available_at > datetime.now(UTC), "backoff delays the retry"


async def test_failure_becomes_terminal_once_attempts_are_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id, max_attempts=2)

    for attempt in (1, 2):
        async with session_factory() as session:
            # Backoff would otherwise hide the job from the second claim.
            await session.execute(
                sa.update(AnalysisJob).values(available_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            job = await service.claim_next(session, worker="w")
            assert job is not None, f"attempt {attempt} should be claimable"
            await service.fail(session, job, error=f"boom {attempt}")
            await session.commit()

    final = await read_job(session_factory, job.id)
    assert final.status is JobStatus.failed
    assert final.error_message == "boom 2"
    assert final.attempts == 2
    assert final.finished_at is not None


async def test_completed_job_is_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        job = await service.claim_next(session, worker="w")
        assert job is not None
        await service.complete(session, job)
        await session.commit()

    finished = await read_job(session_factory, job.id)
    assert finished.status is JobStatus.completed
    assert finished.finished_at is not None

    async with session_factory() as session:
        assert await service.claim_next(session, worker="w") is None


async def test_job_can_park_waiting_for_a_human_and_resume(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        job = await service.claim_next(session, worker="w")
        assert job is not None
        await service.wait_for_user(session, job, reason="Clinical stage missing from dossier")
        await session.commit()

    parked = await read_job(session_factory, job.id)
    assert parked.status is JobStatus.waiting_for_user
    assert parked.error_message == "Clinical stage missing from dossier"

    async with session_factory() as session:
        assert await service.claim_next(session, worker="w") is None

    async with session_factory() as session:
        reloaded = await session.get(AnalysisJob, job.id)
        assert reloaded is not None
        await service.resume(session, reloaded)
        await session.commit()

    async with session_factory() as session:
        resumed = await service.claim_next(session, worker="w")
        await session.commit()
    assert resumed is not None and resumed.id == job.id


async def test_cancelled_job_is_not_claimable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        await service.cancel(session, job, reason="Case withdrawn")
        await session.commit()

    async with session_factory() as session:
        assert await service.claim_next(session, worker="w") is None

    cancelled = await read_job(session_factory, job_id)
    assert cancelled.status is JobStatus.cancelled
    assert cancelled.error_message == "Case withdrawn"


def test_retry_delay_grows_and_is_capped() -> None:
    assert retry_delay(1) == timedelta(seconds=60)
    assert retry_delay(2) == timedelta(seconds=120)
    assert retry_delay(3) == timedelta(seconds=240)
    assert retry_delay(50) == timedelta(seconds=600)


# --------------------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------------------


async def test_worker_runs_a_handler_and_completes_the_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    handled: list[uuid.UUID] = []

    async def handler(session: AsyncSession, job: AnalysisJob) -> None:
        handled.append(job.id)

    worker = Worker(session_factory, name="test-worker")
    worker.register(JobType.PARSE_DOSSIER, handler)

    processed = await worker.run_once()

    assert processed is not None and processed.id == job_id
    assert handled == [job_id]
    assert (await read_job(session_factory, job_id)).status is JobStatus.completed


async def test_worker_records_handler_exceptions_as_job_failures(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=1)

    async def handler(session: AsyncSession, job: AnalysisJob) -> None:
        raise RuntimeError("parser exploded")

    worker = Worker(session_factory, name="test-worker")
    worker.register(JobType.PARSE_DOSSIER, handler)

    await worker.run_once()

    failed = await read_job(session_factory, job_id)
    assert failed.status is JobStatus.failed
    assert "parser exploded" in (failed.error_message or "")


async def test_worker_fails_jobs_with_no_registered_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(
        session_factory, case_id, organization_id, job_type="unheard_of", max_attempts=1
    )

    worker = Worker(session_factory, name="test-worker")

    await worker.run_once()

    failed = await read_job(session_factory, job_id)
    assert failed.status is JobStatus.failed
    assert "unheard_of" in (failed.error_message or "")


async def test_worker_returns_none_when_idle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker = Worker(session_factory, name="test-worker")
    assert await worker.run_once() is None


async def test_worker_honours_an_outcome_returned_by_a_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    async def handler(session: AsyncSession, job: AnalysisJob) -> JobOutcome:
        await service.wait_for_user(session, job, reason="Needs a human")
        return JobOutcome(status=JobStatus.waiting_for_user, error_message="Needs a human")

    worker = Worker(session_factory, name="test-worker")
    worker.register(JobType.PARSE_DOSSIER, handler)

    await worker.run_once()

    parked = await read_job(session_factory, job_id)
    assert parked.status is JobStatus.waiting_for_user


async def test_run_forever_stops_when_asked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker = Worker(session_factory, name="test-worker", poll_interval=0.05)
    stop = asyncio.Event()

    task = asyncio.create_task(worker.run_forever(stop=stop))
    await asyncio.sleep(0.15)
    stop.set()

    await asyncio.wait_for(task, timeout=5)
    assert task.done()


@pytest.mark.parametrize("status", [JobStatus.completed, JobStatus.cancelled, JobStatus.failed])
def test_terminal_statuses_are_declared(status: JobStatus) -> None:
    assert status in service.TERMINAL_STATUSES
