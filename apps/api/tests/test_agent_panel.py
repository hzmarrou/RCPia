"""Running specialists concurrently, and starting the synthesis exactly once when they end.

The properties under test are the ones a fiche depends on and a reader cannot check:

- a panel analysed **one** version of the patient, not a mixture of two,
- the synthesis read **that panel's** opinions, not whatever the case has accumulated,
- it started **once**, and only after every speciality the panel named had settled,
- and a speciality that produced nothing is reported as such rather than dropped.

Concurrency is exercised through real `Worker` instances against the real queue, because the
guarantee being tested belongs to PostgreSQL's `FOR UPDATE SKIP LOCKED` and not to any code
here: a test that simulated the claim would prove nothing about the thing that has to hold.
"""

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.agents import panel as panel_service
from rcpia.agents.opinions import prior_opinions
from rcpia.agents.schemas import SYNTHESIS_ROLE
from rcpia.cases.ledger import latest_version
from rcpia.core.db import get_session
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    AnalysisPanelDispatch,
    AnalysisPanelJob,
    Case,
    JobStatus,
)
from rcpia.jobs.models import JobType
from rcpia.jobs.worker import Worker
from rcpia.main import app
from tests.test_agent_runs import seed_case, sign_in

ROLES = ("urology_oncology", "medical_oncology", "radiation_oncology")


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]):
    """The app wired to this test's database, so a request and an assertion see one world."""

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


async def _version(session: AsyncSession, case: Case) -> int:
    version = await latest_version(session, case_id=case.id)
    assert version is not None
    return version.version


async def _start(
    factory: async_sessionmaker[AsyncSession],
    case: Case,
    *,
    roles=ROLES,
    ledger_version: int | None = None,
    question: str | None = None,
):
    async with factory() as session:
        stored = await session.get(Case, case.id)
        assert stored is not None
        version = ledger_version or await _version(session, stored)
        started = await panel_service.start_panel(
            session,
            case_id=stored.id,
            organization_id=stored.organization_id,
            roles=list(roles),
            ledger_version=version,
            clinical_question=question,
        )
        panel_id = started.panel.id
        jobs = {role: job.id for role, job in started.jobs.items()}
        reused = set(started.reused)
        await session.commit()
    return panel_id, jobs, reused


# --- Concurrency ------------------------------------------------------------------------------


async def test_separate_workers_claim_different_jobs_each_exactly_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The premise of the whole design: three specialists, three workers, three claims.

    If the queue handed the same job to two workers the panel would run one speciality twice
    and never run another, and the synthesis would report a convergence between an agent and
    itself.
    """
    _, case = await seed_case(session_factory)
    _, jobs, _ = await _start(session_factory, case)

    claimed: list[uuid.UUID | None] = []

    async def claim(name: str) -> None:
        worker = Worker(session_factory, name=name, job_types=[JobType.RUN_AGENT])
        # No handler is registered, so the worker claims and then records "unknown type" --
        # which is exactly the part under test: the claim, not the work.
        claimed.append(await worker.run_once())

    await asyncio.gather(*(claim(f"worker-{n}") for n in range(3)))

    got = [job_id for job_id in claimed if job_id is not None]
    assert len(got) == 3, "three workers should have claimed three jobs"
    assert len(set(got)) == 3, "no job may be claimed twice"
    assert set(got) == set(jobs.values())


# --- Version-safe reuse -----------------------------------------------------------------------


async def test_every_job_in_a_panel_is_pinned_to_the_same_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)

    async with session_factory() as session:
        rows = await session.execute(
            sa.select(AnalysisJob).where(AnalysisJob.id.in_(jobs.values()))
        )
        pinned = {job.payload["ledger_version"] for job in rows.scalars().all()}
        panel = await session.get(panel_service.AnalysisPanel, panel_id)
        assert panel is not None
    assert len(pinned) == 1, f"a panel must pin one version, got {pinned}"
    assert pinned == {panel.ledger_version}


async def test_a_live_job_on_an_older_version_is_not_reused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The hole the plain dedupe key leaves.

    `run_agent:{case}:{role}` names the case and the speciality and says nothing about which
    version of the patient is being analysed. A correction between two panels creates a new
    version, and reusing the older run would let the second panel report that it analysed data
    the agent never read.
    """
    _, case = await seed_case(session_factory)
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        assert stored is not None
        version = await _version(session, stored)

    await _start(session_factory, case, ledger_version=version)

    with pytest.raises(panel_service.PanelConflict) as conflict:
        await _start(session_factory, case, ledger_version=version + 1)

    assert set(conflict.value.roles) == set(ROLES)
    assert str(version) in conflict.value.detail


