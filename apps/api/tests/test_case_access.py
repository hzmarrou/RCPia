"""Creator isolation covers every case route, not just the dashboard list."""

import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.agents import routes as agent_routes
from rcpia.cases import service
from rcpia.core.models import (
    AgentRun,
    AnalysisJob,
    AnalysisPanel,
    AuditEvent,
    Case,
    CaseLedgerVersion,
    ExtractedFact,
    Organization,
    UploadedDocument,
    User,
    UserRole,
)
from rcpia.main import app
from rcpia.pps import generator
from tests.test_pps_generator import _minimal_document
from tests.test_route_protection import api_routes
from tests.test_upload import (
    PASSWORD,
    PDF_BYTES,
    create_case,
    make_user,
    sign_in,
    upload,
)
from tests.test_upload import blob_store as blob_store
from tests.test_upload import client as client
from tests.test_upload import reset_throttle as reset_throttle

CASE_REQUESTS = (
    ("GET", "/cases/{case_id}", None),
    ("GET", "/cases/{case_id}/activity", None),
    ("GET", "/cases/{case_id}/documents/{document_id}/content", None),
    ("POST", "/cases/{case_id}/documents", None),
    ("GET", "/cases/{case_id}/ledger", None),
    ("POST", "/cases/{case_id}/ledger/corrections",
     {"fact_type": "muscle_invasion", "value": "Non", "note": "Correction synthétique"}),
    ("GET", "/cases/{case_id}/analysis", None),
    ("POST", "/cases/{case_id}/analysis", {"agent_role": "urology_oncology"}),
    ("POST", "/cases/{case_id}/analysis/panel",
     {"roles": ["urology_oncology", "medical_oncology", "radiation_oncology"]}),
    ("GET", "/cases/{case_id}/pps", None),
    ("GET", "/cases/{case_id}/pps/markdown", None),
    ("GET", "/cases/{case_id}/pps/docx", None),
    ("GET", "/cases/{case_id}/pps/docx/probatoire", None),
)


@dataclass
class AccessFixture:
    users: dict[str, User]
    cases: dict[str, uuid.UUID]
    document_id: str


@pytest.fixture
async def world(client: AsyncClient, session_factory) -> AccessFixture:
    owner = await make_user(session_factory)
    users = {"owner": owner}
    async with session_factory() as session:
        other = Organization(name="Other synthetic test organization")
        session.add(other)
        await session.flush()
        for name, role, org in (
            ("peer", UserRole.uploader, owner.organization_id),
            ("admin", UserRole.admin, owner.organization_id),
            ("viewer", UserRole.viewer, owner.organization_id),
            ("foreign_uploader", UserRole.uploader, other.id),
            ("foreign_admin", UserRole.admin, other.id),
        ):
            user = User(
                organization_id=org, email=f"{name}-{uuid.uuid4().hex[:8]}@example.org",
                role=role, is_active=True, password_hash=owner.password_hash,
            )
            session.add(user)
            users[name] = user
        await session.commit()
        cases = {}
        for name in ("peer", "admin", "foreign_uploader"):
            case = await service.create_case(
                session, user=users[name], reference=f"PRIVATE-{name}",
                cancer_type="bladder", clinical_question=None,
            )
            cases[name] = case.id
        legacy = Case(
            organization_id=owner.organization_id, reference="LEGACY-NO-CREATOR",
            cancer_type="bladder",
        )
        session.add(legacy)
        await session.commit()
        cases["legacy"] = legacy.id

    await sign_in(client, owner)
    created = await create_case(client, reference="PRIVATE-OWNER")
    cases["owner"] = uuid.UUID(created["id"])
    uploaded = await upload(client, created["id"], "synthetic.pdf", PDF_BYTES)
    assert uploaded.status_code == 201
    for key, value in (("histology", "Carcinome urothélial"), ("muscle_invasion", "Oui")):
        correction = await client.post(
            f"/cases/{created['id']}/ledger/corrections",
            json={"fact_type": key, "value": value, "note": "Donnée de test synthétique"},
        )
        assert correction.status_code == 201
    await client.post("/auth/logout")
    return AccessFixture(users, cases, uploaded.json()["id"])


