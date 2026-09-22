"""Synthetic full-source, attestation, authorization and publication-race regressions."""

import asyncio
import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from rcpia.cases import ledger, sources
from rcpia.cases.extraction import INCOMPLETE_MARKER, TRUNCATION_MARKER
from rcpia.cases.source_schemas import SourceReviewRequest
from rcpia.core.models import (
    AgentRun,
    AnalysisJob,
    AuditEvent,
    BlockType,
    Case,
    CaseLedgerVersion,
    DocumentChunk,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    ParseStatus,
    UploadedDocument,
    User,
    UserRole,
)
from tests.test_upload import blob_store as blob_store
from tests.test_upload import client as client
from tests.test_upload import create_case, make_user, sign_in
from tests.test_upload import reset_throttle as reset_throttle

PARTS = (
    "IRM prostatique :\nDate de l'examen : 04/02/2020\nTechnique : acquisition limitée.",
    "| Site | Observation |\n| --- | --- |\n| A | Non évaluable |",
    "CONCLUSION : description finale, à confronter à la clinique.\n"
    "Qualification terminale : ne permet pas de conclure à une extension.",
    '<script>window.executed = true</script><img src=x onerror="alert(1)">',
)


@dataclass
class SourceWorld:
    user: User
    case_id: uuid.UUID
    document_id: uuid.UUID
    chunks: list[uuid.UUID]

    @property
    def base(self) -> str:
        return f"/cases/{self.case_id}/sources"


async def seed(
    client: AsyncClient, factory, *, parts=PARTS, version=True, legacy=False,
) -> SourceWorld:
    user = await make_user(factory)
    await sign_in(client, user)
    created = await create_case(client, cancer_type="prostate")
    async with factory() as session:
        case = await session.get(Case, uuid.UUID(created["id"]))
        document = UploadedDocument(
            case_id=case.id, organization_id=case.organization_id,
            blob_key=f"synthetic-source-{uuid.uuid4()}", original_filename="compte-rendu.pdf",
            content_type="application/pdf", checksum_sha256="a" * 64, size_bytes=123,
            parse_status=ParseStatus.parsed, current_parse_run=2,
            created_at=datetime(2026, 9, 20, tzinfo=UTC),
        )
        session.add(document)
        await session.flush()
        chunks = [
            DocumentChunk(
                document_id=document.id, parse_run=2, ordinal=index, text=text,
                page=index + 1, section=f"Section {index}",
                block_type=BlockType.table if index == 1 else BlockType.paragraph,
            )
            for index, text in reversed(list(enumerate(parts)))
        ]
        session.add_all(chunks)
        await session.flush()
        if version:
            if legacy:
                session.add(CaseLedgerVersion(
                    case_id=case.id, organization_id=case.organization_id, version=1,
                    ledger={"entries": []}, missing_fields=[], uncertain_fields=[],
                    change_reason="Legacy synthetic",
                ))
            else:
                await ledger.record_version(session, case=case, change_reason="Synthetic")
        await session.commit()
        return SourceWorld(
            user, case.id, document.id,
            [chunk.id for chunk in sorted(chunks, key=lambda chunk: chunk.ordinal)],
        )


@pytest.fixture
async def world(client, session_factory):
    return await seed(client, session_factory)


def fields(index):
    return {field["key"]: field for field in index["fields"]}


def binding(index):
    return {
        "ledger_version": index["ledger_version"],
        "source_fingerprint": index["source_fingerprint"],
    }


def review(index, *, key="age", status="absent", note="Sources relues : absence attestée."):
    return {
        "expected_ledger_version": index["ledger_version"],
        "source_fingerprint": index["source_fingerprint"],
        "field_key": key, "status": status, "note": note,
    }


async def index(client, world):
    response = await client.get(world.base)
    assert response.status_code == 200, response.text
    return response.json()


async def add_fact(
    factory, world, *, key, value, status=FactStatus.confirmed, publish=True,
):
    async with factory() as session:
        case = await session.get(Case, world.case_id)
        session.add(ExtractedFact(
            case_id=case.id, organization_id=case.organization_id, fact_type=key, value=value,
            status=status, origin=FactOrigin.machine, source_document_id=world.document_id,
            source_chunk_id=world.chunks[0], source_excerpt=PARTS[0],
            extraction_signature="b" * 64,
        ))
        await session.flush()
        if publish:
            await ledger.record_version(
                session, case=case, change_reason="New fact", inherit_sources=True,
            )
        await session.commit()