async def test_a_live_job_asking_a_different_question_is_not_reused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same version, different question, so the running agent is answering something else."""
    _, case = await seed_case(session_factory)
    await _start(session_factory, case, question="Le patient est-il opérable ?")

    with pytest.raises(panel_service.PanelConflict):
        await _start(session_factory, case, question="Faut-il une chimiothérapie ?")


async def test_two_compatible_panels_share_one_specialist_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same case, same version, same question — so the same opinion answers both.

    Running the agent again would spend a Pi run to produce a second copy of an answer already
    held, and the two panels would then have to be told which copy is theirs.
    """
    _, case = await seed_case(session_factory)
    first_panel, first_jobs, _ = await _start(session_factory, case, question="  Opérable ? ")
    second_panel, second_jobs, reused = await _start(
        session_factory, case, question="opérable ?"
    )

    assert first_panel != second_panel
    assert first_jobs == second_jobs, "a compatible panel must reuse the running jobs"
    assert reused == set(ROLES)

    async with session_factory() as session:
        live = await session.execute(
            sa.select(sa.func.count())
            .select_from(AnalysisJob)
            .where(
                AnalysisJob.case_id == case.id,
                AnalysisJob.job_type == JobType.RUN_AGENT,
                AnalysisJob.status.in_(panel_service.LIVE_JOB_STATUSES),
            )
        )
        assert live.scalar_one() == len(ROLES), "no duplicate execution"

        # Both panels still record which job each of them relies on.
        for panel_id in (first_panel, second_panel):
            rows = await session.execute(
                sa.select(AnalysisPanelJob).where(AnalysisPanelJob.panel_id == panel_id)
            )
            assert {m.agent_role for m in rows.scalars().all()} == set(ROLES)


async def test_the_synthesis_is_not_a_specialist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, case = await seed_case(session_factory)
    with pytest.raises(ValueError, match=SYNTHESIS_ROLE):
        await _start(session_factory, case, roles=("urology_oncology", SYNTHESIS_ROLE))


# --- The synthesis gate -------------------------------------------------------------------


async def _settle(
    factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    *,
    status: JobStatus = JobStatus.completed,
    with_run: bool = True,
) -> None:
    """Bring one specialist job to a terminal state, with or without a usable opinion."""
    async with factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        job.status = status
        if with_run:
            # `ck_agent_runs_ledger_version_reference_is_complete` requires the row id beside
            # the number: a run that named a version without pointing at it could claim to
            # have read one that never existed.
            version = await latest_version(session, case_id=job.case_id)
            assert version is not None
            session.add(
                AgentRun(
                    case_id=job.case_id,
                    organization_id=job.organization_id,
                    agent_role=job.payload["agent_role"],
                    status=AgentRunStatus.succeeded,
                    job_id=job.id,
                    ledger_version_id=version.id,
                    ledger_version=version.version,
                    provider="azure-openai-responses",
                    model="gpt-5.6-sol",
                    session_blob_key=f"cases/{job.case_id}/sessions/{job.id}.jsonl",
                    bundle={"inputs": {"case_id": str(job.case_id)}},
                    output={
                        "agent_role": job.payload["agent_role"],
                        "case_id": str(job.case_id),
                        "recommendation": "Cystectomie radicale après évaluation.",
                        "rationale": "Motif.",
                        "evidence_references": [
                            {
                                "id": "afu.bladder-tvim.2024-2026.standard.01.0107",
                                "supports": "Le standard.",
                            }
                        ],
                    },
                )
            )
        await session.commit()


