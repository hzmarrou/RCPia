"""Case ledger and corrections.

The rules that matter: a correction never destroys the machine's reading, the latest
version is what agents will be given, missing shows red and contradictory shows amber,
and `RAS` counts as answered.
"""

import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.auth import security, throttle
from rcpia.cases import ledger as ledger_service
from rcpia.cases.facts import extract_case_facts
from rcpia.core.db import get_session
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    BlockType,
    CancerType,
    Case,
    CaseLedgerVersion,
    CaseStatus,
    DocumentChunk,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    Organization,
    ParseStatus,
    UploadedDocument,
    User,
    UserRole,
)
from rcpia.main import app

PASSWORD = "correct-horse-battery-staple"

DOSSIER_TEXT = """**Sexe** : Femme
**Age** : 85 ans
**Antécédents médicaux** : HTA sous Losartan 50mg/j depuis 8 ans.
**Histoire de la maladie** : Hématurie totale intermittente depuis 2 ans.
**Antécédents familiaux** : RAS
Créatinine : 17.33
DFG : 30
Hb : 11.6
TDM TAP :
Majoration de l'épaississement pariétal vésical.
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
async def client(session_factory: async_sessionmaker[AsyncSession]):
    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
    app.dependency_overrides.clear()


async def seed_case_with_facts(
    factory: async_sessionmaker[AsyncSession],
    *,
    text: str = DOSSIER_TEXT,
    cancer_type: CancerType = CancerType.bladder,
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
            cancer_type=cancer_type,
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
            checksum_sha256="d" * 64,
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

        job = AnalysisJob(
            case_id=case.id,
            organization_id=organization.id,
            job_type="extract_facts",
            payload={},
        )
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await session.commit()
        await session.refresh(user)
        await session.refresh(case)
        return user, case


async def sign_in(client: AsyncClient, user: User) -> None:
    response = await client.post(
        "/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------------------
# Reading the ledger
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("cancer_type", list(CancerType))
async def test_biology_fields_are_unique_in_the_ledger_and_recorded_version(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    cancer_type: CancerType,
) -> None:
    user, case = await seed_case_with_facts(
        session_factory,
        text="PQ : 220\nBicarbonates : 24\nPhosphore : 30\n",
        cancer_type=cancer_type,
    )
    await sign_in(client, user)
    response = await client.get(f"/cases/{case.id}/ledger")
    assert response.status_code == 200
    body = response.json()
    keys = [entry["key"] for entry in body["entries"]]
    assert len(keys) == len(set(keys)), "a dict keyed by field would conceal duplicate rows"
    assert len(body["missing_fields"]) == len(set(body["missing_fields"]))
    for key, value, unit in (
        ("platelets", "220", None),
        ("bicarbonate", "24", "mmol/L"),
        ("phosphorus", "30", "mg/L"),
    ):
        entries = [entry for entry in body["entries"] if entry["key"] == key]
        assert len(entries) == 1
        assert entries[0]["value"] == value
        assert entries[0]["unit"] == unit
        assert entries[0]["highlight"] == "neutral"

    corrected = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "platelets", "value": "221", "note": "Correction de test."},
    )
    assert corrected.status_code == 201
    assert [entry["key"] for entry in corrected.json()["entries"]] == keys

    async with session_factory() as session:
        version = await session.scalar(
            sa.select(CaseLedgerVersion)
            .where(CaseLedgerVersion.case_id == case.id)
            .order_by(CaseLedgerVersion.version.desc())
        )
        assert version is not None
        stored_keys = [entry["key"] for entry in version.ledger["entries"]]
        assert stored_keys == keys


async def test_the_ledger_shows_values_missing_in_red_and_answered_fields_plain(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    body = (await client.get(f"/cases/{case.id}/ledger")).json()
    entries = {entry["key"]: entry for entry in body["entries"]}

    assert entries["creatinine"]["value"] == "17.33"
    assert entries["creatinine"]["highlight"] == "neutral"
    assert entries["bone_scan"]["highlight"] == "red", "nobody filled it in"
    assert "bone_scan" in body["missing_fields"]
    # RAS means the clinician looked; it is answered, not missing.
    assert entries["family_history"]["value"] == "RAS"
    assert entries["family_history"]["highlight"] == "neutral"
    assert "family_history" not in body["missing_fields"]


async def test_each_entry_carries_the_text_it_came_from(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A clinician checking a value must be able to see the sentence behind it."""
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    entries = {
        entry["key"]: entry
        for entry in (await client.get(f"/cases/{case.id}/ledger")).json()["entries"]
    }

    assert "17.33" in entries["creatinine"]["source_excerpt"]
    assert entries["creatinine"]["source_document_id"]
    assert entries["creatinine"]["source_chunk_id"]


