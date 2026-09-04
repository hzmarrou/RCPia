"""Job queue tests.

The queue is PostgreSQL, so the properties that matter — one job to one worker at a time,
bounded retries, lease recovery, and fencing against stale workers — are properties of the
SQL, not of Python. These tests use real concurrent connections rather than a rolled-back
single session.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.core.models import AnalysisJob, CancerType, Case, JobStatus, Organization
from rcpia.jobs import service
from rcpia.jobs.milestones import MilestoneReporter
from rcpia.jobs.models import AGENT_STEPS, JobOutcome, JobType, RunStep, retry_delay
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


async def claim(
    factory: async_sessionmaker[AsyncSession], worker: str, **kwargs: object
) -> AnalysisJob | None:
    async with factory() as session:
        job = await service.claim_next(session, worker=worker, **kwargs)  # type: ignore[arg-type]
        await session.commit()
        return job


async def read_job(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> AnalysisJob:
    async with factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        return job


async def expire_lease(factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> None:
    """Simulate a worker dying: the lease lapses without the job finishing."""
    async with factory() as session:
        await session.execute(
            sa.update(AnalysisJob)
            .where(AnalysisJob.id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


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
    assert job.claim_token is None


async def test_two_workers_cannot_claim_the_same_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    first, second = await asyncio.gather(
        claim(session_factory, "worker-a"), claim(session_factory, "worker-b")
    )

    claimed = [job for job in (first, second) if job is not None]
    assert len(claimed) == 1, "exactly one worker must win the job"
    assert claimed[0].status is JobStatus.running
    assert claimed[0].attempts == 1
    assert claimed[0].claim_token is not None


async def test_concurrent_workers_split_the_queue_without_duplication(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_ids = {await enqueue_job(session_factory, case_id, organization_id) for _ in range(12)}

    async def drain(worker: str) -> list[uuid.UUID]:
        claimed: list[uuid.UUID] = []
        while True:
            job = await claim(session_factory, worker)
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
    assert await claim(session_factory, "idle") is None


async def test_claim_respects_job_type_filter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id, job_type=JobType.RUN_AGENT)

    assert await claim(session_factory, "w", job_types=[JobType.GENERATE_PPS]) is None
    job = await claim(session_factory, "w", job_types=[JobType.RUN_AGENT])

    assert job is not None
    assert job.job_type == JobType.RUN_AGENT


async def test_empty_job_type_list_claims_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An empty list means no acceptable types — not 'every type'."""
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    assert await claim(session_factory, "w", job_types=[]) is None
    assert await claim(session_factory, "w") is not None


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

    assert await claim(session_factory, "w") is None


# --------------------------------------------------------------------------------------
# Leases and fencing
# --------------------------------------------------------------------------------------


async def test_expired_lease_makes_a_running_job_claimable_again(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=3)

    first = await claim(session_factory, "dying-worker")
    assert first is not None
    await expire_lease(session_factory, job_id)

    second = await claim(session_factory, "rescuer")

    assert second is not None
    assert second.id == job_id
    assert second.claimed_by == "rescuer"
    assert second.attempts == 2, "the reclaimed run counts as another attempt"
    assert second.claim_token != first.claim_token, "reclaiming mints a new fencing token"


async def test_exhausted_job_with_a_dead_lease_is_failed_not_reclaimed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=1)

    assert await claim(session_factory, "dying-worker") is not None
    await expire_lease(session_factory, job_id)

    assert await claim(session_factory, "rescuer") is None, "no attempts remain"

    async with session_factory() as session:
        reaped = await service.reap_expired_leases(session)
        await session.commit()

    assert reaped == 1
    failed = await read_job(session_factory, job_id)
    assert failed.status is JobStatus.failed
    assert failed.attempts == 1
    assert failed.finished_at is not None