async def test_full_document_order_qualifiers_dates_and_html_are_literal(
    client, world, session_factory,
):
    data = await index(client, world)
    assert data["scope"] == "pinned_snapshot"
    assert len(data["source_fingerprint"]) == 64
    summary = data["documents"][0]
    assert summary["filename"] == "compte-rendu.pdf"
    assert summary["parse_run"] == 2 and summary["chunks"] == len(PARTS)
    assert summary["pages"] == [1, 2, 3, 4]
    assert summary["coverage"] == "complete_parsed_source"
    assert summary["uploaded_at"].startswith("2026-09-20")
    response = await client.get(f"{world.base}/{world.document_id}", params=binding(data))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    document = response.json()
    assert [block["text"] for block in document["blocks"]] == list(PARTS)
    assert [block["id"] for block in document["blocks"]] == list(map(str, world.chunks))
    assert document["blocks"][1]["block_type"] == "table"
    assert document["blocks"][-1]["text"].startswith("<script>")
    assert "2020" in document["blocks"][0]["text"]
    assert "qualification terminale" in document["blocks"][2]["text"].lower()
    assert "date de l'examen" not in document["document"]
    assert fields(data)["prostate_mri"]["state"] == "in_report"
    assert fields(data)["age"]["state"] == "unverified"
    assert fields(data)["ctnm"]["state"] == "unverified"
    assert fields(data)["ctnm"]["links"] == []
    assert not any(field["state"] == "absent" for field in data["fields"])


@pytest.mark.parametrize("legacy", [False, True])
async def test_unversioned_sources_unavailable_and_legacy_explicit(client, session_factory, legacy):
    world = await seed(client, session_factory, version=legacy, legacy=legacy)
    data = await index(client, world)
    if legacy:
        assert data["scope"] == "current_parsed_unpinned"
        assert data["notice"].startswith(sources.LEGACY_NOTICE)
        assert (await client.get(
            f"{world.base}/{world.document_id}", params=binding(data),
        )).status_code == 200
    else:
        assert data["ledger_version"] is None
        assert data["scope"] == "unavailable" and data["documents"] == []
        assert not any(field["can_attest_absence"] for field in data["fields"])
        assert (await client.get(
            f"{world.base}/{world.document_id}",
            params={"ledger_version": 1, "source_fingerprint": data["source_fingerprint"]},
        )).status_code == 409


async def test_document_larger_than_transport_cap_is_not_truncated(client, session_factory):
    parts = ("IRM prostatique : Observation.", "Texte intégral " * 10_000 + "RÉSERVE FINALE.")
    world = await seed(client, session_factory, parts=parts)
    data = await index(client, world)
    result = await client.get(f"{world.base}/{world.document_id}", params=binding(data))
    assert result.status_code == 200, result.text
    assert [block["text"] for block in result.json()["blocks"]] == list(parts)


@pytest.mark.parametrize("text", [
    "IRM prostatique : non renseigné",
    "IRM prostatique : [NO_CONTENT_HERE]",
    "IRM prostatique :\n---\nPage 1 sur 2",
    "Technique IRM mentionnée dans un texte sans libellé de champ.",
])
async def test_placeholders_footers_and_generic_mentions_never_prove_presence(
    client, session_factory, text,
):
    world = await seed(client, session_factory, parts=(text,))
    data = await index(client, world)
    assert fields(data)["prostate_mri"]["state"] == "unverified"
    assert fields(data)["ctnm"]["state"] == "unverified"
    assert not any(field["state"] == "absent" for field in data["fields"])


@pytest.mark.parametrize("status,state", [
    (FactStatus.confirmed, "documented"),
    (FactStatus.uncertain, "interpretation"),
    (FactStatus.inferred, "interpretation"),
    (FactStatus.conflicting, "interpretation"),
])
async def test_statuses_and_known_values_cannot_be_overridden_as_absent(
    client, session_factory, world, status, state,
):
    await add_fact(session_factory, world, key="age", value="70", status=status)
    data = await index(client, world)
    assert fields(data)["age"]["state"] == state
    assert not fields(data)["age"]["can_attest_absence"]
    assert fields(data)["age"]["links"][0]["chunk_ids"] == [str(world.chunks[0])]
    response = await client.post(world.base + "/review", json=review(data))
    assert response.status_code == 422
    async with session_factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(AuditEvent).where(
            AuditEvent.event_type == sources.REVIEW_EVENT,
        )) == 0


