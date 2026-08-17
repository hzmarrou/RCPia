"""Running a specialist agent against a real case.

The properties that carry clinical weight here:

- the bundle is built from a **stored** ledger version, so an opinion stays attached to
  what justified it;
- what the agent says is checked against what it was given, and a fabricated citation is
  refused rather than stored;
- a failed attempt is recorded, because "the agent was asked and could not answer" is
  something a reviewer needs to see;
- an opinion, once recorded, cannot be edited into a different opinion.
"""

import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.agents.bundle import build_bundle
from rcpia.agents.pi_adapter import PiAdapter
from rcpia.agents.service import run_agent
from rcpia.auth import security, throttle
from rcpia.cases.facts import extract_case_facts
from rcpia.cases.ledger import apply_correction, latest_version, record_version
from rcpia.core.db import get_session
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    AuditEvent,
    BlockType,
    CancerType,
    Case,
    CaseLedgerVersion,
    CaseStatus,
    DocumentChunk,
    JobStatus,
    Organization,
    ParseStatus,
    UploadedDocument,
    User,
    UserRole,
)
from rcpia.jobs.models import JobType
from rcpia.main import app
from rcpia.storage.keys import pi_session_key
from rcpia.storage.local import LocalBlobStore

FAKE_PI = Path(__file__).parent / "fake_pi.py"
PASSWORD = "correct-horse-battery-staple"

# A dossier complete enough to analyse: histology and muscle invasion are the two
# fields a bladder case cannot be reasoned about without.
DOSSIER_TEXT = """**Sexe** : Femme
**Age** : 85 ans
**Antécédents familiaux** : RAS
Créatinine : 17.33
Hb : 11.6
**Type histologique** : Carcinome urothélial
**pTNM** : p T2
**Grade** : Haut
**Infiltration du muscle** : Oui
"""

# The same case with a second document contradicting the creatinine.
ADDENDUM_TEXT = "Créatinine : 173.3\n"


@pytest.fixture(autouse=True)
def reset_throttle():
    throttle.reset_all()
    yield
    throttle.reset_all()


@pytest.fixture
def agents_root(tmp_path: Path) -> Path:
    """A minimal agent folder: brain.md plus the file that makes Pi read it."""
    agent = tmp_path / "agents" / "urology_oncology"
    (agent / ".pi").mkdir(parents=True)
    (agent / "brain.md").write_text("# Agent d'urologie", encoding="utf-8")
    (agent / ".pi" / "APPEND_SYSTEM.md").write_text("Lisez brain.md.", encoding="utf-8")
    (agent / ".pi" / "settings.json").write_text(
        '{"defaultProvider": "azure-openai-responses", "defaultModel": "gpt-5.6-sol"}',
        encoding="utf-8",
    )
    return tmp_path / "agents"


@pytest.fixture
def adapter(agents_root: Path, tmp_path: Path) -> PiAdapter:
    return PiAdapter(
        agents_root=agents_root,
        work_root=tmp_path / "work",
        api_key="test-key",
        base_url="https://rcpia.services.ai.azure.com/openai/v1",
        pi_command=(sys.executable, str(FAKE_PI)),
        timeout_seconds=30.0,
    )


@pytest.fixture
def blobs(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs", signing_secret="test-secret")


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]):
    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


async def seed_case(
    factory: async_sessionmaker[AsyncSession],
    *,
    text: str = DOSSIER_TEXT,
    addendum: str | None = None,
) -> tuple[User, Case]:
    """A parsed, extracted, versioned bladder case — the state an analysis starts from."""
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
        session.add(organization)
        await session.flush()

        user = User(
            organization_id=organization.id,
            email=f"user-{uuid.uuid4().hex[:8]}@example.org",
            role=UserRole.uploader,
            password_hash=security.hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        case = Case(
            organization_id=organization.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
            status=CaseStatus.ready_for_review,
        )
        session.add(case)
        await session.flush()

        for ordinal, body in enumerate([text, addendum] if addendum else [text]):
            document = UploadedDocument(
                case_id=case.id,
                organization_id=organization.id,
                blob_key=f"cases/{case.id}/dossiers/{uuid.uuid4()}.pdf",
                original_filename=f"dossier-{ordinal}.pdf",
                content_type="application/pdf",
                checksum_sha256=f"{ordinal}" * 64,
                size_bytes=2048,
                parse_status=ParseStatus.parsed,
                parser_provider="llamaparse",
            )
            session.add(document)
            await session.flush()
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    parse_run=0,
                    ordinal=0,
                    text=body,
                    page=ordinal + 1,
                    block_type=BlockType.paragraph,
                )
            )

        extraction = AnalysisJob(
            case_id=case.id,
            organization_id=organization.id,
            job_type=JobType.EXTRACT_FACTS,
            payload={},
        )
        session.add(extraction)
        await session.flush()
        await extract_case_facts(session, extraction)
        await record_version(session, case=case, change_reason="Extraction automatique")
        await session.commit()
        await session.refresh(user)
        await session.refresh(case)
        return user, case