async def test_a_running_job_that_holds_no_lease_is_recovered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A `running` row with no lease is unclaimable *and* unreapable, so it blocks forever.

    `claim_next` only reclaims a running job whose `lease_expires_at` is set and past, and
    `reap_expired_leases` requires the same. A row that reaches `running` without a lease
    therefore satisfies neither, while `start_analysis` and `start_panel` both count it as live
    -- so that `(case, role)` can never be analysed again.

    Found in the live database: **342 such rows across 118 cases**, the oldest from 17 August,
    all with `claimed_by`, `claim_token` and `lease_expires_at` null. They came from a
    measurement driver that marked jobs running itself rather than claiming them, which is not
    shipped code -- but the queue having no way back from that state is the product's own gap,
    and it blocked a panel measurement.

    Recovery is failure rather than requeueing: nothing ever ran, but silently re-running work
    abandoned weeks ago would spend real model time on questions nobody is waiting for. Failing
    it unblocks the case, and asking again is one request.
    """
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    # The shape the driver left behind: running, but never claimed by anyone.
    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        job.status = JobStatus.running
        job.claimed_by = None
        job.claim_token = None
        job.lease_expires_at = None
        await session.commit()

    assert await claim(session_factory, "rescuer") is None, "it cannot be claimed"

    async with session_factory() as session:
        reaped = await service.reap_expired_leases(session)
        await session.commit()

    assert reaped == 1, "the reaper must recover a running job that holds no lease"
    recovered = await read_job(session_factory, job_id)
    assert recovered.status is JobStatus.failed
    assert recovered.finished_at is not None
    assert "lease" in (recovered.error_message or "").lower()


async def test_a_healthy_running_job_is_left_alone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The guard on the fix: a job genuinely under way must not be reaped out from under it."""
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)
    assert await claim(session_factory, "worker") is not None

    async with session_factory() as session:
        reaped = await service.reap_expired_leases(session)
        await session.commit()

    assert reaped == 0
    assert (await read_job(session_factory, job_id)).status is JobStatus.running


async def test_a_finished_job_records_when_it_finished(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`finished_at` must be the moment the work ended, not the moment its transaction opened.

    PostgreSQL's `now()` is the *transaction* timestamp. A worker holds one transaction open for
    the whole handler -- claim, run the agent, record the outcome, commit -- so a fifty-four
    second agent run wrote `finished_at` equal to the instant the handler began. Observed in the
    end-to-end verification: every job reported `finished_at` within twenty milliseconds of
    `started_at`, including runs that demonstrably took a minute.

    Nothing downstream is corrupted -- the real duration is on `agent_runs` -- but every
    queue-level answer to *how long did that take* is wrong, and monitoring built on it would
    report a healthy queue while agents ran for minutes.
    """
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    # One session for the whole thing, which is what the worker does.
    async with session_factory() as session:
        claimed = await service.claim_next(session, worker="slow-worker")
        assert claimed is not None
        token = claimed.claim_token
        assert token is not None
        await session.commit()

    async with session_factory() as session:
        # Exactly what the worker does: read the row first -- which opens the transaction --
        # then run the handler, then record the outcome. `now()` is fixed at that first
        # statement, which is why a long handler wrote a `finished_at` from before it ran.
        assert await session.get(AnalysisJob, job_id) is not None
        await asyncio.sleep(1.1)
        await service.complete(session, job_id, token)
        await session.commit()

    job = await read_job(session_factory, job_id)
    assert job.started_at is not None and job.finished_at is not None
    elapsed = (job.finished_at - job.started_at).total_seconds()
    assert elapsed >= 1.0, (
        f"finished_at is {elapsed:.3f}s after started_at; the work took over a second, so the "
        "timestamp is recording the transaction rather than the moment"
    )


async def test_stale_worker_cannot_complete_a_reclaimed_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The fencing check: a worker that lost its lease must not overwrite the new run."""
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=3)

    stale = await claim(session_factory, "stale-worker")
    assert stale is not None and stale.claim_token is not None
    await expire_lease(session_factory, job_id)

    fresh = await claim(session_factory, "fresh-worker")
    assert fresh is not None and fresh.claim_token is not None

    async with session_factory() as session:
        with pytest.raises(service.JobTransitionError):
            await service.complete(session, job_id, stale.claim_token)
        await session.rollback()

    still_running = await read_job(session_factory, job_id)
    assert still_running.status is JobStatus.running
    assert still_running.claimed_by == "fresh-worker"

    async with session_factory() as session:
        await service.complete(session, job_id, fresh.claim_token)
        await session.commit()

    assert (await read_job(session_factory, job_id)).status is JobStatus.completed


