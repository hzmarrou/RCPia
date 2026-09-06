"""Lifecycle assertions through the real API, queue, and deployed worker wiring."""

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia import worker as worker_module
from rcpia.cases.ledger import latest_version, record_version
from rcpia.cases.lifecycle import refresh_analysis_status
from rcpia.core.models import AgentRun, AgentRunStatus, AnalysisJob, Case, CaseStatus, JobStatus
from rcpia.jobs.models import JobOutcome, JobType
from rcpia.jobs.service import JobTransitionError, cancel, claim_next
from tests.test_agent_panel import ROLES, _dispatch, _settle, _start
from tests.test_agent_panel import client as client
from tests.test_agent_runs import seed_case, sign_in


@pytest.fixture
def runtime(monkeypatch, session_factory):
    """Replace external providers, not the queue, lifecycle, or worker callbacks."""
    for name in ("get_parser", "get_blob_store", "get_pi_adapter"):
        monkeypatch.setattr(worker_module, name, lambda: object())
    for name in ("build_embedder", "build_reranker"):
        monkeypatch.setattr(worker_module, name, lambda settings: object())
    monkeypatch.setattr(worker_module, "get_session_factory", lambda: session_factory)
    # seed_case performs extraction directly but leaves its queue row unclaimed.
    monkeypatch.setenv("WORKER_JOB_TYPES", JobType.RUN_AGENT)

    async def succeed(session, job, **kwargs):
        version = await latest_version(session, case_id=job.case_id)
        assert version is not None
        session.add(
            AgentRun(
                case_id=job.case_id,
                organization_id=job.organization_id,
                agent_role=job.payload["agent_role"],
                job_id=job.id,
                status=AgentRunStatus.succeeded,
                ledger_version_id=version.id,
                ledger_version=version.version,
                provider="synthetic",
                model="synthetic",
                session_blob_key=f"synthetic/{job.id}.jsonl",
                bundle={"inputs": {"case_id": str(job.case_id)}},
                output={"recommendation": "Synthetic answer", "rationale": "Lifecycle fixture"},
            )
        )
        await session.flush()

    monkeypatch.setattr(worker_module, "run_agent", succeed)
    return worker_module.build_worker


async def _status(factory, case_id):
    async with factory() as session:
        return await session.scalar(sa.select(Case.status).where(Case.id == case_id))


async def _reconcile(factory, case_id):
    async with factory() as session:
        result = await refresh_analysis_status(session, case_id=case_id)
        await session.commit()
        return result


async def _awaiting_synthesis(factory):
    user, case = await seed_case(factory)
    panel_id, jobs, _ = await _start(factory, case)
    for job_id in jobs.values():
        await _settle(factory, job_id)
    synthesis_id = await _dispatch(factory, panel_id)
    assert synthesis_id is not None
    return user, case, synthesis_id


async def test_api_and_real_workers_persist_both_transitions(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], runtime
):
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    assert (await client.get(f"/cases/{case.id}")).json()["status"] == "ready_for_review"

    response = await client.post(f"/cases/{case.id}/analysis/panel", json={"roles": list(ROLES)})
    assert response.status_code == 202
    assert (await client.get(f"/cases/{case.id}")).json()["status"] == "analyzing"

    workers = [runtime() for _ in ROLES]
    claimed = await asyncio.gather(*(worker.run_once() for worker in workers))
    async with session_factory() as session:
        jobs = (await session.scalars(
            sa.select(AnalysisJob).where(AnalysisJob.id.in_(claimed))
        )).all()
        outcomes = [(job.status, job.error_message) for job in jobs]
        assert len(jobs) == len(ROLES) and all(
            status is JobStatus.completed for status, _ in outcomes
        ), outcomes
    assert await _status(session_factory, case.id) is CaseStatus.analyzing

    assert await workers[0].run_once() is not None
    assert (await client.get(f"/cases/{case.id}")).json()["status"] == "completed"
    listed = (await client.get("/cases")).json()
    assert next(row for row in listed if row["id"] == str(case.id))["status"] == "completed"


