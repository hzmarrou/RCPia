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

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.agents.bundle import BundleTooLarge, build_bundle
from rcpia.agents.output_processing import FEEDBACK_KEY, OutputFeedback
from rcpia.agents.pi_adapter import PiAdapter
from rcpia.agents.routes import dedupe_key_for
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
from rcpia.jobs.milestones import MilestoneWriter, no_milestones
from rcpia.jobs.models import AGENT_STEPS, JobType, RunStep
from rcpia.main import app
from rcpia.query.decision import RETRIEVAL_CONTRACT
from rcpia.storage.base import StorageError
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
        role = str(payload.get("agent_role", "urology_oncology"))
        job = AnalysisJob(
            case_id=case.id,
            organization_id=case.organization_id,
            job_type=JobType.RUN_AGENT,
            payload={
                "agent_role": "urology_oncology",
                "retrieval_contract": RETRIEVAL_CONTRACT,
                **payload,
            },
            dedupe_key=dedupe_key_for(case.id, role),
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
    milestones: MilestoneWriter = no_milestones,
):
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", mode)
    async with factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        outcome = await run_agent(
            session, job, adapter=adapter, blobs=blobs, milestones=milestones
        )
        await session.commit()
        return outcome


async def test_validation_feedback_survives_commit_and_reaches_the_next_attempt(
    session_factory, adapter, blobs, monkeypatch,
) -> None:
    _, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)
    first = await execute(session_factory, job_id, adapter, blobs, monkeypatch, mode="offschema")
    assert first is not None and first.retryable
    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)
        feedback = OutputFeedback.model_validate(job.payload[FEEDBACK_KEY])
        assert feedback.messages
        failed = await session.get(AgentRun, feedback.previous_run_id)
        assert failed.status == AgentRunStatus.failed
        original_inputs = failed.bundle["inputs"]
    assert await execute(session_factory, job_id, adapter, blobs, monkeypatch) is None
    async with session_factory() as session:
        succeeded = await session.scalar(sa.select(AgentRun).where(
            AgentRun.job_id == job_id, AgentRun.status == AgentRunStatus.succeeded,
        ))
        assert succeeded.bundle["inputs"] == original_inputs
        assert str(feedback.previous_run_id) in succeeded.bundle["constraints"][-1]
        assert all(message in succeeded.bundle["constraints"][-1] for message in feedback.messages)
        event = await session.scalar(sa.select(AuditEvent).where(
            AuditEvent.entity_id == succeeded.id, AuditEvent.event_type == "agent.run_completed",
        ))
        assert event.payload["validation_feedback_used"] is True


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