async def test_context_reasons_are_field_specific_and_french(client, session_factory, world):
    await add_fact(session_factory, world, key="ct_dre", value="cT2a")
    await add_fact(session_factory, world, key="prostate_mri", value="Résultat consigné.")
    data = await index(client, world)
    assert fields(data)["prostate_mri"]["state"] == "documented"
    assert fields(data)["ct_dre"]["state"] == "interpretation"
    assert "Seule une partie du stade" in fields(data)["ct_dre"]["explanation"]
    assert fields(data)["ctnm"]["state"] == "interpretation"
    assert "ne constitue pas un cTNM complet" in fields(data)["ctnm"]["explanation"]
    assert "partial_staging_assessment" not in str(data)
    assert fields(data)["ctnm"]["links"][0]["chunk_ids"] == [str(world.chunks[0])]


@pytest.mark.parametrize("value,phrase", [
    ("en attente", "en attente"),
    ("non évaluable", "non évaluable"),
])
async def test_pending_and_unassessable_are_not_documented_as_confirmed_results(
    client, session_factory, world, value, phrase,
):
    await add_fact(session_factory, world, key="prostate_mri", value=value)
    data = await index(client, world)
    field = fields(data)["prostate_mri"]
    assert field["state"] == "interpretation"
    assert phrase in field["explanation"]


@pytest.mark.parametrize("marker", [TRUNCATION_MARKER, INCOMPLETE_MARKER])
async def test_incomplete_field_links_whole_document(client, session_factory, world, marker):
    await add_fact(
        session_factory, world, key="prostate_mri",
        value="Technique avec injection." + marker, status=FactStatus.uncertain,
    )
    data = await index(client, world)
    field = fields(data)["prostate_mri"]
    assert field["state"] == "in_report"
    assert field["label"] == "Information présente dans un compte rendu, à préciser"
    assert field["links"][0]["document_id"] == str(world.document_id)
    result = await client.get(f"{world.base}/{world.document_id}", params=binding(data))
    assert result.json()["blocks"][2]["text"] == PARTS[2]


async def clinical_state(factory, case_id):
    async with factory() as session:
        case = await session.get(Case, case_id)
        return (
            case.status,
            [(row.id, copy.deepcopy(row.ledger)) for row in (await session.scalars(
                sa.select(CaseLedgerVersion).where(CaseLedgerVersion.case_id == case_id)
                .order_by(CaseLedgerVersion.version),
            )).all()],
            tuple([await session.scalar(
                sa.select(sa.func.count()).select_from(model).where(model.case_id == case_id)
            ) for model in (ExtractedFact, AnalysisJob, AgentRun)]),
        )


async def test_absence_requires_attestation_and_retraction_keeps_history_without_clinical_writes(
    client, session_factory, world,
):
    data = await index(client, world)
    assert fields(data)["age"]["can_attest_absence"]
    baseline = await clinical_state(session_factory, world.case_id)
    ledger_before = (await client.get(f"/cases/{world.case_id}/ledger")).json()
    for note in ("", "  ", "ab", " a "):
        assert (await client.post(
            world.base + "/review", json=review(data, note=note),
        )).status_code == 422
    response = await client.post(world.base + "/review", json=review(data))
    assert response.status_code == 200, response.text
    updated = response.json()
    age = fields(updated)["age"]
    assert age["state"] == "absent" and age["reviewed"]
    assert age["can_attest_absence"]
    assert age["label"] == "Information absente du dossier"
    assert age["review_note"] == "Sources relues : absence attestée."
    assert updated["source_fingerprint"] == data["source_fingerprint"]
    assert (await index(client, world)) == updated
    assert await clinical_state(session_factory, world.case_id) == baseline
    assert (await client.get(f"/cases/{world.case_id}/ledger")).json() == ledger_before
    response = await client.post(
        world.base + "/review",
        json=review(updated, status="unverified", note="Attestation retirée."),
    )
    assert response.status_code == 200
    assert fields(response.json())["age"]["state"] == "unverified"
    assert not fields(response.json())["age"]["reviewed"]
    async with session_factory() as session:
        events = (await session.scalars(sa.select(AuditEvent).where(
            AuditEvent.event_type == sources.REVIEW_EVENT,
        ).order_by(AuditEvent.created_at))).all()
        assert [event.payload["status"] for event in events] == ["absent", "unverified"]
        assert all(
            event.case_id == world.case_id and event.actor_user_id == world.user.id
            and event.organization_id == world.user.organization_id
            and event.payload["source_fingerprint"] == data["source_fingerprint"]
            and event.payload["ledger_version"] == data["ledger_version"]
            and len(event.payload["field_fingerprint"]) == 64
            for event in events
        )
    assert await clinical_state(session_factory, world.case_id) == baseline


