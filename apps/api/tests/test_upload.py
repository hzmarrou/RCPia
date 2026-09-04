"""Case creation and dossier upload.

The properties under test: a dossier is stored byte-for-byte and stays retrievable, its
checksum and content type are recorded, parsing is queued in the same transaction, and no
case is reachable from another organization.
"""

import io
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.auth import security, throttle
from rcpia.core.db import get_session
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    Case,
    CaseStatus,
    JobStatus,
    Organization,
    UploadedDocument,
    User,
    UserRole,
)
from rcpia.jobs.models import JobType, RunStep
from rcpia.main import app
from rcpia.storage.base import checksum
from rcpia.storage.factory import get_blob_store
from rcpia.storage.local import LocalBlobStore

PASSWORD = "correct-horse-battery-staple"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


def docx_bytes() -> bytes:
    """A minimal but genuine .docx: a zip whose first entry is [Content_Types].xml."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document><body>PSA 12.4</body></document>")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def reset_throttle():
    throttle.reset_all()
    yield
    throttle.reset_all()


@pytest.fixture
def blob_store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path, signing_secret="test-signing-secret")


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession], blob_store: LocalBlobStore
):
    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_blob_store] = lambda: blob_store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


async def make_user(
    factory: async_sessionmaker[AsyncSession], *, role: UserRole = UserRole.uploader
) -> User:
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:10]}")
        session.add(organization)
        await session.flush()
        user = User(
            organization_id=organization.id,
            email=f"user-{uuid.uuid4().hex[:8]}@example.org",
            role=role,
            password_hash=security.hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user


async def sign_in(client: AsyncClient, user: User) -> None:
    response = await client.post(
        "/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200


async def create_case(client: AsyncClient, **overrides: object) -> dict:
    payload = {
        "reference": f"CASE-{uuid.uuid4().hex[:8]}",
        "cancer_type": "bladder",
        "clinical_question": "Stratégie thérapeutique ?",
    }
    payload.update(overrides)
    response = await client.post("/cases", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def upload(client: AsyncClient, case_id: str, filename: str, content: bytes):
    return await client.post(
        f"/cases/{case_id}/documents",
        files={"file": (filename, content, "application/octet-stream")},
    )


# --------------------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------------------


async def test_case_routes_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/cases")).status_code == 401
    assert (await client.post("/cases", json={})).status_code == 401


async def test_viewers_cannot_create_cases(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    viewer = await make_user(session_factory, role=UserRole.viewer)
    await sign_in(client, viewer)

    response = await client.post(
        "/cases", json={"reference": "CASE-1", "cancer_type": "prostate"}
    )

    assert response.status_code == 403


async def test_a_case_is_invisible_to_another_organization(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await make_user(session_factory)
    await sign_in(client, owner)
    case = await create_case(client)
    await client.post("/auth/logout")

    outsider = await make_user(session_factory)
    await sign_in(client, outsider)

    assert (await client.get(f"/cases/{case['id']}")).status_code == 404
    assert (await client.get("/cases")).json() == []
    assert (await upload(client, case["id"], "dossier.pdf", PDF_BYTES)).status_code == 404


# --------------------------------------------------------------------------------------
# Case creation
# --------------------------------------------------------------------------------------


async def test_create_and_list_a_case(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)

    case = await create_case(client, reference="  CASE-42  ")

    assert case["reference"] == "CASE-42", "whitespace is normalized"
    assert case["status"] == "draft"
    listed = (await client.get("/cases")).json()
    assert [item["id"] for item in listed] == [case["id"]]


async def test_duplicate_reference_in_one_organization_is_refused(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    response = await client.post(
        "/cases", json={"reference": case["reference"], "cancer_type": "prostate"}
    )

    assert response.status_code == 409


async def test_the_same_reference_is_free_in_another_organization(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    first = await make_user(session_factory)
    await sign_in(client, first)
    case = await create_case(client)
    await client.post("/auth/logout")

    second = await make_user(session_factory)
    await sign_in(client, second)

    response = await client.post(
        "/cases", json={"reference": case["reference"], "cancer_type": "prostate"}
    )
    assert response.status_code == 201


async def test_creating_a_case_writes_an_audit_event(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)

    case = await create_case(client)

    async with session_factory() as session:
        event = await session.scalar(
            sa.select(AuditEvent).where(AuditEvent.case_id == uuid.UUID(case["id"]))
        )
        assert event is not None
        assert event.event_type == "case.created"
        assert event.actor_user_id == user.id


async def test_only_the_supported_cancer_types_are_accepted(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """v1 scope is prostate and bladder; anything else is a validation error."""
    user = await make_user(session_factory)
    await sign_in(client, user)

    response = await client.post(
        "/cases", json={"reference": "CASE-KIDNEY", "cancer_type": "kidney"}
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------------------


async def test_uploading_a_pdf_stores_it_and_records_its_facts(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    blob_store: LocalBlobStore,
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    response = await upload(client, case["id"], "Dossier 2 - vessie.pdf", PDF_BYTES)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_type"] == "application/pdf"
    assert body["size_bytes"] == len(PDF_BYTES)
    assert body["checksum_sha256"] == checksum(PDF_BYTES)
    assert body["original_filename"] == "Dossier 2 - vessie.pdf"
    assert body["parse_status"] == "pending"

    async with session_factory() as session:
        document = await session.get(UploadedDocument, uuid.UUID(body["id"]))
        assert document is not None
        assert await blob_store.get_bytes(document.blob_key) == PDF_BYTES


async def test_uploading_a_docx_is_accepted(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    response = await upload(client, case["id"], "dossier.docx", docx_bytes())

    assert response.status_code == 201
    assert response.json()["content_type"].endswith("wordprocessingml.document")


async def test_the_blob_key_ignores_the_uploaded_filename(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    response = await upload(client, case["id"], "../../etc/passwd.pdf", PDF_BYTES)

    assert response.status_code == 201
    body = response.json()
    async with session_factory() as session:
        document = await session.get(UploadedDocument, uuid.UUID(body["id"]))
        assert document is not None
        assert document.blob_key == f"cases/{case['id']}/dossiers/{document.id}.pdf"
        assert document.original_filename == "../../etc/passwd.pdf", "kept as data"


async def test_a_docx_whose_content_types_entry_is_not_first_is_accepted(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Word does not guarantee entry order, and a substring check on the first few
    kilobytes rejects perfectly valid documents."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("docProps/app.xml", "<Properties/>" + "padding" * 1000)
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")

    response = await upload(client, case["id"], "dossier.docx", buffer.getvalue())

    assert response.status_code == 201


async def test_an_archive_merely_mentioning_content_types_is_refused(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A zip containing the marker as data is not an Office document."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "[Content_Types].xml")

    response = await upload(client, case["id"], "dossier.docx", buffer.getvalue())

    assert response.status_code == 415


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("dossier.txt", b"PSA 12.4 ng/mL"),
        ("dossier.pdf", b"not really a pdf"),
        ("dossier.docx", b"PK\x03\x04 but not an office document"),
        ("dossier.doc", b"\xd0\xcf\x11\xe0legacy word"),
        ("empty.pdf", b""),
    ],
)
async def test_unsupported_files_are_refused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    filename: str,
    content: bytes,
) -> None:
    """The magic bytes decide, not the extension or the browser's content type."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    response = await upload(client, case["id"], filename, content)

    assert response.status_code == 415