def test_the_deployed_worker_gives_its_agents_a_retriever(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_agent` takes the retriever optionally, and the worker was not passing one.

    Without an embedder and a reranker `build_bundle` skips retrieval, so every agent in a
    deployed worker answered with no guideline passages -- honestly labelled in its constraints,
    and completely ungrounded. Every measurement harness passed one, so the difference never
    showed up in anything that was measured.
    """
    import inspect

    from rcpia import worker as worker_module

    source = inspect.getsource(worker_module.build_worker)
    assert "build_embedder" in source and "build_reranker" in source, (
        "the worker must construct a retriever"
    )
    assert "embedder=embedder" in source and "reranker=reranker" in source, (
        "and hand it to the agent handler"
    )


def test_the_deployed_worker_gives_its_agents_a_milestone_reporter() -> None:
    """Same failure mode as the retriever above, one floor down.

    `run_agent` reports progress to whatever it is handed and defaults to reporting
    nowhere, so a worker that forgets to pass a reporter runs correctly and silently: every
    job stays at `milestone = NULL`, and a progress display has nothing to read while
    looking like it is wired.
    """
    import inspect

    from rcpia import worker as worker_module

    source = inspect.getsource(worker_module.build_worker)
    assert "MilestoneReporter(" in source, "the worker must build a reporter for the job"
    assert "milestones=milestones" in source, "and hand it to the agent handler"


def test_every_wired_agent_folder_has_what_the_adapter_requires() -> None:
    """`agent_dir` refuses a folder without `brain.md` or `.pi/APPEND_SYSTEM.md`.

    The second file is the one that is easy to forget and impossible to notice: without it the
    brain is never read, and the agent answers as a general model with the bundle in front of
    it. That failure produces a plausible opinion rather than an error.
    """
    root = Path(__file__).resolve().parents[3] / "agents"
    wired = [d for d in root.iterdir() if d.is_dir() and (d / "brain.md").is_file()]
    assert {d.name for d in wired} >= {
        "urology_oncology",
        "medical_oncology",
        "radiation_oncology",
        "rcp_synthesis",
    }
    for folder in wired:
        assert (folder / ".pi" / "APPEND_SYSTEM.md").is_file(), folder.name
        assert (folder / ".pi" / "settings.json").is_file(), folder.name
        # A brain that never names the role it speaks for is the copy-paste failure.
        assert (folder / "brain.md").read_text(encoding="utf-8").strip()



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


async def test_a_clinicians_correction_arrives_as_a_clinicians_correction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A correction has no excerpt to cite, so without its origin and its reason it would
    reach the agent looking like an unsourced machine reading — the opposite of what it
    is."""
    user, case = await seed_case(session_factory)

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
            note="Relevé au dossier papier lors de la consultation.",
        )
        await record_version(session, case=stored_case, change_reason="Correction")
        await session.commit()

        built = await build_bundle(
            session,
            case=case,
            agent_role="urology_oncology",
            version=await _latest_version(session, case),
        )

    fields = {field.key: field for field in built.bundle.inputs.case_ledger}
    assert fields["who_status"].value == "1"
    assert fields["who_status"].origin == "human"
    assert "dossier papier" in (fields["who_status"].correction_note or "")
    # Machine readings stay marked as such.
    assert fields["muscle_invasion"].origin == "machine"


async def test_a_large_dossier_loses_its_least_important_sources_and_says_so(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bundle has to fit in a context window. What it drops is chosen, and declared."""
    monkeypatch.setattr("rcpia.agents.bundle.MAX_SNIPPETS", 2)
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        built = await build_bundle(
            session,
            case=case,
            agent_role="urology_oncology",
            version=await _latest_version(session, case),
        )

    assert len(built.bundle.inputs.source_snippets) == 2
    fields = {field.key: field for field in built.bundle.inputs.case_ledger}
    # Muscle invasion decides the whole strategy, so it keeps its source.
    assert fields["muscle_invasion"].source_ids
    assert any("omis" in constraint for constraint in built.bundle.constraints)


async def test_a_dossier_too_large_to_send_is_refused_not_truncated(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error would tell nobody why. This does."""
    monkeypatch.setattr("rcpia.agents.bundle.MAX_BUNDLE_BYTES", 500)
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        with pytest.raises(BundleTooLarge, match="dépasse"):
            await build_bundle(
                session,
                case=case,
                agent_role="urology_oncology",
                version=await _latest_version(session, case),
            )


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


async def test_a_run_reports_each_step_before_it_takes_it(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the milestones: a display reads what the run is doing now.

    Order is the property, not the count. A step written after its work finished would let
    the display show `reasoning` while the answer is already stored.
    """
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)
    reported: list[str] = []

    async def record(step: str) -> None:
        reported.append(step)

    assert (
        await execute(session_factory, job_id, adapter, blobs, monkeypatch, milestones=record)
        is None
    )

    assert reported == list(AGENT_STEPS)


async def test_a_run_that_never_reached_the_model_does_not_claim_to_have_reasoned(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed run stops reporting where it stopped working.

    `crash` fails inside the model call, so the run announced `reasoning` and got no
    answer. Reporting `checking` or `recording` afterwards would describe work that never
    happened, on a job that is about to be retried.
    """
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)
    reported: list[str] = []

    async def record(step: str) -> None:
        reported.append(step)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="crash", milestones=record
    )

    assert outcome is not None and outcome.status is JobStatus.failed
    assert reported == [RunStep.BUNDLE, RunStep.REASONING]


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


async def test_an_agent_role_cannot_walk_out_of_the_agents_directory(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`agent_role` becomes a directory name and originates in an HTTP request."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case, agent_role="../../etc")

    outcome = await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    assert outcome is not None
    assert outcome.retryable is False
    assert "Unsafe agent name" in (outcome.error_message or "")


async def test_the_api_refuses_an_agent_role_that_is_not_a_plain_name(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Checked at the edge as well, because the value crosses a process boundary."""
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/analysis", json={"agent_role": "../../etc/passwd"}
    )

    assert response.status_code == 422


async def test_a_crashed_run_keeps_the_transcript_that_explains_it(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error partway through writes the transcript someone needs to debug it;
    discarding it would leave them with an exit code."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    await execute(session_factory, job_id, adapter, blobs, monkeypatch, mode="dieslate")

    run = (await runs_for(session_factory, case))[0]
    assert run.status is AgentRunStatus.failed
    assert run.session_archived is True
    assert run.session_blob_key is not None
    assert b"assistant" in await blobs.get_bytes(run.session_blob_key)


async def test_a_run_whose_transcript_never_landed_does_not_claim_one(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key is a claim about another system; `session_archived` is what the row knows."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    async def refuse(*args, **kwargs):
        raise StorageError("blob store unavailable")

    monkeypatch.setattr(blobs, "put_bytes", refuse)
    outcome = await execute(session_factory, job_id, adapter, blobs, monkeypatch)

    assert outcome is None, "losing the transcript must not lose the opinion"
    run = (await runs_for(session_factory, case))[0]
    assert run.status is AgentRunStatus.succeeded
    assert run.session_archived is False


async def test_an_uncited_recommendation_is_refused(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With evidence in the bundle, a recommendation citing none of it is an assertion."""
    user, case = await seed_case(session_factory)
    job_id = await agent_job(session_factory, case)

    outcome = await execute(
        session_factory, job_id, adapter, blobs, monkeypatch, mode="uncited"
    )

    assert outcome is not None and outcome.status is JobStatus.failed
    assert "sans citer" in (outcome.error_message or "")


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


async def test_the_database_refuses_a_successful_run_that_cannot_be_reviewed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A recommendation is only reviewable if the row says what it read, which job
    produced it, and which deployment answered. That is settled in PostgreSQL, because
    application code is what would be bypassed."""
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        session.add(
            AgentRun(
                case_id=case.id,
                organization_id=case.organization_id,
                agent_role="urology_oncology",
                status=AgentRunStatus.succeeded,
                bundle={},
                output={"recommendation": "Cystectomie"},
            )
        )
        with pytest.raises(IntegrityError, match="successful_runs_are_reviewable"):
            await session.commit()


@pytest.mark.parametrize(
    ("bundle_value", "output_value", "why"),
    [
        ({"inputs": None}, {"recommendation": "x", "rationale": "y"}, "null inputs"),
        ({}, {"recommendation": "x", "rationale": "y"}, "no inputs at all"),
        ({"inputs": {"case_id": ""}}, {"recommendation": "x", "rationale": "y"}, "no case"),
        ({"inputs": {"case_id": "c"}}, {}, "an empty opinion"),
        ({"inputs": {"case_id": "c"}}, {"recommendation": "x"}, "no rationale"),
        ({"inputs": {"case_id": "c"}}, {"rationale": "y"}, "no recommendation"),
    ],
)
async def test_a_successful_run_needs_a_real_bundle_and_a_real_opinion(
    session_factory: async_sessionmaker[AsyncSession],
    bundle_value: dict,
    output_value: dict,
    why: str,
) -> None:
    """A key that exists is not a bundle, and `{}` is not an opinion. Checking presence
    rather than shape let both through."""
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        version = await _latest_version(session, case)
        job_id = await agent_job(session_factory, case)
        session.add(
            AgentRun(
                case_id=case.id,
                organization_id=case.organization_id,
                agent_role="urology_oncology",
                status=AgentRunStatus.succeeded,
                ledger_version_id=version.id,
                ledger_version=version.version,
                job_id=job_id,
                provider="azure-openai-responses",
                model="gpt-5.6-sol",
                session_blob_key="cases/x/sessions/urology_oncology/y.jsonl",
                bundle=bundle_value,
                output=output_value,
            )
        )
        with pytest.raises(IntegrityError, match="successful_runs_are_reviewable"):
            await session.commit()


async def test_a_run_cannot_record_a_version_number_that_is_not_its_snapshots(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Otherwise a run could claim to have read version 1 while pointing at version 7."""
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        version = await _latest_version(session, case)
        session.add(
            AgentRun(
                case_id=case.id,
                organization_id=case.organization_id,
                agent_role="urology_oncology",
                status=AgentRunStatus.failed,
                ledger_version_id=version.id,
                ledger_version=version.version + 99,
                bundle={},
                error_message="…",
            )
        )
        with pytest.raises(IntegrityError, match="ledger_version_number_matches"):
            await session.commit()


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


async def test_twelve_simultaneous_requests_queue_exactly_one_run(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The check-then-insert is a fast path; the partial unique index is the guarantee.

    Without it, concurrent requests each see no live job and each create one — and a case
    quietly buys a dozen opinions on the same question.
    """
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    responses = await asyncio.gather(
        *(client.post(f"/cases/{case.id}/analysis", json={}) for _ in range(12))
    )

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["job_id"] for response in responses}) == 1

    async with session_factory() as session:
        queued = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AnalysisJob)
            .where(
                AnalysisJob.case_id == case.id,
                AnalysisJob.job_type == JobType.RUN_AGENT,
            )
        )
    assert queued == 1


async def test_a_second_agent_may_run_on_the_same_case(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The key is per agent: hub-and-spoke needs several specialists at once."""
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    first = await client.post(f"/cases/{case.id}/analysis", json={})
    second = await client.post(
        f"/cases/{case.id}/analysis", json={"agent_role": "medical_oncology"}
    )

    assert second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]


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


async def test_targeted_api_pins_case_decision_and_refuses_incompatible_reuse(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        stored.clinical_question = "Synthetic recorded decision"
        await session.commit()
    query = {
        "mode": "decision_targeted_v1", "decision_source": "case",
        "selected_fact_keys": ["egfr"],
    }
    url = f"/cases/{case.id}/analysis"
    first = await client.post(url, json={"retrieval_query": query})
    assert first.status_code == 202
    same = await client.post(url, json={"retrieval_query": query})
    assert same.json()["job_id"] == first.json()["job_id"]
    assert (await client.post(url, json={})).status_code == 409
    async with session_factory() as session:
        job = await session.get(AnalysisJob, uuid.UUID(first.json()["job_id"]))
        assert job.payload["retrieval_query"]["decision"] == "Synthetic recorded decision"
        assert job.payload["clinical_question"] is None
        stored = await session.get(Case, case.id)
        stored.clinical_question = "Changed case decision"
        await session.commit()
    assert (await client.post(url, json={"retrieval_query": query})).status_code == 409
    async with session_factory() as session:
        job = await session.get(AnalysisJob, uuid.UUID(first.json()["job_id"]))
        assert job.payload["retrieval_query"]["decision"] == "Synthetic recorded decision"


@pytest.mark.parametrize("query", [
    {"mode": "unknown", "decision": "Synthetic", "selected_fact_keys": ["egfr"]},
    {"mode": "decision_targeted_v1", "selected_fact_keys": ["egfr"]},
    {"mode": "decision_targeted_v1", "decision": "Synthetic", "selected_fact_keys": ["bad"]},
    {
        "mode": "decision_targeted_v1", "decision": "Synthetic",
        "selected_fact_keys": ["egfr", "egfr"],
    },
    {
        "mode": "decision_targeted_v1", "decision": "Synthetic",
        "selected_fact_keys": ["egfr"], "extra": 1,
    },
])
async def test_invalid_targeted_api_request_queues_nothing(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], query: dict,
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    response = await client.post(f"/cases/{case.id}/analysis", json={"retrieval_query": query})
    assert response.status_code == 422
    async with session_factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(AnalysisJob).where(
            AnalysisJob.case_id == case.id, AnalysisJob.job_type == JobType.RUN_AGENT,
        )) == 0


async def test_duplicate_json_keys_never_dispatch_an_analysis(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    response = await client.post(
        f"/cases/{case.id}/analysis",
        content='{"clinical_question":"one","clinical_question":"two"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


async def test_targeted_panel_pins_contract_on_every_job_and_rejects_changed_keys(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    query = {
        "mode": "decision_targeted_v1", "decision": "Synthetic decision",
        "selected_fact_keys": ["egfr"],
    }
    body = {"roles": ["urology_oncology", "medical_oncology"], "retrieval_query": query}
    url = f"/cases/{case.id}/analysis/panel"
    first = await client.post(url, json=body)
    assert first.status_code == 202, first.text
    async with session_factory() as session:
        for item in first.json()["jobs"]:
            job = await session.get(AnalysisJob, uuid.UUID(item["job_id"]))
            assert job.payload["retrieval_query"]["decision"] == "Synthetic decision"
    again = await client.post(url, json=body)
    assert again.status_code == 202
    assert all(item["reused"] for item in again.json()["jobs"])
    query["selected_fact_keys"] = ["ctnm"]
    assert (await client.post(url, json=body)).status_code == 409


async def test_worker_service_consumes_the_pinned_targeted_contract(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from rcpia.agents import service
    from rcpia.agents.bundle import BundleError

    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    query = {
        "mode": "decision_targeted_v1", "decision": "Synthetic pinned decision",
        "selected_fact_keys": ["egfr"],
    }
    response = await client.post(
        f"/cases/{case.id}/analysis", json={"retrieval_query": query},
    )
    assert response.status_code == 202
    build = AsyncMock(side_effect=BundleError("Synthetic stop before any provider."))
    monkeypatch.setattr(service, "build_bundle", build)
    async with session_factory() as session:
        job = await session.get(AnalysisJob, uuid.UUID(response.json()["job_id"]))
        outcome = await service.run_agent(session, job, adapter=None, blobs=None)
    assert outcome.status is JobStatus.failed
    assert build.call_args.kwargs["retrieval_query"].decision == "Synthetic pinned decision"


async def test_baseline_api_and_panel_refuse_legacy_contract_reuse(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)
    old_id = await agent_job(
        session_factory, case, ledger_version=1, retrieval_contract=None,
    )
    url = f"/cases/{case.id}/analysis"
    assert (await client.post(url, json={})).status_code == 409
    panel_response = await client.post(url + "/panel", json={"roles": ["urology_oncology"]})
    assert panel_response.status_code == 409
    async with session_factory() as session:
        old = await session.get(AnalysisJob, old_id)
        assert old.payload.get("retrieval_contract") is None
        old.status = JobStatus.cancelled
        await session.commit()
    current = await client.post(url, json={})
    assert current.status_code == 202
    assert current.json()["job_id"] != str(old_id)
    async with session_factory() as session:
        job = await session.get(AnalysisJob, uuid.UUID(current.json()["job_id"]))
        assert job.payload["retrieval_contract"] == RETRIEVAL_CONTRACT
        assert job.payload.get("retrieval_query") is None