async def test_named_source_value_prevents_false_absence(client, world):
    data = await index(client, world)
    assert fields(data)["prostate_mri"]["state"] == "in_report"
    assert not fields(data)["prostate_mri"]["can_attest_absence"]
    assert (await client.post(
        world.base + "/review", json=review(data, key="prostate_mri"),
    )).status_code == 422
    assert (await client.post(
        world.base + "/review", json=review(data, key="invented_field"),
    )).status_code == 422


@pytest.mark.parametrize("placeholder", ["non renseigné", "NR", "unknown"])
async def test_preserved_unknown_readings_allow_attestation_after_missingness_projection(
    client, session_factory, world, placeholder,
):
    await add_fact(session_factory, world, key="age", value=placeholder)
    response = await client.get(f"/cases/{world.case_id}/ledger")
    age = next(entry for entry in response.json()["entries"] if entry["key"] == "age")
    assert age["value"] is None
    assert age["readings"][0]["value"] == placeholder
    data = await index(client, world)
    assert fields(data)["age"]["state"] == "unverified"
    assert fields(data)["age"]["can_attest_absence"]
    response = await client.post(world.base + "/review", json=review(data))
    assert response.status_code == 200, response.text
    assert fields(response.json())["age"]["state"] == "absent"
    assert fields(response.json())["age"]["can_attest_absence"]


async def test_admin_can_read_full_source_and_attest_with_own_actor(
    client, session_factory, world,
):
    async with session_factory() as session:
        admin = User(
            organization_id=world.user.organization_id, email="source-admin@example.org",
            role=UserRole.admin, is_active=True, password_hash=world.user.password_hash,
        )
        session.add(admin)
        await session.commit()
    await sign_in(client, admin)
    data = await index(client, world)
    assert (await client.get(
        f"{world.base}/{world.document_id}", params=binding(data),
    )).status_code == 200
    response = await client.post(world.base + "/review", json=review(data))
    assert response.status_code == 200
    async with session_factory() as session:
        event = await session.scalar(sa.select(AuditEvent).where(
            AuditEvent.event_type == sources.REVIEW_EVENT,
        ))
        assert event.actor_user_id == admin.id


async def test_footer_block_cannot_supply_missing_value_but_stays_readable(
    client, session_factory,
):
    world = await seed(
        client, session_factory, version=False,
        parts=("IRM prostatique :", "Confidentiel, centre de soins synthétique."),
    )
    async with session_factory() as session:
        footer = await session.get(DocumentChunk, world.chunks[1])
        footer.section = "footer"
        case = await session.get(Case, world.case_id)
        await ledger.record_version(session, case=case, change_reason="With footer")
        await session.commit()
    data = await index(client, world)
    assert fields(data)["prostate_mri"]["state"] == "unverified"
    response = await client.get(f"{world.base}/{world.document_id}", params=binding(data))
    assert response.json()["blocks"][1]["text"] == "Confidentiel, centre de soins synthétique."


async def test_new_upload_invalidates_review_without_changing_version(
    client, session_factory, world,
):
    data = await index(client, world)
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 200
    async with session_factory() as session:
        session.add(UploadedDocument(
            case_id=world.case_id, organization_id=world.user.organization_id,
            blob_key=f"synthetic-new-{uuid.uuid4()}", original_filename="nouveau.pdf",
            content_type="application/pdf", checksum_sha256="c" * 64, size_bytes=456,
        ))
        await session.commit()
    updated = await index(client, world)
    assert updated["ledger_version"] == data["ledger_version"]
    assert updated["source_fingerprint"] != data["source_fingerprint"]
    assert fields(updated)["age"]["state"] == "unverified"
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 409


async def test_empty_manifest_does_not_allow_absence(client, session_factory):
    world = await seed(client, session_factory, version=False)
    async with session_factory() as session:
        case = await session.get(Case, world.case_id)
        document = await session.get(UploadedDocument, world.document_id)
        document.parse_status = ParseStatus.failed
        await ledger.record_version(session, case=case, change_reason="No parsed sources")
        await session.commit()
    data = await index(client, world)
    assert data["scope"] == "unavailable"
    assert not any(field["can_attest_absence"] for field in data["fields"])
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 422


@pytest.mark.parametrize("publish", [False, True])
async def test_new_fact_invalidates_attestation_even_without_ledger_publication(
    client, session_factory, world, publish,
):
    data = await index(client, world)
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 200
    await add_fact(session_factory, world, key="age", value="70", publish=publish)
    updated = await index(client, world)
    assert fields(updated)["age"]["state"] == "documented"
    assert not fields(updated)["age"]["reviewed"]
    assert updated["source_fingerprint"] != data["source_fingerprint"]
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 409
    assert (await client.get(
        f"{world.base}/{world.document_id}", params=binding(data),
    )).status_code == 409