async def agent_job(
    factory: async_sessionmaker[AsyncSession], case: Case, **payload
) -> uuid.UUID:
    async with factory() as session:
        job = AnalysisJob(
            case_id=case.id,
            organization_id=case.organization_id,
            job_type=JobType.RUN_AGENT,
            payload={"agent_role": "urology_oncology", **payload},
            status=JobStatus.running,
        )
        session.add(job)
        await session.commit()
        return job.id


async def execute(
    factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
    mode: str = "ok",
):
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", mode)
    async with factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        outcome = await run_agent(session, job, adapter=adapter, blobs=blobs)
        await session.commit()
        return outcome


async def runs_for(
    factory: async_sessionmaker[AsyncSession], case: Case
) -> list[AgentRun]:
    async with factory() as session:
        rows = await session.execute(
            sa.select(AgentRun)
            .where(AgentRun.case_id == case.id)
            .order_by(AgentRun.created_at)
        )
        return list(rows.scalars().all())


async def sign_in(client: AsyncClient, user: User) -> None:
    response = await client.post(
        "/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------------------
# Building the bundle
# --------------------------------------------------------------------------------------


async def test_the_bundle_carries_the_ledger_and_the_text_behind_each_value(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        built = await build_bundle(
            session,
            case=case,
            agent_role="urology_oncology",
            version=await _latest_version(session, case),
        )

    fields = {field.key: field for field in built.bundle.inputs.case_ledger}
    assert fields["muscle_invasion"].value == "Oui"
    assert fields["histology"].value == "Carcinome urothélial"

    # Every value the agent can act on names a snippet it can cite.
    snippets = {snippet.id: snippet for snippet in built.bundle.inputs.source_snippets}
    cited = fields["muscle_invasion"].source_ids
    assert cited and cited[0] in snippets
    assert "Infiltration du muscle" in snippets[cited[0]].text
    # Ids are exactly what may be cited, and nothing else.
    assert built.bundle.citable_ids() == set(snippets)


async def test_a_field_nobody_filled_in_is_named_as_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The agent must be told what is absent, so it can say so rather than assume."""
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        built = await build_bundle(
            session,
            case=case,
            agent_role="urology_oncology",
            version=await _latest_version(session, case),
        )

    assert "who_status" in built.bundle.inputs.missing_fields
    assert any("manquante" in note for note in built.bundle.constraints)


async def test_a_contradiction_reaches_the_agent_as_a_contradiction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two readings of the creatinine must not be collapsed into whichever came first."""
    user, case = await seed_case(session_factory, addendum=ADDENDUM_TEXT)

    async with session_factory() as session:
        built = await build_bundle(
            session,
            case=case,
            agent_role="urology_oncology",
            version=await _latest_version(session, case),
        )

    conflicts = {conflict.field: conflict for conflict in built.bundle.inputs.conflicting_fields}
    assert "creatinine" in conflicts
    assert set(conflicts["creatinine"].values) == {"17.33", "173.3"}
    # Both readings are citable, each with its own source.
    assert len(conflicts["creatinine"].source_ids) == 2


async def test_the_bundle_has_no_room_for_another_agents_opinion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Independence is structural: there is no field through which one could travel."""
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        built = await build_bundle(
            session,
            case=case,
            agent_role="urology_oncology",
            version=await _latest_version(session, case),
        )

    assert "other_agent" not in str(built.bundle.model_dump()).lower()
    assert not hasattr(built.bundle.inputs, "other_agent_outputs")


# --------------------------------------------------------------------------------------
# Running the agent
# --------------------------------------------------------------------------------------


async def test_a_successful_run_is_stored_with_its_input_and_its_transcript(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    assert await execute(session_factory, job_id, adapter, blobs, monkeypatch) is None

    runs = await runs_for(session_factory, case)
    assert len(runs) == 1
    run = runs[0]
    assert run.status is AgentRunStatus.succeeded
    assert run.output is not None
    assert run.output["recommendation"].startswith("Cystectomie")
    # The deployment that produced the opinion is recorded, not implied.
    assert run.model == "gpt-5.6-sol"
    assert run.provider == "azure-openai-responses"
    # And so is the exact input, so the run can be reproduced.
    assert run.bundle["inputs"]["case_id"] == str(case.id)
    assert run.ledger_version == 1

    archived = await blobs.get_bytes(pi_session_key(case.id, "urology_oncology", run.id))
    assert b'"role": "assistant"' in archived


async def test_the_run_is_visible_in_the_audit_trail(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)
    await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        events = await session.execute(
            sa.select(AuditEvent).where(
                AuditEvent.case_id == case.id,
                AuditEvent.event_type == "agent.run_completed",
            )
        )
        recorded = events.scalars().all()

    assert len(recorded) == 1
    assert recorded[0].payload["model"] == "gpt-5.6-sol"


async def test_local_work_files_do_not_survive_the_run(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundle holds patient data; once archived it has no reason to sit on disk."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)
    await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    leftovers = list(adapter.work_root.rglob("*.json")) + list(
        adapter.work_root.rglob("*.jsonl")
    )
    assert leftovers == []


# --------------------------------------------------------------------------------------
# Refusing an answer RCPia cannot stand behind
# --------------------------------------------------------------------------------------


async def test_an_invented_citation_is_refused_and_the_attempt_recorded(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fabricated reference looks like grounding while being none."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="invented"
    )

    assert outcome is not None
    assert outcome.status is JobStatus.failed
    assert "ev-999" in (outcome.error_message or "")

    runs = await runs_for(session_factory, case)
    assert [run.status for run in runs] == [AgentRunStatus.failed]
    assert runs[0].output is None
    # The transcript is kept: a rejected answer is exactly what someone will want to read.
    assert runs[0].session_blob_key is not None
    assert await blobs.exists(runs[0].session_blob_key)


async def test_an_answer_about_another_case_is_refused(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Echoing the wrong case means the agent was not reading its own bundle."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="wrongcase"
    )

    assert outcome is not None and outcome.status is JobStatus.failed
    assert str(case.id) in (outcome.error_message or "")


async def test_an_answer_carrying_unknown_fields_is_refused(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confidence score RCPia never asked for is not a field it can interpret."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="offschema"
    )

    assert outcome is not None and outcome.status is JobStatus.failed
    assert outcome.retryable is True


async def test_prose_instead_of_json_fails_the_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="garbage"
    )

    assert outcome is not None and outcome.status is JobStatus.failed
    assert (await runs_for(session_factory, case))[0].status is AgentRunStatus.failed


async def test_a_crashed_run_is_retryable_and_leaves_a_record(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="crash"
    )

    assert outcome is not None
    assert outcome.retryable is True
    runs = await runs_for(session_factory, case)
    assert runs[0].status is AgentRunStatus.failed
    # Nothing ran, so there is no transcript to point at.
    assert runs[0].session_blob_key is None
    # The bundle holds patient data; a run that failed still has to leave nothing behind.
    assert list(adapter.work_root.rglob("*.json")) == []


async def test_a_missing_agent_folder_is_not_retried(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration will not fix itself; retrying only delays the human who must act."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case, agent_role="radiology")

    outcome = await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    assert outcome is not None
    assert outcome.retryable is False


# --------------------------------------------------------------------------------------
# What stops a run, and what does not
# --------------------------------------------------------------------------------------


async def test_a_missing_blocking_field_waits_for_a_person(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No safe provisional answer exists without muscle invasion, so nobody is asked."""
    user, case = await seed_case(
        session_factory, text="**Type histologique** : Carcinome urothélial\n"
    )
    job_id = await agent_job(session_factory, case)

    outcome = await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    assert outcome is not None
    assert outcome.status is JobStatus.waiting_for_user
    assert "Infiltration du muscle" in (outcome.error_message or "")
    assert await runs_for(session_factory, case) == []


async def test_the_run_reads_the_version_the_job_named_not_the_newest(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correction landing while the job waits must not change the question asked."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case, ledger_version=1)

    async with session_factory() as session:
        stored_user = await session.get(User, user.id)
        stored_case = await session.get(Case, case.id)
        assert stored_user is not None and stored_case is not None
        await apply_correction(
            session,
            case=stored_case,
            user=stored_user,
            fact_type="who_status",
            value="1",
            note="Relevé au dossier papier.",
        )
        await record_version(session, case=stored_case, change_reason="Correction")
        await session.commit()

    await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    run = (await runs_for(session_factory, case))[0]
    assert run.ledger_version == 1
    assert "who_status" in run.bundle["inputs"]["missing_fields"]


# --------------------------------------------------------------------------------------
# What a recorded opinion guarantees
# --------------------------------------------------------------------------------------


async def test_a_recorded_opinion_cannot_be_edited_into_another_one(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)
    await execute(session_factory, job_id, adapter, blobs, monkeypatch)
    run = (await runs_for(session_factory, case))[0]

    async with session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                sa.text(
                    "UPDATE agent_runs SET error_message = 'rewritten' WHERE id = :id"
                ),
                {"id": run.id},
            )
        await session.rollback()


async def test_a_second_delivery_of_the_same_job_does_not_buy_a_second_opinion(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Job delivery is at-least-once, so the handler has to be idempotent."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    await execute(session_factory, job_id, adapter, blobs, monkeypatch)
    assert await execute(session_factory, job_id, adapter, blobs, monkeypatch) is None

    assert len(await runs_for(session_factory, case)) == 1


# --------------------------------------------------------------------------------------
# Asking for an analysis
# --------------------------------------------------------------------------------------


async def test_requesting_an_analysis_queues_a_job_pinned_to_the_current_version(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    response = await client.post(f"/cases/{case.id}/analysis", json={})

    assert response.status_code == 202
    async with session_factory() as session:
        job = await session.get(AnalysisJob, uuid.UUID(response.json()["job_id"]))
        assert job is not None
        assert job.job_type == JobType.RUN_AGENT
        assert job.payload["ledger_version"] == 1


async def test_asking_twice_does_not_queue_two_runs(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    first = await client.post(f"/cases/{case.id}/analysis", json={})
    second = await client.post(f"/cases/{case.id}/analysis", json={})

    assert first.json()["job_id"] == second.json()["job_id"]


async def test_filling_the_missing_field_releases_the_parked_job(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing else returns a parked job to the queue, so asking again has to.

    Otherwise the clinician supplies exactly what was asked for, presses the button, and
    gets back a job that will never run.
    """
    user, case = await seed_case(
        session_factory, text="**Type histologique** : Carcinome urothélial\n"
    )
    await sign_in(client, user)
    job_id = await agent_job(session_factory, case)
    parked = await execute(session_factory, job_id, adapter, blobs, monkeypatch)
    assert parked is not None and parked.status is JobStatus.waiting_for_user

    async with session_factory() as session:
        # What the worker records when the handler reports `waiting_for_user`.
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        job.status = JobStatus.waiting_for_user
        job.error_message = "Infiltration du muscle : information manquante"

        stored_user = await session.get(User, user.id)
        stored_case = await session.get(Case, case.id)
        assert stored_user is not None and stored_case is not None
        await apply_correction(
            session,
            case=stored_case,
            user=stored_user,
            fact_type="muscle_invasion",
            value="Oui",
            note="Confirmé sur le compte rendu opératoire.",
        )
        await record_version(session, case=stored_case, change_reason="Correction")
        await session.commit()

    response = await client.post(f"/cases/{case.id}/analysis", json={})

    assert response.status_code == 202
    assert response.json()["job_id"] == str(job_id), "the parked job, not a new one"
    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        assert job.status is JobStatus.queued
        # Re-pinned: the correction that unblocked the case created a new version, and
        # the old one is the version that was blocked.
        assert job.payload["ledger_version"] == 2


async def test_a_case_missing_a_blocking_field_cannot_be_analysed(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The clinician is at the screen now; naming the missing field beats a job that
    fails a minute later."""
    user, case = await seed_case(
        session_factory, text="**Type histologique** : Carcinome urothélial\n"
    )
    await sign_in(client, user)

    response = await client.post(f"/cases/{case.id}/analysis", json={})

    assert response.status_code == 409
    assert "Infiltration du muscle" in response.json()["detail"]


async def test_runs_are_listed_with_failures_included(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    job_id = await agent_job(session_factory, case)
    await execute(session_factory, job_id, adapter, blobs, monkeypatch, mode="invented")
    await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    body = (await client.get(f"/cases/{case.id}/analysis")).json()

    assert [run["status"] for run in body] == ["succeeded", "failed"], "newest first"
    assert body[0]["output"]["recommendation"].startswith("Cystectomie")
    assert "ev-999" in body[1]["error_message"]


async def test_another_organizations_case_is_not_analysable(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, _ = await seed_case(session_factory)
    _, other_case = await seed_case(session_factory)
    await sign_in(client, user)

    response = await client.post(f"/cases/{other_case.id}/analysis", json={})

    assert response.status_code == 404


async def _latest_version(session: AsyncSession, case: Case) -> CaseLedgerVersion:
    version = await latest_version(session, case_id=case.id)
    assert version is not None
    return version