@pytest.mark.parametrize(
    "outcome",
    [
        JobOutcome(JobStatus.failed, "Synthetic terminal failure"),
        JobOutcome(JobStatus.cancelled, "Synthetic cancellation"),
        None,
    ],
    ids=["failed", "cancelled", "completed-without-output"],
)
async def test_terminal_synthesis_without_an_answer_is_blocked(
    session_factory, runtime, monkeypatch, outcome
):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)

    async def no_answer(session, job, **kwargs):
        return outcome

    monkeypatch.setattr(worker_module, "run_agent", no_answer)
    assert await runtime().run_once() == synthesis_id
    assert await _status(session_factory, case.id) is CaseStatus.blocked


async def test_retryable_synthesis_failure_stays_analyzing(session_factory, runtime, monkeypatch):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)

    async def retry(session, job, **kwargs):
        return JobOutcome(JobStatus.failed, "Synthetic retry", retryable=True)

    monkeypatch.setattr(worker_module, "run_agent", retry)
    assert await runtime().run_once() == synthesis_id
    assert await _reconcile(session_factory, case.id) is CaseStatus.analyzing
    async with session_factory() as session:
        job = await session.get(AnalysisJob, synthesis_id)
        assert job.status is JobStatus.queued


async def test_worker_reconciles_a_crash_after_the_last_attempt(
    session_factory, runtime, monkeypatch
):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)
    async with session_factory() as session:
        job = await session.get(AnalysisJob, synthesis_id)
        job.max_attempts = 1
        await session.commit()

    async def crash(session, job, **kwargs):
        raise RuntimeError("Synthetic process failure")

    monkeypatch.setattr(worker_module, "run_agent", crash)
    worker = runtime()
    assert await worker.run_once() == synthesis_id
    assert await worker.run_once() is None
    assert await _status(session_factory, case.id) is CaseStatus.blocked


async def test_worker_reconciles_an_exhausted_lease(session_factory, runtime):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)
    async with session_factory() as session:
        job = await session.get(AnalysisJob, synthesis_id)
        job.max_attempts = 1
        await session.flush()
        claimed = await claim_next(
            session, worker="expired-worker", lease=timedelta(seconds=-1),
            job_types=[JobType.RUN_AGENT],
        )
        assert claimed.id == synthesis_id
        await session.commit()

    assert await runtime().run_once() is None
    assert await _status(session_factory, case.id) is CaseStatus.blocked


async def test_lost_claim_rolls_back_the_case_completion_and_agent_run(
    session_factory, runtime, monkeypatch
):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)
    succeed = worker_module.run_agent

    async def cancelled_during_run(session, job, **kwargs):
        async with session_factory() as other:
            await cancel(other, job.id, reason="Synthetic cancellation while running")
            await other.commit()
        await succeed(session, job, **kwargs)

    monkeypatch.setattr(worker_module, "run_agent", cancelled_during_run)
    worker = runtime()
    with pytest.raises(JobTransitionError):
        await worker.run_once()
    assert await _status(session_factory, case.id) is CaseStatus.analyzing
    async with session_factory() as session:
        recorded = await session.scalar(
            sa.select(sa.func.count()).select_from(AgentRun).where(
                AgentRun.job_id == synthesis_id
            )
        )
        assert recorded == 0
    assert await worker.run_once() is None
    assert await _status(session_factory, case.id) is CaseStatus.blocked


async def test_a_new_panel_clears_the_previous_terminal_status(session_factory, runtime):
    _, case, _ = await _awaiting_synthesis(session_factory)
    await runtime().run_once()
    assert await _status(session_factory, case.id) is CaseStatus.completed
    await _start(session_factory, case)
    assert await _status(session_factory, case.id) is CaseStatus.analyzing