async def _dispatch(factory: async_sessionmaker[AsyncSession], panel_id: uuid.UUID):
    async with factory() as session:
        job = await panel_service.dispatch_synthesis_if_ready(session, panel_id=panel_id)
        await session.commit()
        return job.id if job is not None else None


async def test_the_synthesis_waits_for_every_requested_speciality(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)

    for role in ROLES[:-1]:
        await _settle(session_factory, jobs[role])
        assert await _dispatch(session_factory, panel_id) is None, (
            f"synthesis dispatched while {ROLES[-1]} was still running"
        )

    await _settle(session_factory, jobs[ROLES[-1]])
    assert await _dispatch(session_factory, panel_id) is not None


async def test_a_retrying_specialist_is_not_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A failed attempt with budget left goes back to `queued`, so the opinion is still coming.

    Reading that as terminal would convene the board without an answer that was minutes away —
    the one failure mode that produces a confidently incomplete fiche.
    """
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)

    for role in ROLES[:-1]:
        await _settle(session_factory, jobs[role])
    # The third failed once and is queued for another attempt.
    await _settle(
        session_factory, jobs[ROLES[-1]], status=JobStatus.queued, with_run=False
    )
    assert await _dispatch(session_factory, panel_id) is None

    await _settle(session_factory, jobs[ROLES[-1]])
    assert await _dispatch(session_factory, panel_id) is not None


async def test_simultaneous_completions_dispatch_exactly_one_synthesis(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two specialists finishing at the same instant, each the other's last.

    The row lock decides which evaluation goes first; the unique constraint decides the
    outcome. Both are needed: without the lock neither sees the other and no synthesis is ever
    dispatched, which loses a fiche rather than duplicating one.
    """
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)
    for job_id in jobs.values():
        await _settle(session_factory, job_id)

    results = await asyncio.gather(*(_dispatch(session_factory, panel_id) for _ in range(4)))
    dispatched = [job_id for job_id in results if job_id is not None]
    assert len(dispatched) == 1, f"expected one synthesis, got {dispatched}"

    async with session_factory() as session:
        rows = await session.execute(
            sa.select(AnalysisPanelDispatch).where(
                AnalysisPanelDispatch.panel_id == panel_id
            )
        )
        records = rows.scalars().all()
        assert len(records) == 1
        assert records[0].dispatch_kind == AnalysisPanelDispatch.SYNTHESIS
        assert records[0].job_id == dispatched[0]


async def test_a_failed_specialist_reaches_the_synthesis_as_explicitly_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Never a silently complete panel.

    The synthesis is handed the names of the specialities that were asked for and produced
    nothing, because it sees answers and never the request: an absent speciality is otherwise
    indistinguishable from one nobody convened.
    """
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)

    await _settle(session_factory, jobs["urology_oncology"])
    await _settle(session_factory, jobs["medical_oncology"])
    await _settle(
        session_factory, jobs["radiation_oncology"], status=JobStatus.failed, with_run=False
    )

    synthesis_job_id = await _dispatch(session_factory, panel_id)
    assert synthesis_job_id is not None

    async with session_factory() as session:
        job = await session.get(AnalysisJob, synthesis_job_id)
        assert job is not None
        assert job.payload["agent_role"] == SYNTHESIS_ROLE
        assert job.payload["panel_id"] == str(panel_id)
        assert job.payload["missing_roles"] == ["radiation_oncology"]
        assert set(job.payload["requested_roles"]) == set(ROLES)

        state = await panel_service.panel_state(session, panel_id=panel_id)
        assert state.complete
        assert state.failed == frozenset({"radiation_oncology"})


# --- Panel-scoped reading ---------------------------------------------------------------------


async def test_the_synthesis_reads_its_own_panel_and_not_the_cases_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The defect the case-wide query leaves behind.

    A case analysed twice holds two generations of opinions. Reading the newest per speciality
    across the case hands the second panel the first panel's opinion for any speciality that
    did not re-run — and the fiche then reports agreement between two agents that read two
    different versions of the patient.
    """
    _, case = await seed_case(session_factory)
    first_panel, first_jobs, _ = await _start(session_factory, case)
    for job_id in first_jobs.values():
        await _settle(session_factory, job_id)

    # A correction lands; the next panel runs against a newer version.
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        assert stored is not None
        version = await _version(session, stored)
    second_panel, second_jobs, _ = await _start(
        session_factory, case, roles=("urology_oncology",), ledger_version=version + 1
    )
    await _settle(session_factory, second_jobs["urology_oncology"])

    async with session_factory() as session:
        scoped, _, notes = await prior_opinions(
            session, case_id=case.id, panel_id=second_panel
        )
        case_wide, _, _ = await prior_opinions(session, case_id=case.id)

    assert {opinion.agent_role for opinion in scoped} == {"urology_oncology"}
    assert {opinion.agent_role for opinion in case_wide} == set(ROLES), (
        "the case-wide read is what would leak the earlier panel"
    )
    assert notes, "the synthesis is told how many specialities it is reading"