async def test_contradictory_values_show_amber(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(
        session_factory, text=DOSSIER_TEXT + "\nCréatinine : 173.3\n"
    )
    await sign_in(client, user)

    body = (await client.get(f"/cases/{case.id}/ledger")).json()
    entries = {entry["key"]: entry for entry in body["entries"]}

    assert entries["creatinine"]["highlight"] == "amber"
    assert entries["creatinine"]["status"] == "conflicting"
    assert "creatinine" in body["uncertain_fields"]
    assert "173.3" in (entries["creatinine"]["note"] or "")


async def test_the_ledger_is_scoped_to_the_organization(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, case = await seed_case_with_facts(session_factory)
    outsider, _ = await seed_case_with_facts(session_factory)
    await sign_in(client, outsider)

    assert (await client.get(f"/cases/{case.id}/ledger")).status_code == 404


async def test_the_ledger_needs_a_session(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, case = await seed_case_with_facts(session_factory)

    assert (await client.get(f"/cases/{case.id}/ledger")).status_code == 401


# --------------------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------------------


async def test_a_correction_never_destroys_the_machine_reading(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={
            "fact_type": "creatinine",
            "value": "173.3",
            "note": "Virgule mal lue par l'extraction.",
        },
    )

    assert response.status_code == 201
    entries = {entry["key"]: entry for entry in response.json()["entries"]}
    assert entries["creatinine"]["value"] == "173.3"
    assert entries["creatinine"]["corrected"] is True

    async with session_factory() as session:
        facts = (
            (
                await session.execute(
                    sa.select(ExtractedFact)
                    .where(
                        ExtractedFact.case_id == case.id,
                        ExtractedFact.fact_type == "creatinine",
                    )
                    .order_by(ExtractedFact.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(facts) == 2
        machine, human = facts
        assert machine.origin is FactOrigin.machine and machine.value == "17.33"
        assert human.origin is FactOrigin.human and human.value == "173.3"
        assert human.supersedes_fact_id == machine.id
        assert human.recorded_by_user_id == user.id
        assert human.note


async def test_a_correction_requires_a_reason(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "173.3"},
    )

    assert response.status_code == 422


async def test_a_correction_creates_a_new_ledger_version(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Versions are what agent runs are pinned to, so each correction makes one."""
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    first = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "grade", "value": "Haut", "note": "Confirmé sur la relecture."},
    )
    second = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "cis", "value": "Non", "note": "Absent sur la relecture."},
    )

    assert first.json()["version"] == 1
    assert second.json()["version"] == 2

    async with session_factory() as session:
        versions = (
            (
                await session.execute(
                    sa.select(CaseLedgerVersion)
                    .where(CaseLedgerVersion.case_id == case.id)
                    .order_by(CaseLedgerVersion.version)
                )
            )
            .scalars()
            .all()
        )
        assert [version.version for version in versions] == [1, 2]
        assert versions[0].ledger["entries"], "the snapshot holds the whole ledger"
        assert versions[0].created_by_user_id == user.id


async def test_correcting_the_same_field_twice_chains_the_corrections(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "173.3", "note": "Première correction."},
    )
    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "17.33", "note": "Finalement correct."},
    )

    assert response.status_code == 201
    entries = {entry["key"]: entry for entry in response.json()["entries"]}
    assert entries["creatinine"]["value"] == "17.33"

    async with session_factory() as session:
        facts = (
            (
                await session.execute(
                    sa.select(ExtractedFact)
                    .where(
                        ExtractedFact.case_id == case.id,
                        ExtractedFact.fact_type == "creatinine",
                    )
                    .order_by(ExtractedFact.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(facts) == 3, "every reading is still on record"
        assert facts[2].supersedes_fact_id == facts[1].id


async def test_a_clinician_can_fill_in_a_missing_field(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={
            "fact_type": "bone_scan",
            "value": "Pas de lésion secondaire",
            "note": "Compte rendu récupéré auprès du service de médecine nucléaire.",
        },
    )

    body = response.json()
    entries = {entry["key"]: entry for entry in body["entries"]}
    assert entries["bone_scan"]["highlight"] == "neutral"
    assert "bone_scan" not in body["missing_fields"]


async def test_a_correction_is_audited(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "173.3", "note": "Virgule mal lue."},
    )

    async with session_factory() as session:
        event = await session.scalar(
            sa.select(AuditEvent).where(AuditEvent.event_type == "ledger.corrected")
        )
        assert event is not None
        assert event.actor_user_id == user.id
        assert event.payload["previous_value"] == "17.33"
        assert event.payload["new_value"] == "173.3"


async def test_an_unknown_field_cannot_be_corrected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "favourite_colour", "value": "bleu", "note": "Non clinique."},
    )

    assert response.status_code == 404


async def test_a_field_outside_the_cancer_type_cannot_be_corrected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """PSA is not a bladder field; accepting it would put a meaningless value in front
    of the agents."""
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "psa_total", "value": "12.4", "note": "Saisi par erreur."},
    )

    assert response.status_code == 422


async def test_corrections_are_scoped_to_the_organization(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, case = await seed_case_with_facts(session_factory)
    outsider, _ = await seed_case_with_facts(session_factory)
    await sign_in(client, outsider)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "1", "note": "Tentative."},
    )

    assert response.status_code == 404


async def test_the_stored_version_reflects_the_ledger_at_that_moment(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A later correction must not change what an earlier version says."""
    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)

    await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "111", "note": "Première valeur."},
    )
    await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={"fact_type": "creatinine", "value": "222", "note": "Deuxième valeur."},
    )

    async with session_factory() as session:
        versions = (
            (
                await session.execute(
                    sa.select(CaseLedgerVersion)
                    .where(CaseLedgerVersion.case_id == case.id)
                    .order_by(CaseLedgerVersion.version)
                )
            )
            .scalars()
            .all()
        )

    def value_of(version: CaseLedgerVersion, key: str) -> str | None:
        for entry in version.ledger["entries"]:
            if entry["key"] == key:
                return entry["value"]
        return None

    assert value_of(versions[0], "creatinine") == "111"
    assert value_of(versions[1], "creatinine") == "222"


async def test_a_contradicted_field_shows_every_reading_with_its_source(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """"The dossier says both 17.33 and 173.3" is only actionable with both sources."""
    user, case = await seed_case_with_facts(
        session_factory, text=DOSSIER_TEXT + "\nCréatinine : 173.3\n"
    )
    await sign_in(client, user)

    entries = {
        entry["key"]: entry
        for entry in (await client.get(f"/cases/{case.id}/ledger")).json()["entries"]
    }
    creatinine = entries["creatinine"]

    assert creatinine["highlight"] == "amber"
    assert {reading["value"] for reading in creatinine["readings"]} == {"17.33", "173.3"}
    assert all(reading["source_chunk_id"] for reading in creatinine["readings"])
    assert all(reading["source_excerpt"] for reading in creatinine["readings"])


async def test_correcting_a_contradicted_field_settles_it(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user, case = await seed_case_with_facts(
        session_factory, text=DOSSIER_TEXT + "\nCréatinine : 173.3\n"
    )
    await sign_in(client, user)

    response = await client.post(
        f"/cases/{case.id}/ledger/corrections",
        json={
            "fact_type": "creatinine",
            "value": "173.3",
            "note": "Valeur confirmée sur le compte rendu de biologie.",
        },
    )

    entries = {entry["key"]: entry for entry in response.json()["entries"]}
    assert entries["creatinine"]["value"] == "173.3"
    assert entries["creatinine"]["highlight"] == "neutral", "a decision was made"
    assert "creatinine" not in response.json()["uncertain_fields"]


async def test_concurrent_corrections_do_not_collide_on_a_version_number(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two clinicians correcting different fields at once must both succeed."""
    import asyncio

    user, case = await seed_case_with_facts(session_factory)
    await sign_in(client, user)
    cookies = dict(client.cookies)

    async def correct(field: str, value: str):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", cookies=cookies
        ) as other:
            return await other.post(
                f"/cases/{case.id}/ledger/corrections",
                json={"fact_type": field, "value": value, "note": "Correction simultanée."},
            )

    first, second = await asyncio.gather(
        correct("grade", "Haut"), correct("cis", "Non")
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert {first.json()["version"], second.json()["version"]} == {1, 2}


def test_highlight_mapping() -> None:
    assert ledger_service.highlight_for(FactStatus.missing) == "red"
    assert ledger_service.highlight_for(FactStatus.conflicting) == "amber"
    assert ledger_service.highlight_for(FactStatus.uncertain) == "amber"
    assert ledger_service.highlight_for(FactStatus.confirmed) == "neutral"