async def test_housekeeping_repairs_historical_status_and_does_not_touch_its_timestamp_again(
    session_factory, runtime
):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)
    await _settle(session_factory, synthesis_id)
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        stored.status = CaseStatus.ready_for_review
        await session.commit()

    worker = runtime()
    assert await worker.run_once() is None
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        assert stored.status is CaseStatus.completed
        updated_at = stored.updated_at
    assert await worker.run_once() is None
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        assert stored.updated_at == updated_at


async def test_old_success_cannot_override_a_newer_failed_panel(session_factory):
    _, case = await seed_case(session_factory)
    first, first_jobs, _ = await _start(session_factory, case, roles=[ROLES[0]])
    second, second_jobs, _ = await _start(session_factory, case, roles=[ROLES[1]])
    for job_id in [*first_jobs.values(), *second_jobs.values()]:
        await _settle(session_factory, job_id)
    first_synthesis = await _dispatch(session_factory, first)
    second_synthesis = await _dispatch(session_factory, second)
    await _settle(session_factory, second_synthesis, status=JobStatus.failed, with_run=False)
    assert await _reconcile(session_factory, case.id) is CaseStatus.analyzing
    await _settle(session_factory, first_synthesis)
    assert await _reconcile(session_factory, case.id) is CaseStatus.blocked


async def test_shared_synthesis_completes_both_compatible_panels(session_factory):
    _, case = await seed_case(session_factory)
    first, jobs, _ = await _start(session_factory, case)
    second, _, _ = await _start(session_factory, case)
    for job_id in jobs.values():
        await _settle(session_factory, job_id)
    first_synthesis = await _dispatch(session_factory, first)
    assert await _dispatch(session_factory, second) == first_synthesis
    await _settle(session_factory, first_synthesis)
    assert await _reconcile(session_factory, case.id) is CaseStatus.completed


async def test_missing_specialist_does_not_change_the_existing_synthesis_policy(
    session_factory, runtime
):
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)
    for role, job_id in jobs.items():
        await _settle(
            session_factory, job_id,
            status=JobStatus.failed if role == ROLES[0] else JobStatus.completed,
            with_run=role != ROLES[0],
        )
    synthesis_id = await _dispatch(session_factory, panel_id)
    assert await runtime().run_once() == synthesis_id
    assert await _status(session_factory, case.id) is CaseStatus.completed


@pytest.mark.parametrize("finished", [False, True])
async def test_correction_returns_to_review_and_old_completion_cannot_undo_it(
    client, session_factory, finished
):
    user, case, synthesis_id = await _awaiting_synthesis(session_factory)
    if finished:
        await _settle(session_factory, synthesis_id)
        assert await _reconcile(session_factory, case.id) is CaseStatus.completed
    await sign_in(client, user)
    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "12", "note": "Synthetic correction"},
    )
    assert response.status_code == 201
    assert (await client.get(f"/cases/{case.id}")).json()["status"] == "ready_for_review"
    if not finished:
        await _settle(session_factory, synthesis_id)
    assert await _reconcile(session_factory, case.id) is CaseStatus.ready_for_review


async def test_reconciliation_does_not_overwrite_a_new_upload(session_factory):
    _, case, synthesis_id = await _awaiting_synthesis(session_factory)
    await _settle(session_factory, synthesis_id)
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        stored.status = CaseStatus.parsing
        await record_version(session, case=stored, change_reason="Synthetic extraction")
        await session.commit()
    assert await _reconcile(session_factory, case.id) is CaseStatus.parsing


async def test_a_clinical_gap_still_refuses_the_panel_without_using_blocked(
    client, session_factory
):
    user, case = await seed_case(session_factory, text="**Age** : 70 ans\n")
    await sign_in(client, user)
    response = await client.post(f"/cases/{case.id}/analysis/panel", json={"roles": list(ROLES)})
    assert response.status_code == 409
    assert await _status(session_factory, case.id) is CaseStatus.ready_for_review