async def test_stale_worker_cannot_fail_or_extend_a_reclaimed_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=3)

    stale = await claim(session_factory, "stale-worker")
    assert stale is not None and stale.claim_token is not None
    await expire_lease(session_factory, job_id)
    assert await claim(session_factory, "fresh-worker") is not None

    async with session_factory() as session:
        with pytest.raises(service.JobTransitionError):
            await service.fail(session, job_id, stale.claim_token, error="stale failure")
        await session.rollback()

        with pytest.raises(service.JobTransitionError):
            await service.extend_lease(session, job_id, stale.claim_token)
        await session.rollback()

    job = await read_job(session_factory, job_id)
    assert job.status is JobStatus.running
    assert job.error_message is None


async def test_live_lease_protects_a_running_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    assert await claim(session_factory, "busy-worker") is not None
    assert await claim(session_factory, "other") is None


async def test_extend_lease_pushes_the_deadline_out(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id)

    job = await claim(session_factory, "w", lease=timedelta(minutes=1))
    assert job is not None and job.claim_token is not None
    original = job.lease_expires_at

    async with session_factory() as session:
        await service.extend_lease(session, job.id, job.claim_token, lease=timedelta(minutes=30))
        await session.commit()

    refreshed = await read_job(session_factory, job.id)
    assert original is not None and refreshed.lease_expires_at is not None
    assert refreshed.lease_expires_at > original


# --------------------------------------------------------------------------------------
# Outcomes and transitions
# --------------------------------------------------------------------------------------


async def test_failure_is_retried_with_backoff_and_keeps_the_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    await enqueue_job(session_factory, case_id, organization_id, max_attempts=3)

    job = await claim(session_factory, "w")
    assert job is not None and job.claim_token is not None

    async with session_factory() as session:
        await service.fail(session, job.id, job.claim_token, error="LlamaParse timed out")
        await session.commit()

    retried = await read_job(session_factory, job.id)
    assert retried.status is JobStatus.queued
    assert retried.error_message == "LlamaParse timed out"
    assert retried.attempts == 1
    assert retried.claimed_by is None
    assert retried.claim_token is None
    assert retried.available_at > datetime.now(UTC), "backoff delays the retry"