async def test_a_rejected_upload_creates_no_document_and_no_job(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    await upload(client, case["id"], "notes.txt", b"free text")

    async with session_factory() as session:
        documents = (await session.execute(sa.select(UploadedDocument))).scalars().all()
        job_count = await session.scalar(sa.select(sa.func.count()).select_from(AnalysisJob))
        assert documents == []
        assert job_count == 0


async def test_uploading_queues_a_parsing_job(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    document = (await upload(client, case["id"], "dossier.pdf", PDF_BYTES)).json()

    async with session_factory() as session:
        job = await session.scalar(
            sa.select(AnalysisJob).where(AnalysisJob.case_id == uuid.UUID(case["id"]))
        )
        assert job is not None
        assert job.job_type == "parse_dossier"
        assert job.status is JobStatus.queued
        assert job.payload == {"document_id": document["id"]}
        assert job.organization_id == user.organization_id


async def test_uploading_moves_the_case_into_parsing(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    await upload(client, case["id"], "dossier.pdf", PDF_BYTES)

    async with session_factory() as session:
        stored = await session.get(Case, uuid.UUID(case["id"]))
        assert stored is not None
        assert stored.status is CaseStatus.parsing


async def test_two_uploads_are_stored_separately(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Blobs are immutable, so a second dossier must not collide with the first."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    first = (await upload(client, case["id"], "dossier.pdf", PDF_BYTES)).json()
    second = (await upload(client, case["id"], "dossier.pdf", PDF_BYTES + b"addendum")).json()

    assert first["id"] != second["id"]
    detail = (await client.get(f"/cases/{case['id']}")).json()
    assert len(detail["documents"]) == 2


async def test_case_detail_lists_documents_with_their_status(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await upload(client, case["id"], "dossier.pdf", PDF_BYTES)

    detail = (await client.get(f"/cases/{case['id']}")).json()

    assert detail["reference"] == case["reference"]
    assert len(detail["documents"]) == 1
    assert detail["documents"][0]["parse_status"] == "pending"
    assert detail["documents"][0]["parse_warnings"] == []


# --------------------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------------------


async def test_the_original_dossier_can_be_downloaded_unchanged(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The uploaded file stays the primary source and must be reviewable as-is."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    document = (await upload(client, case["id"], "dossier.pdf", PDF_BYTES)).json()

    response = await client.get(f"/cases/{case['id']}/documents/{document['id']}/content")

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["cache-control"] == "no-store"


async def test_download_is_refused_across_organizations(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner = await make_user(session_factory)
    await sign_in(client, owner)
    case = await create_case(client)
    document = (await upload(client, case["id"], "dossier.pdf", PDF_BYTES)).json()
    await client.post("/auth/logout")

    outsider = await make_user(session_factory)
    await sign_in(client, outsider)

    response = await client.get(f"/cases/{case['id']}/documents/{document['id']}/content")
    assert response.status_code == 404


async def test_a_corrupted_blob_is_refused_and_audited_on_download(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    blob_store: LocalBlobStore,
    tmp_path: Path,
) -> None:
    """Handing back bytes that no longer match their checksum would quietly undermine
    every fact extracted from the dossier."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    document = (await upload(client, case["id"], "dossier.pdf", PDF_BYTES)).json()

    async with session_factory() as session:
        stored = await session.get(UploadedDocument, uuid.UUID(document["id"]))
        assert stored is not None
        key = stored.blob_key
    (tmp_path / f"objects/{key}/data").write_bytes(b"%PDF-1.7 tampered")

    response = await client.get(f"/cases/{case['id']}/documents/{document['id']}/content")

    assert response.status_code == 409
    async with session_factory() as session:
        event = await session.scalar(
            sa.select(AuditEvent).where(AuditEvent.event_type == "document.integrity_failed")
        )
        assert event is not None
        assert event.entity_id == uuid.UUID(document["id"])


async def test_a_failed_upload_transaction_leaves_no_orphan_blob(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    blob_store: LocalBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blob is written before the row that justifies it. If the transaction fails,
    that blob must not survive as unaudited clinical data."""
    from rcpia.cases import service as cases_service

    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    async def explode(*args: object, **kwargs: object):
        raise RuntimeError("database went away mid-commit")

    monkeypatch.setattr(cases_service, "_record_document", explode)

    # The test client re-raises rather than turning it into a 500; either way the
    # request failed and the compensation must have run.
    with pytest.raises(RuntimeError, match="database went away"):
        await upload(client, case["id"], "dossier.pdf", PDF_BYTES)

    async with session_factory() as session:
        documents = (await session.execute(sa.select(UploadedDocument))).scalars().all()
        assert documents == []

    keys = list((Path(blob_store.root) / "objects").rglob("data"))
    assert keys == [], "the orphaned blob was removed"


async def test_downloading_an_unknown_document_is_not_found(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)

    response = await client.get(f"/cases/{case['id']}/documents/{uuid.uuid4()}/content")
    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# Reporting what is still running
# --------------------------------------------------------------------------------------


async def add_job(
    factory: async_sessionmaker[AsyncSession],
    case_id: str,
    user: User,
    **overrides: object,
) -> uuid.UUID:
    async with factory() as session:
        job = AnalysisJob(
            case_id=uuid.UUID(case_id),
            organization_id=user.organization_id,
            job_type=overrides.pop("job_type", JobType.RUN_AGENT),  # type: ignore[arg-type]
            payload=overrides.pop("payload", {}),  # type: ignore[arg-type]
            status=overrides.pop("status", JobStatus.running),  # type: ignore[arg-type]
            **overrides,  # type: ignore[arg-type]
        )
        session.add(job)
        await session.commit()
        return job.id


async def test_the_activity_of_a_case_names_the_step_each_run_is_on(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await add_job(
        session_factory,
        case["id"],
        user,
        payload={"agent_role": "medical_oncology"},
        milestone=RunStep.REASONING,
        milestone_at=datetime.now(UTC),
    )

    response = await client.get(f"/cases/{case['id']}/activity")

    assert response.status_code == 200
    [job] = response.json()
    assert job["job_type"] == JobType.RUN_AGENT
    assert job["agent_role"] == "medical_oncology"
    assert job["milestone"] == RunStep.REASONING
    assert job["milestone_at"] is not None


async def test_a_run_that_has_not_reported_yet_says_so_rather_than_starting_the_bar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Null is "not known", never "at the first step". A queued job has not begun, and a
    display that renders an unreported job as `bundle` invents the same progress the
    milestones exist to replace."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await add_job(
        session_factory,
        case["id"],
        user,
        payload={"agent_role": "pathology"},
        status=JobStatus.queued,
    )

    [job] = (await client.get(f"/cases/{case['id']}/activity")).json()

    assert job["milestone"] is None and job["milestone_at"] is None


async def test_finished_work_leaves_the_activity_list(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """What an agent concluded is an `AgentRun` and outlives its job. This endpoint is
    only about what is still moving, so a completed job disappears from it."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await add_job(
        session_factory,
        case["id"],
        user,
        payload={"agent_role": "radiology"},
        status=JobStatus.completed,
        milestone=RunStep.RECORDING,
    )
    await add_job(
        session_factory, case["id"], user, payload={"agent_role": "urology_oncology"}
    )

    listed = (await client.get(f"/cases/{case['id']}/activity")).json()

    assert [job["agent_role"] for job in listed] == ["urology_oncology"]


async def test_the_clinical_question_does_not_travel_with_the_progress_report(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """**The payload is dossier-adjacent and this endpoint is polled constantly.**

    A job payload carries the clinical question the agent was asked, which is written by
    the uploader about a patient. Returning the payload wholesale would put it in every
    progress poll, in browser caches and in any proxy log along the way — so the route
    reads one named key and never the payload itself.
    """
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await add_job(
        session_factory,
        case["id"],
        user,
        payload={
            "agent_role": "urology_oncology",
            "clinical_question": "MOTIF_CONFIDENTIEL_DU_DOSSIER",
        },
    )

    response = await client.get(f"/cases/{case['id']}/activity")

    assert "MOTIF_CONFIDENTIEL_DU_DOSSIER" not in response.text
    assert "payload" not in response.text


async def test_a_payload_without_a_usable_role_reports_no_role(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Parsing and extraction jobs carry no role, and a payload is JSON someone supplied:
    a present key is not a promise of a string."""
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await add_job(
        session_factory, case["id"], user, job_type=JobType.PARSE_DOSSIER, payload={}
    )
    await add_job(session_factory, case["id"], user, payload={"agent_role": 42})

    listed = (await client.get(f"/cases/{case['id']}/activity")).json()

    assert [job["agent_role"] for job in listed] == [None, None]


async def test_another_organizations_activity_is_not_reachable(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session_factory)
    await sign_in(client, user)
    case = await create_case(client)
    await add_job(session_factory, case["id"], user, payload={"agent_role": "pathology"})

    outsider = await make_user(session_factory)
    await sign_in(client, outsider)

    assert (await client.get(f"/cases/{case['id']}/activity")).status_code == 404