async def counts(factory) -> tuple[int, ...]:
    async with factory() as session:
        return tuple([
            int(await session.scalar(sa.select(sa.func.count()).select_from(model)))
            for model in (
                Case, UploadedDocument, ExtractedFact, CaseLedgerVersion,
                AnalysisJob, AnalysisPanel, AgentRun, AuditEvent,
            )
        ])


def test_all_case_scoped_routes_have_isolation_regressions() -> None:
    live = {
        (method, route.path)
        for route in api_routes(app.routes)
        if route.path.startswith("/cases/{case_id}")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    assert live == {(method, path) for method, path, _ in CASE_REQUESTS}


@pytest.mark.parametrize("actor", ["owner", "peer", "admin", "foreign_uploader", "foreign_admin"])
async def test_lists_and_direct_case_reads_use_the_same_policy(
    client: AsyncClient, world: AccessFixture, session_factory, actor: str,
) -> None:
    await sign_in(client, world.users[actor])
    visible = (
        {"owner", "peer", "admin", "legacy"} if actor == "admin"
        else {"foreign_uploader"} if actor == "foreign_admin"
        else {actor}
    )
    response = await client.get("/cases")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert {item["id"] for item in response.json()} == {
        str(world.cases[name]) for name in visible
    }
    for name, case_id in world.cases.items():
        response = await client.get(f"/cases/{case_id}")
        assert response.status_code == (200 if name in visible else 404)
        assert response.headers["cache-control"] == "no-store"
    async with session_factory() as session:
        legacy = await session.get(Case, world.cases["legacy"])
        assert legacy.created_by_user_id is None


@pytest.mark.parametrize("actor", ["peer", "foreign_uploader", "foreign_admin", "viewer"])
async def test_other_users_cannot_read_or_mutate_any_case_surface(
    client: AsyncClient, world: AccessFixture, session_factory, actor: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_generator(*args, **kwargs):
        raise AssertionError("An unauthorized request reached PPS generation")

    monkeypatch.setattr(generator, "generate", forbidden_generator)
    await sign_in(client, world.users[actor])
    before = await counts(session_factory)
    for method, path, body in CASE_REQUESTS:
        path = path.format(case_id=world.cases["owner"], document_id=world.document_id)
        if method == "POST" and path.endswith("/documents"):
            response = await client.post(
                path, files={"file": ("unauthorized.pdf", PDF_BYTES, "application/pdf")},
            )
        else:
            response = await client.request(method, path, json=body)
        assert response.status_code == (403 if actor == "viewer" else 404), (method, path)
        if actor != "viewer":
            assert response.json() == {"detail": "Dossier introuvable."}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert await counts(session_factory) == before


@pytest.mark.parametrize("actor", ["owner", "admin"])
async def test_owner_and_org_admin_can_read_documents_and_all_pps_formats(
    client: AsyncClient, world: AccessFixture, actor: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the clinical generator is stubbed; authentication, case lookup and renderers are real.
    async def synthetic_pps(session, *, case):
        assert case.id == world.cases["owner"]
        return _minimal_document().model_copy(update={
            "case_id": str(case.id), "case_reference": case.reference,
        })

    monkeypatch.setattr(generator, "generate", synthetic_pps)
    await sign_in(client, world.users[actor])
    base = f"/cases/{world.cases['owner']}"
    response = await client.get(f"{base}/documents/{world.document_id}/content")
    assert response.status_code == 200 and response.content == PDF_BYTES
    for suffix in ("/ledger", "/activity", "/analysis", "/pps", "/pps/markdown",
                   "/pps/docx", "/pps/docx/probatoire"):
        response = await client.get(base + suffix)
        assert response.status_code == 200, suffix
        assert response.headers["cache-control"] == "no-store"
        if "/docx" in suffix:
            assert response.content.startswith(b"PK")


@pytest.mark.parametrize("actor", ["owner", "admin"])
async def test_owner_and_org_admin_can_correct_upload_and_request_analysis(
    client: AsyncClient, world: AccessFixture, session_factory, actor: str,
) -> None:
    await sign_in(client, world.users[actor])
    base = f"/cases/{world.cases['owner']}"
    assert (await client.post(
        base + "/ledger/corrections",
        json={"fact_type": "age", "value": "70", "note": "Correction de test"},
    )).status_code == 201
    assert (await upload(
        client, str(world.cases["owner"]), "addendum.pdf", PDF_BYTES,
    )).status_code == 201
    assert (await client.post(
        base + "/analysis", json={"agent_role": "urology_oncology"},
    )).status_code == 202
    assert (await client.post(
        base + "/analysis/panel", json={"roles": ["medical_oncology", "radiation_oncology"]},
    )).status_code == 202
    async with session_factory() as session:
        case = await session.get(Case, world.cases["owner"])
        assert case.created_by_user_id == world.users["owner"].id


async def test_a_new_invitee_starts_empty_without_inheriting_admin_history(
    client: AsyncClient, world: AccessFixture,
) -> None:
    await sign_in(client, world.users["admin"])
    issued = await client.post(
        "/auth/invitations",
        json={"email": "new-tester@example.org", "role": "uploader"},
    )
    assert issued.status_code == 201
    await client.post("/auth/logout")
    activation = await client.post(
        "/auth/activate",
        json={"token": issued.json()["token"], "password": PASSWORD, "full_name": "Testeur"},
    )
    assert activation.status_code == 201
    assert (await client.get("/cases")).json() == []
    assert (await client.get("/auth/invitations")).status_code == 403
    own_case = await create_case(client)
    assert [case["id"] for case in (await client.get("/cases")).json()] == [own_case["id"]]
    assert (await client.get(f"/cases/{world.cases['owner']}")).status_code == 404


async def test_service_denies_viewers_and_role_changes_take_effect_on_the_same_session(
    client: AsyncClient, world: AccessFixture,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await sign_in(client, world.users["admin"])
    base = f"/cases/{world.cases['owner']}"
    assert (await client.get(base)).status_code == 200
    async with session_factory() as session:
        admin = await session.get(User, world.users["admin"].id)
        admin.role = UserRole.uploader
        await session.commit()
        viewer = world.users["viewer"]
        assert await service.list_cases(session, user=viewer) == []
        with pytest.raises(service.CaseNotFound):
            await service.get_case(session, user=viewer, case_id=world.cases["owner"])
    assert (await client.get(base)).status_code == 404
    assert {row["id"] for row in (await client.get("/cases")).json()} == {
        str(world.cases["admin"])
    }


@pytest.mark.parametrize("targeted", [False, True])
async def test_concurrent_owner_dispatch_survives_rollback_without_reloading_expired_input(
    client: AsyncClient, world: AccessFixture, session_factory,
    monkeypatch: pytest.MonkeyPatch, targeted: bool,
) -> None:
    original_enqueue = agent_routes.enqueue
    winning_ids = []

    async def enqueue_after_competitor(session, **kwargs):
        async with session_factory() as competing_session:
            winner = await original_enqueue(competing_session, **kwargs)
            await competing_session.commit()
            winning_ids.append(str(winner.id))
        return await original_enqueue(session, **kwargs)

    monkeypatch.setattr(agent_routes, "enqueue", enqueue_after_competitor)
    await sign_in(client, world.users["owner"])
    payload = {"agent_role": "urology_oncology"}
    if targeted:
        payload["retrieval_query"] = {
            "mode": "decision_targeted_v1", "decision_source": "request",
            "decision": "Question synthétique", "selected_fact_keys": ["muscle_invasion"],
        }
    response = await client.post(
        f"/cases/{world.cases['owner']}/analysis", json=payload,
    )
    assert response.status_code == 202, response.text
    assert winning_ids == [response.json()["job_id"]]
    async with session_factory() as session:
        assert await session.scalar(
            sa.select(sa.func.count()).select_from(AnalysisJob).where(
                AnalysisJob.case_id == world.cases["owner"],
                AnalysisJob.job_type == "run_agent",
            )
        ) == 1