async def test_failure_becomes_terminal_once_attempts_are_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=2)

    for attempt in (1, 2):
        async with session_factory() as session:
            # Backoff would otherwise hide the job from the next claim.
            await session.execute(
                sa.update(AnalysisJob).values(available_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()

        job = await claim(session_factory, "w")
        assert job is not None and job.claim_token is not None, f"attempt {attempt} claimable"
        async with session_factory() as session:
            await service.fail(session, job.id, job.claim_token, error=f"boom {attempt}")
            await session.commit()

    final = await read_job(session_factory, job_id)
    assert final.status is JobStatus.failed
    assert final.error_message == "boom 2"
    assert final.attempts == 2
    assert final.finished_at is not None

    assert await claim(session_factory, "w") is None, "an exhausted job is not retried"


async def test_completed_job_keeps_its_last_error_as_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=3)

    first = await claim(session_factory, "w")
    assert first is not None and first.claim_token is not None
    async with session_factory() as session:
        await service.fail(session, job_id, first.claim_token, error="transient network error")
        await session.commit()

    async with session_factory() as session:
        await session.execute(
            sa.update(AnalysisJob).values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    second = await claim(session_factory, "w")
    assert second is not None and second.claim_token is not None
    async with session_factory() as session:
        await service.complete(session, job_id, second.claim_token)
        await session.commit()

    finished = await read_job(session_factory, job_id)
    assert finished.status is JobStatus.completed
    assert finished.error_message == "transient network error", "attempt history survives"


async def test_completed_job_is_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    job = await claim(session_factory, "w")
    assert job is not None and job.claim_token is not None
    async with session_factory() as session:
        await service.complete(session, job_id, job.claim_token)
        await session.commit()

    finished = await read_job(session_factory, job_id)
    assert finished.status is JobStatus.completed
    assert finished.finished_at is not None
    assert await claim(session_factory, "w") is None


async def test_cancelled_job_cannot_then_be_completed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    job = await claim(session_factory, "w")
    assert job is not None and job.claim_token is not None

    async with session_factory() as session:
        await service.cancel(session, job_id, reason="Case withdrawn")
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(service.JobTransitionError):
            await service.complete(session, job_id, job.claim_token)
        await session.rollback()

    assert (await read_job(session_factory, job_id)).status is JobStatus.cancelled


async def test_terminal_jobs_cannot_be_resumed_or_cancelled_again(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    job = await claim(session_factory, "w")
    assert job is not None and job.claim_token is not None
    async with session_factory() as session:
        await service.complete(session, job_id, job.claim_token)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(service.JobTransitionError):
            await service.resume(session, job_id)
        await session.rollback()

        with pytest.raises(service.JobTransitionError):
            await service.cancel(session, job_id, reason="too late")
        await session.rollback()

    assert (await read_job(session_factory, job_id)).status is JobStatus.completed


async def test_job_can_park_waiting_for_a_human_and_resume(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    job = await claim(session_factory, "w")
    assert job is not None and job.claim_token is not None
    async with session_factory() as session:
        await service.wait_for_user(
            session, job_id, job.claim_token, reason="Clinical stage missing from dossier"
        )
        await session.commit()

    parked = await read_job(session_factory, job_id)
    assert parked.status is JobStatus.waiting_for_user
    assert parked.error_message == "Clinical stage missing from dossier"
    assert await claim(session_factory, "w") is None

    async with session_factory() as session:
        await service.resume(session, job_id)
        await session.commit()

    resumed = await claim(session_factory, "w")
    assert resumed is not None and resumed.id == job_id


async def test_cancelled_job_is_not_claimable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    async with session_factory() as session:
        await service.cancel(session, job_id, reason="Case withdrawn")
        await session.commit()

    assert await claim(session_factory, "w") is None

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

    assert processed == job_id
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


async def test_worker_applies_a_waiting_outcome_returned_by_a_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    async def handler(session: AsyncSession, job: AnalysisJob) -> JobOutcome:
        return JobOutcome(status=JobStatus.waiting_for_user, error_message="Needs a human")

    worker = Worker(session_factory, name="test-worker")
    worker.register(JobType.PARSE_DOSSIER, handler)

    await worker.run_once()

    parked = await read_job(session_factory, job_id)
    assert parked.status is JobStatus.waiting_for_user
    assert parked.error_message == "Needs a human"


async def test_worker_applies_a_failure_outcome_returned_by_a_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=1)

    async def handler(session: AsyncSession, job: AnalysisJob) -> JobOutcome:
        return JobOutcome(status=JobStatus.failed, error_message="dossier unreadable")

    worker = Worker(session_factory, name="test-worker")
    worker.register(JobType.PARSE_DOSSIER, handler)

    await worker.run_once()

    failed = await read_job(session_factory, job_id)
    assert failed.status is JobStatus.failed
    assert failed.error_message == "dossier unreadable"


async def test_worker_heartbeat_keeps_the_lease_alive_during_a_slow_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The heartbeat runs on its own session, so the renewal is visible while the
    handler's transaction is still open."""
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    seen: list[datetime] = []

    async def slow_handler(session: AsyncSession, job: AnalysisJob) -> None:
        for _ in range(3):
            await asyncio.sleep(0.15)
            # Read the lease from a different connection than the handler's.
            async with session_factory() as observer:
                current = await observer.get(AnalysisJob, job_id)
                assert current is not None and current.lease_expires_at is not None
                seen.append(current.lease_expires_at)

    worker = Worker(
        session_factory,
        name="test-worker",
        lease=timedelta(seconds=30),
        heartbeat_interval=0.1,
    )
    worker.register(JobType.PARSE_DOSSIER, slow_handler)

    await worker.run_once()

    assert len(seen) == 3
    assert seen[-1] > seen[0], "the lease deadline moved while the handler was working"
    assert (await read_job(session_factory, job_id)).status is JobStatus.completed


async def test_worker_reaps_exhausted_expired_jobs_while_polling(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id, max_attempts=1)

    assert await claim(session_factory, "dying-worker") is not None
    await expire_lease(session_factory, job_id)

    worker = Worker(session_factory, name="test-worker")
    assert await worker.run_once() is None

    assert (await read_job(session_factory, job_id)).status is JobStatus.failed


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


# --------------------------------------------------------------------------------------
# Reporting progress
# --------------------------------------------------------------------------------------


async def test_a_milestone_is_visible_while_the_job_is_still_running(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """**The property the whole design turns on.**

    A handler holds one transaction open for its entire run, so a milestone written on the
    handler's session would only become visible when the job ended -- at which point it is
    a claim about work that is already over. The reporter uses its own session, and this
    reads the row from a third connection while the handler is still working.
    """
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    seen: list[str | None] = []

    async def slow_handler(session: AsyncSession, job: AnalysisJob) -> None:
        assert job.claim_token is not None
        report = MilestoneReporter(session_factory, job.id, job.claim_token)
        for step in (RunStep.BUNDLE, RunStep.REASONING):
            await report(step)
            async with session_factory() as observer:
                current = await observer.get(AnalysisJob, job_id)
                assert current is not None
                seen.append(current.milestone)

    worker = Worker(session_factory, name="test-worker")
    worker.register(JobType.PARSE_DOSSIER, slow_handler)

    await worker.run_once()

    assert seen == [RunStep.BUNDLE, RunStep.REASONING]
    finished = await read_job(session_factory, job_id)
    assert finished.status is JobStatus.completed
    assert finished.milestone_at is not None


async def test_a_stale_worker_cannot_report_progress_on_a_reclaimed_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Fenced like every other write. A worker that lost its lease reporting `bundle` over
    the run that replaced it would show the newer run going backwards."""
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    stale = await claim(session_factory, "worker-a")
    assert stale is not None and stale.claim_token is not None
    await expire_lease(session_factory, job_id)
    fresh = await claim(session_factory, "worker-b")
    assert fresh is not None and fresh.claim_token is not None

    async with session_factory() as session:
        await service.record_milestone(session, job_id, fresh.claim_token, RunStep.REASONING)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(service.JobTransitionError):
            await service.record_milestone(session, job_id, stale.claim_token, RunStep.BUNDLE)

    assert (await read_job(session_factory, job_id)).milestone == RunStep.REASONING


async def test_a_lost_claim_costs_the_progress_report_and_nothing_else(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The reporter swallows what the service refuses.

    Progress is cosmetic and an opinion is not: a run that can no longer report where it
    is must still finish, archive its transcript and store its answer.
    """
    case_id, organization_id = await make_case(session_factory)
    job_id = await enqueue_job(session_factory, case_id, organization_id)

    job = await claim(session_factory, "worker-a")
    assert job is not None
    await expire_lease(session_factory, job_id)
    assert await claim(session_factory, "worker-b") is not None

    assert job.claim_token is not None
    report = MilestoneReporter(session_factory, job_id, job.claim_token)

    await report(RunStep.BUNDLE)  # must not raise

    assert (await read_job(session_factory, job_id)).milestone is None


async def test_every_agent_step_is_short_enough_for_the_column() -> None:
    """The steps reach the database as text in a bounded column, and they are constants:
    a step too long to store would fail at the one moment nobody is watching."""
    assert all(len(step) <= 50 for step in AGENT_STEPS)