@pytest.mark.parametrize("legacy", [False, True])
async def test_reparse_invalidates_binding_but_pinned_text_remains_immutable(
    client, session_factory, legacy,
):
    world = await seed(client, session_factory, legacy=legacy)
    data = await index(client, world)
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 200
    async with session_factory() as session:
        document = await session.get(UploadedDocument, world.document_id)
        document.current_parse_run = 3
        session.add(DocumentChunk(
            document_id=document.id, parse_run=3, ordinal=0, text="NOUVELLE LECTURE INTÉGRALE",
        ))
        await session.commit()
    updated = await index(client, world)
    assert updated["source_fingerprint"] != data["source_fingerprint"]
    assert fields(updated)["age"]["state"] == "unverified"
    assert not fields(updated)["age"]["reviewed"]
    assert (await client.post(world.base + "/review", json=review(data))).status_code == 409
    assert (await client.get(
        f"{world.base}/{world.document_id}", params=binding(data),
    )).status_code == 409
    result = await client.get(f"{world.base}/{world.document_id}", params=binding(updated))
    texts = [block["text"] for block in result.json()["blocks"]]
    assert texts == (["NOUVELLE LECTURE INTÉGRALE"] if legacy else list(PARTS))
    assert result.json()["document"]["parse_run"] == (3 if legacy else 2)


@pytest.mark.parametrize("mutation", ["checksum", "chunk_hash", "case", "missing_document"])
async def test_corrupt_pinned_manifest_fails_closed(client, session_factory, world, mutation):
    async with session_factory() as session:
        case = await session.get(Case, world.case_id)
        previous = await ledger.latest_version(session, case_id=case.id)
        snapshot = copy.deepcopy(previous.ledger)
        manifest = snapshot["patient_source_manifest"]
        if mutation == "checksum":
            manifest["documents"][0]["checksum_sha256"] = "b" * 64
        elif mutation == "chunk_hash":
            manifest["documents"][0]["chunks"][0]["text_sha256"] = "b" * 64
        elif mutation == "case":
            manifest["case_id"] = str(uuid.uuid4())
        else:
            manifest["documents"][0]["id"] = str(uuid.uuid4())
        session.add(CaseLedgerVersion(
            case_id=case.id, organization_id=case.organization_id, version=2,
            ledger=snapshot, missing_fields=[], uncertain_fields=[],
        ))
        await session.commit()
    response = await client.get(world.base)
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert PARTS[0] not in response.text


async def test_other_case_document_is_indistinguishable_from_unknown(
    client, session_factory, world,
):
    other = await seed(client, session_factory, parts=("PRIVATE OTHER CASE",))
    await sign_in(client, world.user)
    data = await index(client, world)
    for identifier in (other.document_id, uuid.uuid4()):
        result = await client.get(f"{world.base}/{identifier}", params=binding(data))
        assert result.status_code == 404
        assert result.json() == {"detail": "Document introuvable."}
        assert "PRIVATE OTHER CASE" not in result.text
    for params in ({}, {"ledger_version": 1}, {"source_fingerprint": "a" * 64}):
        assert (await client.get(
            f"{world.base}/{world.document_id}", params=params,
        )).status_code == 422


async def test_case_lock_revalidates_version_after_concurrent_publication(
    client, session_factory, world,
):
    data = await index(client, world)
    async with session_factory() as publisher:
        case = await publisher.get(Case, world.case_id)
        await publisher.execute(
            sa.select(Case.id).where(Case.id == world.case_id).with_for_update(key_share=True),
        )
        pending = asyncio.create_task(client.post(world.base + "/review", json=review(data)))
        try:
            await asyncio.sleep(0.1)
            assert not pending.done()
            await ledger.apply_correction(
                publisher, case=case, user=world.user, fact_type="age", value="70",
                note="Publication concurrente synthétique",
            )
            await ledger.record_version(
                publisher, case=case, user=world.user, change_reason="Concurrent publication",
            )
            await publisher.commit()
            response = await asyncio.wait_for(pending, timeout=10)
        finally:
            if not pending.done():
                pending.cancel()
    assert response.status_code == 409
    async with session_factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(AuditEvent).where(
            AuditEvent.event_type == sources.REVIEW_EVENT,
        )) == 0


async def test_review_service_checks_binding_after_lock(client, session_factory, world):
    data = await index(client, world)
    async with session_factory() as session:
        case = await session.get(Case, world.case_id)
        with pytest.raises(sources.StaleSourceReview):
            await sources.record_review(
                session, case=case, user=world.user,
                payload=SourceReviewRequest(**{**review(data), "source_fingerprint": "0" * 64}),
            )
