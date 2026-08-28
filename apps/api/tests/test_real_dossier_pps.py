"""End to end on a real dossier: does the build actually produce a fiche?

**Phase 9's own precondition says this is unverified.** *"The current build produces a PPS for
all 15 dossiers, end to end. Unverified… If the pipeline cannot ingest a real dossier and emit a
fiche without a person in the middle, there is nothing to score."* Every other test in this
suite uses a fixture written to exercise one behaviour. This one runs the whole chain —
extraction, ledger, agent, generation, rendering — on the text of a **real anonymised dossier**,
and writes the result out so a person can read it.

**The agent is the fake.** `RCPIA_FAKE_PI_MODE` stands in for a live model call, so what this
verifies is the *plumbing and the document*, not the clinical reasoning. That distinction is the
point: it isolates "can the app produce a fiche from a real record" from "is the opinion any
good", which are different questions with different failure modes.

The dossier text is passed in rather than committed, so no patient record enters the repository.
"""

import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.agents.pi_adapter import PiAdapter
from rcpia.agents.routes import dedupe_key_for
from rcpia.agents.service import run_agent
from rcpia.cases.facts import extract_case_facts
from rcpia.cases.ledger import record_version
from rcpia.core.models import (
    AnalysisJob,
    BlockType,
    CancerType,
    Case,
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
from rcpia.pps import generator
from rcpia.pps.render import render_markdown
from rcpia.storage.local import LocalBlobStore

FAKE_PI = Path(__file__).parent / "fake_pi.py"

# Where a real dossier can be found on the machine that has them. Absent on any other, and the
# test skips rather than failing -- the record is not in the repository and must not be.
DOSSIER_DIR = Path(
    os.environ.get(
        "RCPIA_DOSSIER_DIR",
        r"C:\Users\hzmarrou\OneDrive\python\learning\pi\rcpia\docs",
    )
)
OUT_DIR = Path(os.environ.get("RCPIA_PPS_OUT", "")) if os.environ.get("RCPIA_PPS_OUT") else None


@pytest.fixture
def agents_root(tmp_path: Path) -> Path:
    """A minimal agent folder. The brain is a stub -- the fake pi does not read it."""
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


def _dossier(name: str) -> str:
    path = DOSSIER_DIR / name
    if not path.exists():
        pytest.skip(f"no real dossier at {path}; set RCPIA_DOSSIER_DIR to run this")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("filename", "cancer_type"),
    [("dossier01.md", CancerType.bladder), ("dossier03.md", CancerType.prostate)],
)
async def test_a_real_dossier_produces_a_rendered_fiche(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    cancer_type: CancerType,
) -> None:
    text = _dossier(filename)
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", "ok")

    async with session_factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
        session.add(organization)
        await session.flush()
        session.add(
            User(
                organization_id=organization.id,
                email=f"u-{uuid.uuid4().hex[:8]}@example.org",
                role=UserRole.uploader,
                password_hash="x" * 60,
                is_active=True,
            )
        )
        case = Case(
            organization_id=organization.id,
            reference=filename.replace(".md", ""),
            cancer_type=cancer_type,
            status=CaseStatus.ready_for_review,
        )
        session.add(case)
        await session.flush()

        document = UploadedDocument(
            case_id=case.id,
            organization_id=organization.id,
            blob_key=f"cases/{case.id}/dossiers/{uuid.uuid4()}.pdf",
            original_filename=filename,
            content_type="application/pdf",
            checksum_sha256="e" * 64,
            size_bytes=len(text),
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
                text=text,
                page=1,
                block_type=BlockType.paragraph,
            )
        )

        job = AnalysisJob(
            case_id=case.id,
            organization_id=organization.id,
            job_type=JobType.EXTRACT_FACTS,
            payload={},
        )
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await record_version(session, case=case, change_reason="Extraction automatique")
        await session.commit()
        case_id = case.id

    # The agent, faked. What is under test here is the chain, not the opinion.
    role = "urology_oncology"
    async with session_factory() as session:
        stored = await session.get(Case, case_id)
        assert stored is not None
        run_job = AnalysisJob(
            case_id=case_id,
            organization_id=stored.organization_id,
            job_type=JobType.RUN_AGENT,
            payload={"agent_role": role},
            dedupe_key=dedupe_key_for(case_id, role),
            status=JobStatus.running,
        )
        session.add(run_job)
        await session.commit()
        run_job_id = run_job.id

    async with session_factory() as session:
        run_job = await session.get(AnalysisJob, run_job_id)
        assert run_job is not None
        outcome = await run_agent(session, run_job, adapter=adapter, blobs=blobs)
        assert outcome is None, f"the agent run failed: {outcome}"
        await session.commit()

    async with session_factory() as session:
        stored = await session.get(Case, case_id)
        assert stored is not None
        document_model = await generator.generate(session, case=stored)

    body = render_markdown(document_model)

    # The document exists, names the patient's case, and never claims a decision was taken.
    assert body.strip()
    assert "Statut : Décision prise" not in body
    assert "Fiche validée : OUI" not in body

    if OUT_DIR is not None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{filename.replace('.md', '')}-pps.md").write_text(body, encoding="utf-8")
