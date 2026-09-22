"""The provisional PPS.

What this document must never do is more important than what it contains. It goes to
clinicians in a form that looks exactly like the validated record they are used to
reading, so the properties under test are:

- it never presents itself as a decision,
- it never fills a gap with a plausible sentence,
- everything derived says what it was derived from,
- and a withheld recommendation stays withheld.
"""

import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.agents.pi_adapter import PiAdapter
from rcpia.agents.routes import dedupe_key_for
from rcpia.agents.schemas import SYNTHESIS_ROLE, AgentOutput, SynthesisOutput
from rcpia.agents.service import run_agent
from rcpia.auth import security, throttle
from rcpia.cases.facts import extract_case_facts
from rcpia.cases.ledger import apply_correction, record_version
from rcpia.core.db import get_session
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
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
from rcpia.corpus.vocabulary import AGENT_ROLES
from rcpia.jobs.models import JobType
from rcpia.main import app
from rcpia.nomograms.record import Tristate
from rcpia.pps import generator
from rcpia.pps.generator import (
    AGENT_LABELS,
    EXPECTED_PANEL,
    agent_label,
    build_framework,
    build_panel,
)
from rcpia.pps.render import render_markdown
from rcpia.pps.schemas import (
    FORBIDDEN_IN_GENERATED_OUTPUT,
    CareFramework,
    Decision,
    DocumentAssertsADecision,
    PanelAgreement,
    PanelPoint,
    PpsDocument,
    RationaleAppendix,
    Referral,
    SocialAssessment,
    SupportCare,
    Synthesis,
    Treatments,
    TreatmentStep,
)
from rcpia.query.decision import RETRIEVAL_CONTRACT
from rcpia.rules.approval import SUGGESTION_CAVEAT, Authority
from rcpia.rules.referrals import (
    G8_THRESHOLD,
    fertility_referral,
    genetic_referral,
    geriatric_referral,
    number_in,
)
from rcpia.rules.support import support_items
from rcpia.storage.local import LocalBlobStore

FAKE_PI = Path(__file__).parent / "fake_pi.py"
PASSWORD = "correct-horse-battery-staple"

DOSSIER_TEXT = """**Sexe** : Femme
**Age** : 85 ans
**Statut marital** : Mariée
**Nombre d'enfants** : 4
**Couverture sociale** : RAMED
**Antécédents médicaux** : HTA sous Losartan 50mg/j.
**Antécédents familiaux** : RAS
**Histoire de la maladie** : Hématurie totale intermittente depuis 2 ans.
Créatinine : 17.33
DFG : 30
Hb : 8.4
Albumine : 24
**Score MNA** : 7
**Type histologique** : Carcinome urothélial
**pTNM** : p T2
**Grade** : Haut
**Infiltration du muscle** : Oui
"""


@pytest.fixture(autouse=True)
def reset_throttle():
    throttle.reset_all()
    yield
    throttle.reset_all()