async def test_missing_roles_are_stated_in_the_synthesis_constraints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, case = await seed_case(session_factory)
    panel_id, jobs, _ = await _start(session_factory, case)
    await _settle(session_factory, jobs["urology_oncology"])

    async with session_factory() as session:
        _, _, notes = await prior_opinions(
            session,
            case_id=case.id,
            panel_id=panel_id,
            missing_roles=["radiation_oncology"],
        )
    joined = " ".join(notes)
    assert "Radiothérapie" in joined
    assert "incomplet" in joined


# --- The endpoint -----------------------------------------------------------------------------


async def test_the_endpoint_queues_every_speciality_on_one_version(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/analysis/panel", json={"roles": list(ROLES)}
    )

    assert response.status_code == 202
    body = response.json()
    assert {job["agent_role"] for job in body["jobs"]} == set(ROLES)
    assert not any(job["reused"] for job in body["jobs"])

    async with session_factory() as session:
        rows = await session.execute(
            sa.select(AnalysisJob).where(
                AnalysisJob.id.in_([uuid.UUID(job["job_id"]) for job in body["jobs"]])
            )
        )
        jobs = rows.scalars().all()
    assert len(jobs) == len(ROLES)
    assert {job.payload["ledger_version"] for job in jobs} == {body["ledger_version"]}


async def test_repeated_panel_requests_do_not_duplicate_a_live_job(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Twelve at once, because the check-then-insert is a fast path and the index is the rule.

    A case that quietly bought a dozen opinions on the same question would also be paying for
    twelve Pi runs and holding twelve of the provider's concurrent slots.
    """
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    responses = await asyncio.gather(
        *(
            client.post(f"/cases/{case.id}/analysis/panel", json={"roles": list(ROLES)})
            for _ in range(12)
        )
    )
    accepted = [r for r in responses if r.status_code == 202]
    assert accepted, "at least one request must succeed"
    assert {r.status_code for r in responses} <= {202, 409}

    async with session_factory() as session:
        live = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AnalysisJob)
            .where(
                AnalysisJob.case_id == case.id,
                AnalysisJob.job_type == JobType.RUN_AGENT,
                AnalysisJob.status.in_(panel_service.LIVE_JOB_STATUSES),
            )
        )
    assert live == len(ROLES), f"expected {len(ROLES)} live jobs, found {live}"


async def test_the_endpoint_refuses_the_synthesis_and_unknown_specialities(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    refused = await client.post(
        f"/cases/{case.id}/analysis/panel",
        json={"roles": ["urology_oncology", SYNTHESIS_ROLE]},
    )
    assert refused.status_code == 422

    unknown = await client.post(
        f"/cases/{case.id}/analysis/panel", json={"roles": ["homeopathy"]}
    )
    assert unknown.status_code == 422

    async with session_factory() as session:
        queued = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AnalysisJob)
            .where(
                AnalysisJob.case_id == case.id, AnalysisJob.job_type == JobType.RUN_AGENT
            )
        )
    assert queued == 0, "a refused panel must queue nothing at all"