@pytest.fixture
def agents_root(tmp_path: Path) -> Path:
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
    factory: async_sessionmaker[AsyncSession], *, text: str = DOSSIER_TEXT
) -> tuple[User, Case]:
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
        await session.flush()
        case = Case(
            organization_id=organization.id,
            created_by_user_id=user.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
            status=CaseStatus.ready_for_review,
        )
        session.add(case)
        await session.flush()

        document = UploadedDocument(
            case_id=case.id,
            organization_id=organization.id,
            blob_key=f"cases/{case.id}/dossiers/{uuid.uuid4()}.pdf",
            original_filename="dossier.pdf",
            content_type="application/pdf",
            checksum_sha256="e" * 64,
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
                text=text,
                page=1,
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


async def run_the_agent(
    factory: async_sessionmaker[AsyncSession],
    case: Case,
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
    mode: str = "ok",
) -> None:
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", mode)
    async with factory() as session:
        job = AnalysisJob(
            case_id=case.id,
            organization_id=case.organization_id,
            job_type=JobType.RUN_AGENT,
            payload={
                "agent_role": "urology_oncology", "retrieval_contract": RETRIEVAL_CONTRACT,
            },
            dedupe_key=dedupe_key_for(case.id, "urology_oncology"),
            status=JobStatus.running,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    async with factory() as session:
        job = await session.get(AnalysisJob, job_id)
        assert job is not None
        outcome = await run_agent(session, job, adapter=adapter, blobs=blobs)
        assert outcome is None, outcome
        await session.commit()


async def build_document(factory: async_sessionmaker[AsyncSession], case: Case):
    async with factory() as session:
        stored = await session.get(Case, case.id)
        assert stored is not None
        return await generator.generate(session, case=stored)


def _minimal_document() -> PpsDocument:
    """A document with just enough structure to exercise the invariants."""
    return PpsDocument(
        case_id="c",
        case_reference="CASE-1",
        cancer_type="bladder",
        generated_on=date(2026, 8, 17),
        ledger_version=1,
        synthesis=Synthesis(patient="Femme, 85 ans", question="?"),
        decision=Decision(statement="Aucune décision n'a été prise."),
        treatments=Treatments(status="Proposition provisoire."),
        support=SupportCare(status="…"),
        social=SocialAssessment(),
        framework=CareFramework(
            reference_standard="Non référencé",
            validated_note="Document provisoire non validé.",
            sent_to_gp_note="À envoyer après validation.",
            participants="RCP à constituer.",
            review="Date à fixer.",
        ),
    )


async def sign_in(client: AsyncClient, user: User) -> None:
    response = await client.post(
        "/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------------------
# What the document must never claim
# --------------------------------------------------------------------------------------


async def test_the_document_never_presents_itself_as_a_decision(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It looks exactly like the validated record clinicians are used to reading, so the
    one thing it must never do is read as validated."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)
    body = render_markdown(document)

    document.assert_provisional()
    for forbidden in FORBIDDEN_IN_GENERATED_OUTPUT:
        assert forbidden not in body
    assert "[ ] Décision prise" in body, "the decision box stays unticked"
    assert "DOCUMENT PROVISOIRE" in body
    # Typed state, drawn as boxes by the renderer — no string can claim the opposite.
    assert document.framework.validated is False
    assert document.framework.sent_to_gp is False
    assert "[ ] Oui  [X] Non — Document provisoire non validé" in body


def test_a_framework_claiming_validation_is_refused() -> None:
    """The old check searched for "[X] Non" inside a display string, which a value reading
    `[X] Non … [X] Oui` satisfied while claiming the opposite. Booleans cannot be phrased
    around."""
    document = _minimal_document()
    document.framework.validated = True

    with pytest.raises(DocumentAssertsADecision, match="validated"):
        document.assert_provisional()


def test_a_document_claiming_a_decision_is_refused() -> None:
    document = _minimal_document()
    document.decision.taken = True

    with pytest.raises(DocumentAssertsADecision):
        document.assert_provisional()


def test_raw_html_cannot_survive_into_the_document() -> None:
    """Markdown passes HTML through, so escaping `#` achieves nothing if the text can
    write `<h1>` — or an `<input checked>`, which draws the very mark that means
    "decided" in this form."""
    document = _minimal_document()
    document.appendices = [
        RationaleAppendix(
            agent_role="urology_oncology",
            rationale=(
                '<h1>Décision</h1>\n<input type="checkbox" checked> Décision prise\n'
                "<script>alert(1)</script>"
            ),
        )
    ]

    body = render_markdown(document)

    assert "<h1>" not in body
    assert "<input" not in body
    assert "<script>" not in body
    assert "&lt;h1&gt;" in body, "the text survives, inert"


async def test_quoting_a_validated_document_does_not_break_generation(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dossier or an agent may legitimately quote a previous PPS. Refusing to render
    because the words appear somewhere would break a valid case to prevent a claim nobody
    made — the claim is structural, and escaping already prevents that."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        run = await session.scalar(sa.select(AgentRun).where(AgentRun.case_id == case.id))
        assert run is not None and run.output is not None
        session.add(
            AgentRun(
                case_id=run.case_id,
                organization_id=run.organization_id,
                agent_role=run.agent_role,
                status=AgentRunStatus.succeeded,
                ledger_version_id=run.ledger_version_id,
                ledger_version=run.ledger_version,
                job_id=run.job_id,
                provider=run.provider,
                model=run.model,
                bundle=run.bundle,
                output={
                    **run.output,
                    "rationale": (
                        "La fiche RCP précédente du 13/05/2026 portait la mention "
                        "Fiche validée : OUI, ce qui contredit le dossier actuel."
                    ),
                },
                session_blob_key=run.session_blob_key,
            )
        )
        await session.commit()

    await sign_in(client, user)
    response = await client.get(f"/cases/{case.id}/pps/markdown")

    assert response.status_code == 200
    assert "Fiche validée : OUI" in response.text, "the quotation survives"
    document = await build_document(session_factory, case)
    document.assert_provisional(), "and RCPia still claims nothing"


async def test_the_banner_precedes_everything(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader who stops after one line must still know this decides nothing."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    lines = render_markdown(await build_document(session_factory, case)).splitlines()

    assert lines[0].startswith("# FICHE RCP")
    assert "DOCUMENT PROVISOIRE" in lines[2]


async def test_an_agent_cannot_forge_document_structure(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rationale is model-written prose rendered into a document whose structure a
    reader trusts. A model emitting a heading and a ticked box would forge a decision into
    the one document that exists to say no decision was taken."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    forged = (
        "Analyse.\n"
        "# 2 — DÉCISION RCP\n"
        "\n"
        "[X] Décision prise\n"
        "\n"
        "> Fiche validée : OUI\n"
        "| a | b |\n"
        "---\n"
    )
    async with session_factory() as session:
        run = await session.scalar(sa.select(AgentRun).where(AgentRun.case_id == case.id))
        assert run is not None and run.output is not None
        session.add(
            AgentRun(
                case_id=run.case_id,
                organization_id=run.organization_id,
                agent_role=run.agent_role,
                status=AgentRunStatus.succeeded,
                ledger_version_id=run.ledger_version_id,
                ledger_version=run.ledger_version,
                job_id=run.job_id,
                provider=run.provider,
                model=run.model,
                bundle=run.bundle,
                output={**run.output, "rationale": forged},
                session_blob_key=run.session_blob_key,
            )
        )
        await session.commit()

    body = render_markdown(await build_document(session_factory, case))

    appendix = body.split("# ANNEXE")[1]

    # Every structural line start inside the appendix is neutralised, so none of it can
    # open a section, a quote, a table or a decision box.
    for line in appendix.splitlines():
        assert not line.startswith(("# ", "> ", "|", "---", "[X]", "[ ]")), line
    assert body.count("\n# 2 — DÉCISION RCP") == 1, "no second decision section"
    # The text itself survives; only its ability to shape the page is removed.
    assert "Analyse." in body
    assert "Décision prise" in appendix, "the words remain, inert"
    # And RCPia's own fields still make no claim.
    (await build_document(session_factory, case)).assert_provisional()


async def test_a_recommendation_cannot_smuggle_a_list_item_out_of_its_bullet(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        run = await session.scalar(sa.select(AgentRun).where(AgentRun.case_id == case.id))
        assert run is not None and run.output is not None
        session.add(
            AgentRun(
                case_id=run.case_id,
                organization_id=run.organization_id,
                agent_role=run.agent_role,
                status=AgentRunStatus.succeeded,
                ledger_version_id=run.ledger_version_id,
                ledger_version=run.ledger_version,
                job_id=run.job_id,
                provider=run.provider,
                model=run.model,
                bundle=run.bundle,
                output={
                    **run.output,
                    "recommendation": "Cystectomie.\n\n# 6 — CADRE\n\nValidé.",
                },
                session_blob_key=run.session_blob_key,
            )
        )
        await session.commit()

    body = render_markdown(await build_document(session_factory, case))

    assert body.count("# 6 — CADRE DE LA PRISE EN CHARGE") == 1
    assert "\n# 6 — CADRE\n" not in body


async def test_an_opinion_formed_on_older_data_says_so(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering the newest ledger beside an opinion formed on an older one, under the
    newer version number, misrepresents what the agent saw."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        stored_user = await session.get(User, user.id)
        stored_case = await session.get(Case, case.id)
        assert stored_user is not None and stored_case is not None
        await apply_correction(
            session,
            case=stored_case,
            user=stored_user,
            fact_type="who_status",
            value="2",
            note="Réévalué en consultation.",
        )
        await record_version(session, case=stored_case, change_reason="Correction")
        await session.commit()

    document = await build_document(session_factory, case)
    body = render_markdown(document)

    # The document reports the version the agent read, and names the newer one.
    assert document.ledger_version == 1
    assert document.superseded_by == 2
    assert "Données modifiées depuis cette analyse" in body
    assert "Relancez l'analyse" in body
    assert "dépassée — version courante 2" in body
    # And it shows what the agent saw, not what the ledger says now: the corrected value
    # was not in front of the agent, so it must not appear as though it had been.
    assert "OMS : 2" not in body


async def test_a_current_document_carries_no_stale_warning(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)

    assert document.superseded_by is None
    assert "Données modifiées" not in render_markdown(document)


async def test_the_question_asked_is_the_question_reported(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Substituting a generic sentence would answer a different question from the one the
    RCP was convened for."""
    user, case = await seed_case(session_factory)
    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        assert stored is not None
        stored.clinical_question = "Cystectomie ou préservation vésicale à 85 ans ?"
        await session.commit()
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)

    assert document.synthesis.question == "Cystectomie ou préservation vésicale à 85 ans ?"


async def test_no_pps_without_an_agent_opinion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An empty document in this shape would be a form inviting someone to fill it in."""
    user, case = await seed_case(session_factory)

    with pytest.raises(generator.NothingToReport):
        await build_document(session_factory, case)


# --------------------------------------------------------------------------------------
# The six sections
# --------------------------------------------------------------------------------------


async def test_all_six_sections_are_present_and_numbered(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    body = render_markdown(await build_document(session_factory, case))

    for number, title in enumerate(
        [
            "SYNTHÈSE ET QUESTION POSÉE",
            "DÉCISION RCP",
            "PROPOSITIONS DE TRAITEMENTS",
            "PROPOSITION D'ACCOMPAGNEMENT / SOINS DE SUPPORT",
            "ÉVALUATION SOCIALE ET FAMILIALE",
            "CADRE DE LA PRISE EN CHARGE",
        ],
        start=1,
    ):
        assert f"# {number} — {title}" in body


async def test_the_synthesis_identifies_the_patient_without_naming_them(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)

    assert document.synthesis.patient == "Femme, 85 ans"
    assert "Carcinome urothélial" in (document.synthesis.diagnosis or "")


async def test_every_value_in_the_summary_says_what_it_is(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Joined bare, this line reads `Carcinome urothélial. p T2. Haut. Oui.` — and "Oui"
    to what? A summary the reader must decode is worse than none, because it still looks
    like one."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    diagnosis = (await build_document(session_factory, case)).synthesis.diagnosis or ""

    # The histology stands alone; everything after it is labelled.
    assert diagnosis.startswith("Carcinome urothélial")
    assert "Infiltration du muscle : Oui" in diagnosis
    assert "Grade : Haut" in diagnosis
    # And an acronym stays an acronym: `Ptnm` reads as a typo to the clinicians it is for.
    assert "pTNM : p T2" in diagnosis


async def test_the_recommendation_carries_the_sources_behind_it(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recommendation -> citation id -> the sentence in the dossier. The loop has to close
    on the page, not only in the database."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)
    body = render_markdown(document)

    assert document.evidence, "the agent cited something"
    entry = document.evidence[0]
    assert entry.quote == DOSSIER_TEXT, "the citation preserves the complete patient source"
    assert f"`{entry.id}`" in body
    assert entry.quote.replace("\n", " ").strip() in body


async def test_a_withheld_recommendation_is_not_turned_into_a_plan(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering "no safe recommendation" as a treatment plan would invent the very thing
    the agent declined to state."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        run = await session.scalar(sa.select(AgentRun).where(AgentRun.case_id == case.id))
        assert run is not None and run.output is not None
        withheld = {
            **run.output,
            "recommendation": "Aucune recommandation possible en l'état.",
            "recommendation_withheld": True,
            "missing_information": ["Bilan d'extension incomplet"],
        }
        # agent_runs is append-only, so a different opinion is a different run.
        session.add(
            AgentRun(
                case_id=run.case_id,
                organization_id=run.organization_id,
                agent_role=run.agent_role,
                status=AgentRunStatus.succeeded,
                ledger_version_id=run.ledger_version_id,
                ledger_version=run.ledger_version,
                job_id=run.job_id,
                provider=run.provider,
                model=run.model,
                bundle=run.bundle,
                output=withheld,
                session_blob_key=run.session_blob_key,
            )
        )
        await session.commit()

    document = await build_document(session_factory, case)
    body = render_markdown(document)

    assert document.treatments.withheld is True
    # No treatment content at all. An agent that declines often still writes paragraphs
    # about what could be considered, and printing them under a heading saying it declined
    # gives the reader a document that disclaims a plan and then prints one.
    assert document.treatments.steps == []
    assert document.treatments.rejected == []
    assert "Aucune proposition thérapeutique n'est formulée" in body
    # What is missing takes its place.
    assert "Bilan d'extension incomplet" in body
    # The agent's own words survive in the reasoning, where they are marked as reasoning.
    appendix = body.split("# ANNEXE")[1]
    assert "Aucune recommandation possible en l'état." in appendix
    assert document.appendices[0].withheld_statement is not None


async def test_a_withheld_section_cannot_be_built_with_treatment_content(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Enforced by the model, so no future caller can reintroduce it."""
    with pytest.raises(ValidationError, match="no treatment steps"):
        Treatments(
            status="…",
            withheld=True,
            steps=[TreatmentStep(title="Cystectomie", items=["…"])],
        )


# --------------------------------------------------------------------------------------
# Gaps stay visible
# --------------------------------------------------------------------------------------


async def test_a_field_nobody_filled_in_is_marked_not_written_around(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document that reads as complete is the failure that matters: the reader skims it,
    sees no gap, and proceeds."""
    user, case = await seed_case(
        session_factory,
        text=(
            "**Type histologique** : Carcinome urothélial\n"
            "**Infiltration du muscle** : Oui\n"
        ),
    )
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)
    body = render_markdown(document)

    assert generator.NOT_RECORDED in body
    assert any(row.missing for row in document.social.rows)


async def test_the_missing_information_report_carries_red_and_amber(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)
    body = render_markdown(document)

    assert document.missing, "this dossier omits required fields"
    assert "# INFORMATIONS MANQUANTES ET INCERTAINES" in body
    assert "🔴" in body


async def test_section_six_does_not_claim_a_guideline_it_never_had(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No evidence corpus exists yet, so the agents reasoned from the dossier alone.
    Naming a référentiel would be false."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    document = await build_document(session_factory, case)

    assert "Non référencé" in document.framework.reference_standard


# --------------------------------------------------------------------------------------
# Proposals say what they were derived from
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "referral",
    [
        geriatric_referral(age="85 ans", g8=None),
        geriatric_referral(age="62 ans", g8="5"),
        geriatric_referral(age=None, g8=None),
        genetic_referral(family_history="RAS"),
        genetic_referral(family_history="Mere : cancer du sein a 45 ans"),
        genetic_referral(family_history=None),
        fertility_referral(age="34 ans", sex="Homme"),
        fertility_referral(age="46 ans", sex="Femme"),
        fertility_referral(age="85 ans", sex="Femme"),
    ],
)
def test_rcpia_never_ticks_a_decision_box(referral) -> None:
    """No threshold has a clinician's name against it, so the checkbox stays undetermined.

    An earlier version let an unsigned threshold tick the box and printed a caveat beneath
    it. The document then said RCPia was not deciding while showing the mark that means a
    decision, and the mark is what a reader sees.
    """
    assert referral.answer == "a_determiner"
    assert referral.authority is not Authority.clinician_approved


def test_the_checkbox_cannot_be_set_without_a_clinician() -> None:
    """Enforced by the model, so no future rule can reintroduce it."""
    with pytest.raises(ValidationError, match="only a clinician-approved rule"):
        Referral(
            label="Avis oncogeriatrique",
            answer="oui",
            justification="Score G8 a 5.",
            authority=Authority.project_suggestion,
        )


def test_a_rule_with_no_authority_may_not_even_suggest() -> None:
    with pytest.raises(ValidationError, match="may not suggest"):
        Referral(
            label="Avis oncogenetique",
            answer="a_determiner",
            justification="...",
            suggestion="oui",
            authority=Authority.none,
        )


def test_genetics_suggests_nothing_at_all() -> None:
    """A correct rule needs the relationship, the tumour type, the age at diagnosis and the
    family pattern. Keyword matching on free text returned Oui for a spinal column, a
    benign breast tumour, and an isolated prostate cancer at 88 - word boundaries fix only
    the first of those."""
    for history in [
        "Pere : HTA",
        "Mere : cancer du sein a 45 ans",
        "Antecedent de colonne vertebrale operee",
        "frein lingual",
        "Tumeur benigne du sein chez la tante",
        "Pere : cancer de la prostate a 88 ans",
        "RAS",
        None,
    ]:
        referral = genetic_referral(family_history=history)
        assert referral.answer == "a_determiner"
        assert referral.suggestion is None
        assert referral.authority is Authority.none

    # The recorded text still reaches the RCP so a clinician can judge it.
    assert "cancer du sein" in genetic_referral(
        family_history="Mere : cancer du sein a 45 ans"
    ).justification


def test_a_missing_or_normal_g8_never_closes_the_question() -> None:
    """Absence of screening is not a negative screening result. A 70-year-old with no G8
    used to receive a ticked Non."""
    for age, g8 in [("70 ans", None), ("62 ans", None), (None, None), ("62 ans", "16")]:
        referral = geriatric_referral(age=age, g8=g8)
        assert referral.suggestion != "non", f"{age} / G8 {g8} closed the question"
        assert referral.answer == "a_determiner"


def test_a_low_g8_raises_the_question_as_a_suggestion() -> None:
    referral = geriatric_referral(age="62 ans", g8="5")

    assert referral.suggestion == "oui"
    assert referral.answer == "a_determiner", "a suggestion is not a decision"
    assert referral.basis == "G8 : 5"


def test_fertility_preservation_is_never_closed() -> None:
    """Wrongly declining to raise it is permanent; wrongly raising it costs a conversation.
    So the rule opens the question or leaves it open - it never suggests Non."""
    for age, sex in [
        ("34 ans", "Homme"), ("46 ans", "Femme"), ("85 ans", "Femme"),
        ("62 ans", "Homme"), (None, "Femme"), ("70 ans", None),
    ]:
        referral = fertility_referral(age=age, sex=sex)
        assert referral.suggestion != "non", f"{age} {sex} closed the question"

    assert fertility_referral(age="34 ans", sex="Homme").suggestion == "oui"
    assert fertility_referral(age="85 ans", sex="Femme").suggestion is None


def test_an_observation_still_puts_the_evidence_in_front_of_the_rcp() -> None:
    """Declining to answer is not declining to be useful: the question is asked with the
    recorded value beside it."""
    geriatric = geriatric_referral(age="85 ans", g8="5")

    assert "5" in geriatric.justification
    assert geriatric.basis == "G8 : 5"
    assert str(G8_THRESHOLD)[0] in geriatric.justification


def test_ras_and_an_empty_field_remain_distinguishable() -> None:
    """The distinction the whole ledger turns on, carried into section 2."""
    assert "RAS" in genetic_referral(family_history="RAS").justification
    assert "non renseigné" in genetic_referral(family_history=None).justification.lower()


def test_a_contested_value_is_no_basis_for_an_observation() -> None:
    """Reasoning from one arbitrary reading, and printing it as the reason, presents as
    settled the very thing a clinician still has to settle."""
    contested = frozenset({"age", "g8_score", "family_history", "sex"})

    geriatric = geriatric_referral(age="85 ans", g8="5", contested=contested)
    genetic = genetic_referral(family_history="Mère : cancer", contested=contested)
    fertility = fertility_referral(age="46 ans", sex="Femme", contested=contested)

    for referral in (geriatric, genetic, fertility):
        assert referral.basis is None
        assert "contradictoire" in referral.justification


def test_every_referral_states_a_justification() -> None:
    """An observation a reader cannot trace is indistinguishable from an assertion."""
    referrals = [
        geriatric_referral(age="85 ans", g8=None),
        genetic_referral(family_history="RAS"),
        fertility_referral(age="85 ans", sex="Femme"),
        geriatric_referral(age=None, g8=None),
    ]

    assert all(referral.justification for referral in referrals)


def test_numbers_are_read_out_of_the_text_clinicians_write() -> None:
    assert number_in("85 ans") == 85.0
    assert number_in("17,33") == 17.33
    assert number_in("5/17") == 5.0
    assert number_in("RAS") is None
    assert number_in(None) is None


def test_a_digit_inside_a_label_or_a_stage_is_not_a_number() -> None:
    """Without this, `G8 : 5/17` reads as 8 and a T2 stage becomes a score of 2."""
    assert number_in("G8 : 5/17") == 5.0
    assert number_in("p T2") is None
    assert number_in("cT4a N1 M1a") is None


async def test_a_referral_proposal_shows_its_basis_on_the_page(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    body = render_markdown(await build_document(session_factory, case))

    assert "Fondé sur — Âge : 85 ans" in body
    assert "RCPia ne se prononce pas" in body


# --------------------------------------------------------------------------------------
# Support care is derived, not templated
# --------------------------------------------------------------------------------------


def test_support_items_fire_on_recorded_values_and_say_which() -> None:
    items = support_items({"haemoglobin": "8.4", "albumin": "24", "egfr": "30"})
    titles = {item.title: item for item in items}

    assert "Transfusion sanguine" in titles
    assert titles["Transfusion sanguine"].trigger == "Hb : 8.4 g/dL"
    assert "Support nutritionnel" in titles
    assert "Surveillance néphrologique" in titles


def test_a_triggered_item_states_the_threshold_and_that_it_is_unvalidated() -> None:
    """Hb 9.9 and Hb 10.1 are not different clinical situations. An item that instructs on
    one side of an unapproved line pretends they are, so it evaluates rather than
    prescribes and shows the line it used."""
    item = next(
        item for item in support_items({"haemoglobin": "9.9"}) if item.trigger
    )

    assert SUGGESTION_CAVEAT in (item.caveat or "")
    # In words, not "<": the renderer escapes angle brackets, and `Hb &lt; 10` is not
    # something to show a clinician.
    assert "Hb inférieure à 10 g/dL" in (item.caveat or "")
    # Phrased as an assessment, not an instruction.
    assert "évaluer l'indication" in item.detail


def test_a_contested_value_triggers_nothing() -> None:
    """Firing on one arbitrary reading, and printing it as the reason, presents a
    contradiction as a settled fact."""
    items = support_items({"haemoglobin": "8.4"}, contested=frozenset({"haemoglobin"}))

    assert not any(item.trigger for item in items)


def test_untriggered_items_are_simply_absent() -> None:
    """Emitting all seven blocks for every case would be a template, not a proposal."""
    items = support_items({"haemoglobin": "13.5", "albumin": "42", "egfr": "95"})

    assert not any(item.trigger for item in items)
    # The standing items still apply to anyone entering an oncology pathway.
    assert {item.title for item in items} == {
        "Prise en charge de la douleur",
        "Soutien psychologique",
        "Accompagnement social",
    }


def test_two_triggers_for_one_need_do_not_repeat_the_heading() -> None:
    items = support_items({"albumin": "24", "mna": "7"})

    assert [item.title for item in items].count("Support nutritionnel") == 1


def test_nutrition_advice_does_not_repeat_the_reference_documents_error() -> None:
    """One reference PPS prescribes IV albumin as repletion. It is not nutrition."""
    items = support_items({"albumin": "24"})
    nutrition = next(item for item in items if item.title == "Support nutritionnel")

    assert "orau" in nutrition.detail  # compléments nutritionnels oraux
    assert "n'est pas un support nutritionnel" in nutrition.detail


# --------------------------------------------------------------------------------------
# Reading it over the API
# --------------------------------------------------------------------------------------


async def test_the_pps_is_readable_as_json_and_downloadable_as_markdown(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)
    await sign_in(client, user)

    document = await client.get(f"/cases/{case.id}/pps")
    download = await client.get(f"/cases/{case.id}/pps/markdown")

    assert document.status_code == 200
    assert document.json()["ledger_version"] == 1
    assert document.json()["banner"].startswith("DOCUMENT PROVISOIRE")

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/markdown")
    assert "PPS-provisoire" in download.headers["content-disposition"]
    assert "# FICHE RCP" in download.text


async def test_the_fiche_and_the_probatoire_are_both_downloadable_as_word_files(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two Word files a clinician actually takes into an RCP, over the HTTP path a
    browser uses -- not just the renderer called directly."""
    import io

    from docx import Document as WordDocument

    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)
    await sign_in(client, user)

    reference = (await client.get(f"/cases/{case.id}/pps")).json()
    fiche = await client.get(f"/cases/{case.id}/pps/docx")
    probatoire = await client.get(f"/cases/{case.id}/pps/docx/probatoire")

    word_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert fiche.status_code == 200
    assert fiche.headers["content-type"] == word_type
    assert "Fiche-RCP" in fiche.headers["content-disposition"]
    assert probatoire.status_code == 200
    assert probatoire.headers["content-type"] == word_type
    assert "Dossier-probatoire" in probatoire.headers["content-disposition"]

    def _full_text(word_document) -> str:
        # The header, and the case reference inside it, live in a table -- paragraphs
        # alone would silently miss most of what a reader actually sees.
        paragraphs = "\n".join(p.text for p in word_document.paragraphs)
        cells = "\n".join(
            cell.text
            for table in word_document.tables
            for row in table.rows
            for cell in row.cells
        )
        return paragraphs + "\n" + cells

    # Bytes a real Word installation can open, not merely bytes with the right header.
    fiche_doc = WordDocument(io.BytesIO(fiche.content))
    probatoire_doc = WordDocument(io.BytesIO(probatoire.content))
    fiche_text = _full_text(fiche_doc)
    probatoire_text = _full_text(probatoire_doc)

    assert "FICHE RCP" in fiche_text
    assert "DOSSIER PROBATOIRE" in probatoire_text
    # Both name the same case and the same version of the data: this is what lets the two
    # files be matched to each other after the fact.
    assert case.reference in fiche_text
    assert case.reference in probatoire_text
    version = str(reference["ledger_version"])
    assert version in fiche_text
    assert version in probatoire_text


async def test_a_case_with_no_analysis_says_so_rather_than_returning_an_empty_form(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case(session_factory)
    await sign_in(client, user)

    response = await client.get(f"/cases/{case.id}/pps")

    assert response.status_code == 409
    assert "lancez une analyse" in response.json()["detail"]


async def test_another_organizations_pps_is_not_readable(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await seed_case(session_factory)
    _, other_case = await seed_case(session_factory)
    await run_the_agent(session_factory, other_case, adapter, blobs, monkeypatch)
    await sign_in(client, user)

    response = await client.get(f"/cases/{other_case.id}/pps")

    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------------------


async def test_the_document_names_what_it_was_built_from(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is persisted, so the inputs are printed instead: the same ledger version
    and the same runs assemble the same clinical content."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    first = await build_document(session_factory, case)
    second = await build_document(session_factory, case)

    assert first.ledger_version == second.ledger_version
    assert first.agent_run_ids == second.agent_run_ids
    assert render_markdown(first) == render_markdown(second)


async def test_the_downloaded_document_names_the_runs_and_the_generator(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is persisted, so what produced the document has to travel inside it.
    Otherwise a downloaded copy cannot be matched later to the runs and the code behind
    it, and "regenerate and compare" compares unknowns."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)
    await sign_in(client, user)

    document = await build_document(session_factory, case)
    body = (await client.get(f"/cases/{case.id}/pps/markdown")).text

    assert document.agent_run_ids
    for run_id in document.agent_run_ids:
        assert run_id in body
    assert document.generator_version in body
    assert "Version du générateur" in body


async def test_several_specialists_are_refused_until_a_synthesis_exists(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whichever run finished last would otherwise lead section 3, letting the job queue
    decide which speciality a clinician reads first."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        run = await session.scalar(sa.select(AgentRun).where(AgentRun.case_id == case.id))
        assert run is not None and run.output is not None
        session.add(
            AgentRun(
                case_id=run.case_id,
                organization_id=run.organization_id,
                agent_role="medical_oncology",
                status=AgentRunStatus.succeeded,
                ledger_version_id=run.ledger_version_id,
                ledger_version=run.ledger_version,
                job_id=run.job_id,
                provider=run.provider,
                model=run.model,
                bundle=run.bundle,
                output={**run.output, "agent_role": "medical_oncology"},
                session_blob_key=run.session_blob_key,
            )
        )
        await session.commit()

    with pytest.raises(generator.SynthesisRequired, match="synthèse"):
        await build_document(session_factory, case)

    await sign_in(client, user)
    response = await client.get(f"/cases/{case.id}/pps")
    assert response.status_code == 409
    assert "Oncologie médicale" in response.json()["detail"]


async def test_only_the_newest_opinion_per_specialist_is_rendered(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two opinions from one specialist would ask the reader to arbitrate between them."""
    user, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        run = await session.scalar(sa.select(AgentRun).where(AgentRun.case_id == case.id))
        assert run is not None
        session.add(
            AgentRun(
                case_id=run.case_id,
                organization_id=run.organization_id,
                agent_role=run.agent_role,
                status=AgentRunStatus.succeeded,
                ledger_version_id=run.ledger_version_id,
                ledger_version=run.ledger_version,
                job_id=run.job_id,
                provider=run.provider,
                model=run.model,
                bundle=run.bundle,
                output={**(run.output or {}), "recommendation": "Avis révisé."},
                session_blob_key=run.session_blob_key,
            )
        )
        await session.commit()

    document = await build_document(session_factory, case)

    assert len(document.agent_run_ids) == 1
    assert document.treatments.steps[0].items == ["Avis révisé."]


async def test_a_ledger_version_is_required(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The PPS reports on a snapshot, so there has to be one."""
    user, case = await seed_case(session_factory)

    async with session_factory() as session:
        stored = await session.get(Case, case.id)
        assert stored is not None
        versions = await session.execute(
            sa.select(CaseLedgerVersion).where(CaseLedgerVersion.case_id == case.id)
        )
        assert versions.scalars().first() is not None, "extraction wrote one"


# --------------------------------------------------------------------------------------
# Agent labels — the document is French, and so must every role name in it
# --------------------------------------------------------------------------------------

# The routing vocabulary, imported rather than restated. It lived here as a literal tuple for
# exactly as long as nothing owned it; `rcpia.corpus.vocabulary` now does, so a role added there
# is checked here without anyone remembering to update two places.
CORPUS_AGENT_ROLES = tuple(sorted(AGENT_ROLES))


def test_every_role_the_corpus_can_route_to_has_a_french_label() -> None:
    """**A missing label does not fail — it prints English.**

    `agent_label` falls back to `role.replace("_", " ").capitalize()`, so a role absent from
    `AGENT_LABELS` renders as "Geriatric frailty" or "Palliative supportive" inside a French
    clinical document handed to a clinician. Five of the ten roles did exactly that until the
    map was checked against the corpus vocabulary, and nothing anywhere failed.
    """
    missing = [role for role in CORPUS_AGENT_ROLES if role not in AGENT_LABELS]

    assert missing == [], f"roles rendering in English: {missing}"


def test_no_label_is_the_english_fallback() -> None:
    """The fallback is legible, which is why its output survives review. Catching it needs a
    test that compares against the fallback rather than merely checking a key exists."""
    for role in CORPUS_AGENT_ROLES:
        assert agent_label(role) != role.replace("_", " ").capitalize(), (
            f"{role} is rendering through the English fallback"
        )


# --- The panel block ---------------------------------------------------------------------------


def _synthesis(**overrides: object) -> SynthesisOutput:
    payload: dict = {
        "agent_role": SYNTHESIS_ROLE,
        "case_id": "case-1",
        "recommendation": "Cystectomie après optimisation.",
        "rationale": "Motif.",
        "convergences": [
            {
                "point": "Pas de cisplatine en l'état.",
                "agents_agreeing": ["urology_oncology", "medical_oncology"],
                # Every named speciality carries a quotation from its own opinion; the
                # schema refuses a convergence that cannot show one.
                "support": [
                    {"role": "urology_oncology", "quote": "DFG sous le seuil usuel"},
                    {"role": "medical_oncology", "quote": "cisplatine non eligible"},
                ],
                "evidence_summary": "DFG sous le seuil usuel.",
            }
        ],
        "coverage_gaps": [
            {
                "point": "Le carboplatine ne remplace pas le cisplatine.",
                "raised_by": [
                    {"role": "medical_oncology", "quote": "cisplatine non eligible"}
                ],
                "silent": ["urology_oncology"],
                "clinical_impact": "Examiné par une seule spécialité.",
            }
        ],
    }
    payload.update(overrides)
    return SynthesisOutput.model_validate(payload)


def test_the_panel_block_names_who_held_each_point() -> None:
    """The defect this block exists for was invisible in the fiche: a speciality was recorded
    as agreeing to a point it never mentioned, and nothing on the page showed it."""
    panel = build_panel(
        _synthesis(), ["rcp_synthesis", "urology_oncology", "medical_oncology"]
    )

    kinds = [point.kind for point in panel.points]
    assert kinds == ["convergence", "gap"]
    assert "Oncologie urologique" in panel.points[0].attribution
    assert "non abordé par Oncologie urologique" in panel.points[1].attribution


def test_the_synthesis_is_not_listed_among_the_opinions_it_compared() -> None:
    """It is the agent doing the comparing. Listing it read as a third speciality agreeing."""
    panel = build_panel(
        _synthesis(), ["rcp_synthesis", "urology_oncology", "medical_oncology"]
    )
    assert "Rcp synthesis" not in panel.specialities
    assert panel.specialities == ["Oncologie urologique", "Oncologie médicale"]


def test_the_panel_names_the_specialities_that_were_never_asked() -> None:
    """Naming only the contributors makes any panel read as a complete one.

    Two specialities of eight look like a board rather than a quarter of one, and the illusion
    strengthens as agents are added: seven names look like all of them. A reader cannot weigh a
    silence without knowing who was never invited to break it.
    """
    panel = build_panel(
        _synthesis(), ["rcp_synthesis", "urology_oncology", "medical_oncology"]
    )
    assert panel.specialities == ["Oncologie urologique", "Oncologie médicale"]
    assert panel.absent == [
        "Radiothérapie",
        "Radiologie",
        "Anatomopathologie",
        "Oncogériatrie",
        "Nutrition clinique",
        "Soins de support et palliatifs",
    ]
    # The synthesis is not a speciality, so it is neither seated nor missing.
    assert "Rcp synthesis" not in panel.absent


def test_an_agent_that_was_asked_and_crashed_is_not_an_agent_nobody_asked() -> None:
    """`latest_runs` selects successes, so a crashed agent leaves no trace in the document.

    That was a silent omission until the fiche began naming which specialities did not
    contribute. From then on it became a positive claim, and a false one: the board would read
    *not asked* where the truth is *asked, and the answer never came back*. Nobody can recover
    the opinion of a speciality that was never wired; this one exists to be recovered.
    """
    panel = build_panel(
        _synthesis(),
        ["rcp_synthesis", "urology_oncology", "medical_oncology"],
        ["radiation_oncology"],
    )
    assert panel.failed == ["Radiothérapie"]
    assert "Radiothérapie" not in panel.absent
    # The denominator is unchanged: eight are expected however the shortfall is explained.
    assert len(panel.specialities) + len(panel.absent) + len(panel.failed) == len(
        EXPECTED_PANEL
    )


def test_the_fiche_asks_for_a_re_run_only_where_an_opinion_was_expected() -> None:
    """The two sentences ask different things of the reader, so they are two sentences."""
    document = _minimal_document()
    document.panel = PanelAgreement(
        points=[PanelPoint(kind="gap", point="p", attribution="a")],
        specialities=["Oncologie urologique"],
        absent=["Nutrition clinique"],
        failed=["Radiothérapie"],
    )
    body = render_markdown(document)
    assert "Panel incomplet : 1 spécialité(s) sur 3" in body
    assert "Non sollicitées à ce jour : Nutrition clinique" in body
    assert "Avis attendu et non obtenu : Radiothérapie" in body
    assert "relancez l'analyse" in body


async def test_a_failed_run_reaches_the_fiche_as_a_missing_answer(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: PiAdapter,
    blobs: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the database, because the distinction is drawn by a query, not by a caller.

    A crashed run is invisible to `latest_runs`, so without this the fiche states that the
    speciality was never consulted -- on a case where it was consulted and the analysis failed.
    """
    _, case = await seed_case(session_factory)
    await run_the_agent(session_factory, case, adapter, blobs, monkeypatch)

    async with session_factory() as session:
        version = (
            await session.execute(
                sa.select(CaseLedgerVersion)
                .where(CaseLedgerVersion.case_id == case.id)
                .order_by(CaseLedgerVersion.version.desc())
            )
        ).scalars().first()
        assert version is not None
        session.add(
            AgentRun(
                case_id=case.id,
                organization_id=case.organization_id,
                agent_role="radiation_oncology",
                status=AgentRunStatus.failed,
                ledger_version_id=version.id,
                ledger_version=version.version,
                bundle={},
                error_message="le processus ne s'est pas terminé",
            )
        )
        await session.commit()

    document = await build_document(session_factory, case)
    assert "Radiothérapie" in document.framework.participants
    assert "échec technique" in document.framework.participants
    # Named as a failure, not folded into the specialities nobody asked.
    assert "non sollicitées : Radiothérapie" not in document.framework.participants


def test_a_complete_panel_carries_no_shortfall() -> None:
    """A full board is not made to carry a disclaimer about nothing."""
    panel = build_panel(_synthesis(), [SYNTHESIS_ROLE, *EXPECTED_PANEL])
    assert panel.absent == []


def test_the_fiche_states_the_shortfall_where_it_names_the_participants() -> None:
    """The panel block only exists for a panel. A single-agent fiche has none, and then this
    field is the only place saying who was consulted."""
    framework = build_framework(agent_roles=["urology_oncology"], models=["claude-opus-5"])
    assert "Panel incomplet : 1 spécialité(s) sur 8" in framework.participants
    assert "Oncogériatrie" in framework.participants


def test_a_single_specialist_produces_no_panel_block() -> None:
    """One agent has nothing to converge with, and a block announcing unanimity among a single
    speciality would be worse than no block."""
    lone = AgentOutput.model_validate(
        {
            "agent_role": "urology_oncology",
            "case_id": "case-1",
            "recommendation": "Cystectomie.",
            "rationale": "Motif.",
        }
    )
    assert build_panel(lone, ["urology_oncology"]).points == []


def test_the_rendered_block_states_that_silence_is_not_consent() -> None:
    """A reader who never opens a JSON file has to be able to read the provenance rule."""
    document = _minimal_document()
    document.panel = build_panel(
        _synthesis(), ["rcp_synthesis", "urology_oncology", "medical_oncology"]
    )
    body = render_markdown(document)

    assert "Concordance du panel" in body
    assert "n'y a pas consenti" in body
    assert "Non traité par tous" in body
    # The block belongs before the plan: a reader should know what was disputed before
    # reading what is proposed.
    assert body.index("Concordance du panel") < body.index("PROPOSITIONS DE TRAITEMENTS")


# --------------------------------------------------------------------------------------
# Computed scores on the fiche
# --------------------------------------------------------------------------------------

PROSTATE_FACTS = {
    "age": "70",
    "psa_total": "7.53",
    "gleason": "7 (3+4)",
    "ct_dre": "T2a",
    "cores_positive": "5",
    "cores_total": "21",
    "hormone_therapy": "Non",
    "radiation_therapy": "Non",
    # Without this, both nomograms now correctly refuse to compute (finding 3): histology is
    # an eligibility question, and an unrecorded one is `NOT_POSSIBLE`, not `CALCULATED`.
    "histology": "Adénocarcinome acinaire",
}


def test_a_computed_score_reaches_the_fiche_with_its_model_named() -> None:
    """**A percentage on a tumour-board document is indistinguishable from an estimate**
    unless it carries its source, and the whole reason this layer exists is that a language
    model asked for a probability produces a convincing one."""
    from rcpia.pps.generator import build_nomograms

    figures = build_nomograms(
        dict(PROSTATE_FACTS),
        cancer_type="prostate",
        # The clinician's answer to the eligibility question. Without it both calculators
        # correctly refuse, and this test would assert the gate rather than the score.
        adenocarcinoma_confirmed=Tristate.yes,
    )
    briganti = next(f for f in figures if "Briganti" in f.display_name)

    assert briganti.estimates["lymph_node_invasion_percent"] == pytest.approx(3.06, abs=0.01)
    assert briganti.inputs_used["psa_ng_ml"] == "7.53"


def test_the_reconstruction_caveat_is_printed_not_stored() -> None:
    """Briganti 2012's coefficients were read off a published graphic rather than released as
    an equation. A reader who cannot see that cannot weigh the number, so it travels onto the
    page rather than living in a docstring."""
    from rcpia.pps.generator import build_nomograms
    from rcpia.pps.render import _nomograms

    class Document:
        nomograms = build_nomograms(dict(PROSTATE_FACTS), cancer_type="prostate")

    rendered = "\n".join(_nomograms(Document()))

    assert "Coefficients reconstruits" in rendered
    assert "non une décision" in rendered


def test_a_refusal_is_printed_and_names_what_is_missing() -> None:
    """An absent row is a silence nobody questions; "manque l'antécédent d'hormonothérapie"
    is an instruction the board can act on."""
    from rcpia.pps.generator import build_nomograms
    from rcpia.pps.render import _nomograms

    silent = {k: v for k, v in PROSTATE_FACTS.items() if "therapy" not in k}

    class Document:
        nomograms = build_nomograms(silent, cancer_type="prostate")

    rendered = "\n".join(_nomograms(Document()))

    assert "non calculé" in rendered
    assert "antécédent d'hormonothérapie" in rendered


def test_the_board_reads_french_not_schema_keys() -> None:
    """The calculators name their inputs in the published models' terms, which are code. The
    fiche is the one place those must not appear."""
    from rcpia.pps.generator import build_nomograms
    from rcpia.pps.render import _nomograms

    class Document:
        nomograms = build_nomograms(
            dict(PROSTATE_FACTS),
            cancer_type="prostate",
            adenocarcinoma_confirmed=Tristate.yes,
        )

    rendered = "\n".join(_nomograms(Document()))

    assert "psa_ng_ml" not in rendered
    assert "hormone_therapy" not in rendered
    assert "PSA 7.53" in rendered


def test_a_bladder_fiche_carries_no_prostate_score() -> None:
    """Briganti and the MSKCC prostate models have nothing to say about a bladder tumour, and
    an empty block prints nothing at all."""
    from rcpia.pps.generator import build_nomograms
    from rcpia.pps.render import _nomograms

    class Document:
        nomograms = build_nomograms({"psa_total": "7.5"}, cancer_type="bladder")

    assert Document.nomograms == []
    assert _nomograms(Document()) == []
