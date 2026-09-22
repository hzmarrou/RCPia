"""Private ephemeral PostgreSQL only; synthetic cases and the existing fake Pi runtime."""

from copy import deepcopy
from dataclasses import asdict, replace

import pytest
import sqlalchemy as sa

from rcpia.agents.bundle import build_bundle, entries_from_version
from rcpia.cases import facts, ledger
from rcpia.core.models import (
    AgentRun,
    AnalysisJob,
    AnalysisPanel,
    Case,
    CaseLedgerVersion,
    CaseStatus,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    JobStatus,
)
from rcpia.jobs.models import JobType
from tests import test_agent_runs as agent_run_fixtures
from tests.test_agent_runs import (
    adapter as adapter,
)
from tests.test_agent_runs import (
    agent_job,
    execute,
    seed_case,
    sign_in,
)
from tests.test_agent_runs import (
    agents_root as agents_root,
)
from tests.test_agent_runs import (
    blobs as blobs,
)
from tests.test_agent_runs import (
    client as client,
)
from tests.test_agent_runs import (
    reset_throttle as reset_throttle,
)

SYNTHETIC = "Type histologique : Carcinome urothélial\nInfiltration du muscle : Non renseigné"


async def historical_case(factory, monkeypatch, origin):
    """Write the pre-fix shape initially, never UPDATE an append-only historical row."""
    original_resolve = facts.resolve

    def legacy_resolve(candidates, *, cancer_type):
        return [
            replace(fact, status=FactStatus.confirmed)
            if fact.key == "muscle_invasion" else fact
            for fact in original_resolve(candidates, cancer_type=cancer_type)
        ]

    async def legacy_record_version(session, *, case, change_reason):
        entries = await ledger.build_entries(session, case=case)
        previous = await ledger.latest_version(session, case_id=case.id)
        version = CaseLedgerVersion(
            case_id=case.id, organization_id=case.organization_id,
            version=previous.version + 1 if previous else 1,
            ledger={"entries": [asdict(entry) for entry in entries]},
            missing_fields=ledger.missing_keys(entries),
            uncertain_fields=ledger.uncertain_keys(entries), change_reason=change_reason,
        )
        session.add(version)
        await session.flush()
        return version

    with monkeypatch.context() as legacy:
        legacy.setattr(facts, "resolve", legacy_resolve)
        legacy.setattr(facts, "is_unknown_value", lambda value: False)
        legacy.setattr(ledger, "project_entry", lambda entry: entry)
        legacy.setattr(ledger, "record_version", legacy_record_version)
        legacy.setattr(agent_run_fixtures, "record_version", legacy_record_version)
        user, case = await seed_case(factory, text=SYNTHETIC)
        if origin is FactOrigin.human:
            async with factory() as session:
                session.add(ExtractedFact(
                    case_id=case.id, organization_id=case.organization_id,
                    fact_type="muscle_invasion", value="Non renseigné",
                    status=FactStatus.confirmed, origin=origin,
                    recorded_by_user_id=user.id, note="Ancienne saisie synthétique",
                ))
                await session.flush()
                await ledger.record_version(
                    session, case=case, change_reason="Ancienne version synthétique",
                )
                await session.commit()
    return user, case


async def assert_no_analysis(factory, case_id):
    async with factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(AnalysisJob).where(
            AnalysisJob.case_id == case_id, AnalysisJob.job_type == JobType.RUN_AGENT,
        )) == 0
        assert await session.scalar(sa.select(sa.func.count()).select_from(AnalysisPanel).where(
            AnalysisPanel.case_id == case_id,
        )) == 0
        assert await session.scalar(sa.select(sa.func.count()).select_from(AgentRun).where(
            AgentRun.case_id == case_id,
        )) == 0


async def test_new_intake_payload_and_both_dispatches_are_blocked(client, session_factory):
    user, case = await seed_case(session_factory, text=SYNTHETIC)
    await sign_in(client, user)
    body = (await client.get(f"/cases/{case.id}/ledger")).json()
    field = next(e for e in body["entries"] if e["key"] == "muscle_invasion")
    assert not body["can_run_analysis"]
    assert field["highlight"] == "red" and field["status"] == "missing"
    assert field["readings"][0]["value"] == "Non renseigné"
    assert any(i["key"] == "muscle_invasion" and i["severity"] == "blocking"
               for i in body["issues"])
    for path in ("analysis", "analysis/panel"):
        payload = {"roles": ["urology_oncology"]} if path.endswith("panel") else {}
        response = await client.post(f"/cases/{case.id}/{path}", json=payload)
        assert response.status_code == 409
    await assert_no_analysis(session_factory, case.id)


@pytest.mark.parametrize("origin", [FactOrigin.machine, FactOrigin.human])
async def test_stored_confirmed_placeholder_projects_and_real_correction_unblocks(
    client, session_factory, monkeypatch, adapter, blobs, origin,
):
    user, case = await historical_case(session_factory, monkeypatch, origin)
    await sign_in(client, user)
    async with session_factory() as session:
        old = await ledger.latest_version(session, case_id=case.id)
        old_id, old_number, raw = old.id, old.version, deepcopy(old.ledger)
        old_fact = (await ledger.current_facts(session, case_id=case.id))["muscle_invasion"]
        old_fact_id = old_fact.id
        assert old_fact.status is FactStatus.confirmed
        built = await build_bundle(
            session, case=case, version=old, agent_role="urology_oncology",
        )
        assert not built.report.can_run_analysis
        assert "muscle_invasion" in built.bundle.inputs.missing_fields
        assert next(e for e in entries_from_version(old) if e.key == "muscle_invasion").value \
            is None
    body = (await client.get(f"/cases/{case.id}/ledger")).json()
    assert body["version"] == old_number and not body["can_run_analysis"]
    field = next(e for e in body["entries"] if e["key"] == "muscle_invasion")
    assert field["status"] == "missing" and field["highlight"] == "red"
    assert field["readings"][0]["value"] == "Non renseigné"
    for path in ("analysis", "analysis/panel"):
        payload = {"roles": ["urology_oncology"]} if path.endswith("panel") else {}
        assert (await client.post(f"/cases/{case.id}/{path}", json=payload)).status_code == 409
    await assert_no_analysis(session_factory, case.id)

    unknown = await client.post(f"/cases/{case.id}/ledger/corrections", json={
        "fact_type": "muscle_invasion", "value": "NR", "note": "Saisie synthétique inconnue",
    })
    assert unknown.status_code == 201
    assert not unknown.json()["can_run_analysis"]
    assert unknown.json()["version"] == old_number + 1
    assert (await client.post(
        f"/cases/{case.id}/analysis/panel", json={"roles": ["urology_oncology"]},
    )).status_code == 409
    corrected = await client.post(f"/cases/{case.id}/ledger/corrections", json={
        "fact_type": "muscle_invasion", "value": "Non", "note": "Correction synthétique valide",
    })
    assert corrected.status_code == 201 and corrected.json()["can_run_analysis"]
    assert corrected.json()["version"] == old_number + 2
    async with session_factory() as session:
        unchanged = await session.get(CaseLedgerVersion, old_id)
        assert unchanged.ledger == raw
        unchanged_fact = await session.get(ExtractedFact, old_fact_id)
        assert unchanged_fact.value == "Non renseigné"
        assert unchanged_fact.status is FactStatus.confirmed
        current = (await ledger.current_facts(session, case_id=case.id))["muscle_invasion"]
        intermediate = await session.get(ExtractedFact, current.supersedes_fact_id)
        assert intermediate.value == "NR" and intermediate.status is FactStatus.missing
        assert intermediate.supersedes_fact_id == old_fact_id
        assert current.value == "Non" and current.status is FactStatus.confirmed
        stored_case = await session.get(Case, case.id)
        assert stored_case.status is CaseStatus.ready_for_review
    started = await client.post(f"/cases/{case.id}/analysis/panel", json={
        "roles": ["urology_oncology"],
    })
    assert started.status_code == 202
    async with session_factory() as session:
        job = await session.scalar(sa.select(AnalysisJob).where(
            AnalysisJob.case_id == case.id, AnalysisJob.job_type == JobType.RUN_AGENT,
        ))
        job_id = job.id
        assert job.payload["ledger_version"] == old_number + 2
    assert await execute(session_factory, job_id, adapter, blobs, monkeypatch) is None


async def test_live_correction_without_snapshot_cannot_dispatch_old_unknown_version(
    client, session_factory, monkeypatch,
):
    user, case = await historical_case(session_factory, monkeypatch, FactOrigin.machine)
    await sign_in(client, user)
    async with session_factory() as session:
        await ledger.apply_correction(
            session, case=case, user=user, fact_type="muscle_invasion",
            value="Non", note="Correction synthétique sans nouvelle version",
        )
        await session.commit()
    for path in ("analysis", "analysis/panel"):
        payload = {"roles": ["urology_oncology"]} if path.endswith("panel") else {}
        assert (await client.post(f"/cases/{case.id}/{path}", json=payload)).status_code == 409
    await assert_no_analysis(session_factory, case.id)


async def test_stale_job_uses_its_blocked_snapshot_and_is_not_reused(
    client, session_factory, monkeypatch, adapter, blobs,
):
    user, case = await historical_case(session_factory, monkeypatch, FactOrigin.machine)
    await sign_in(client, user)
    async with session_factory() as session:
        version = await ledger.latest_version(session, case_id=case.id)
    job_id = await agent_job(session_factory, case, ledger_version=version.version)
    corrected = await client.post(f"/cases/{case.id}/ledger/corrections", json={
        "fact_type": "muscle_invasion", "value": "Non", "note": "Correction synthétique",
    })
    assert corrected.json()["can_run_analysis"]
    assert (await client.post(f"/cases/{case.id}/analysis", json={})).status_code == 409
    assert (await client.post(
        f"/cases/{case.id}/analysis/panel", json={"roles": ["urology_oncology"]},
    )).status_code == 409
    outcome = await execute(session_factory, job_id, adapter, blobs, monkeypatch)
    assert outcome.status is JobStatus.waiting_for_user
    async with session_factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(AgentRun).where(
            AgentRun.case_id == case.id,
        )) == 0
        stored_case = await session.get(Case, case.id)
        assert stored_case.status is CaseStatus.ready_for_review


async def test_legacy_unknown_first_snapshot_retains_structured_known_conflict(
    client, session_factory,
):
    text = (
        "Type histologique : Carcinome urothélial\n"
        "Infiltration du muscle : NR\n"
        "Infiltration du muscle : Non\n"
        "Infiltration du muscle : Oui"
    )
    user, case = await seed_case(session_factory, text=text)
    await sign_in(client, user)
    async with session_factory() as session:
        previous = await ledger.latest_version(session, case_id=case.id)
        raw = deepcopy(previous.ledger)
        raw.pop("patient_source_manifest", None)  # This test recreates a pre-manifest snapshot.
        raw.pop("patient_context", None)
        field = next(e for e in raw["entries"] if e["key"] == "muscle_invasion")
        unknown = next(r for r in field["readings"] if r["value"] == "NR")
        field.update(unknown)
        field["status"], field["highlight"] = "conflicting", "amber"
        field["readings"].sort(key=lambda reading: ["NR", "Non", "Oui"].index(reading["value"]))
        version = CaseLedgerVersion(
            case_id=case.id, organization_id=case.organization_id, version=previous.version + 1,
            ledger=raw, missing_fields=[], uncertain_fields=["muscle_invasion"],
            change_reason="Ancienne représentation synthétique : lecture inconnue en premier",
            created_by_user_id=user.id,
        )
        session.add(version)
        await session.commit()
        version_id, expected = version.id, deepcopy(version.ledger)
    async with session_factory() as session:
        stored = await session.get(CaseLedgerVersion, version_id)
        entries = entries_from_version(stored)
        projected = next(e for e in entries if e.key == "muscle_invasion")
        assert projected.status == "conflicting" and projected.highlight == "amber"
        assert projected.value == "Non"
        assert [r.value for r in projected.readings] == ["NR", "Non", "Oui"]
        built = await build_bundle(
            session, case=case, agent_role="urology_oncology", version=stored,
        )
        conflict = next(c for c in built.bundle.inputs.conflicting_fields
                        if c.field == "muscle_invasion")
        assert conflict.values == ["Non", "Oui"] and conflict.source_ids
        assert not built.report.can_run_analysis
        assert "muscle_invasion" not in built.bundle.inputs.missing_fields
        assert any("NR" in snippet.text for snippet in built.bundle.inputs.source_snippets)
        assert stored.ledger == expected
    body = (await client.get(f"/cases/{case.id}/ledger")).json()
    field = next(e for e in body["entries"] if e["key"] == "muscle_invasion")
    assert field["status"] == "conflicting" and field["highlight"] == "amber"
    for path in ("analysis", "analysis/panel"):
        payload = {"roles": ["urology_oncology"]} if path.endswith("panel") else {}
        assert (await client.post(f"/cases/{case.id}/{path}", json=payload)).status_code == 409
    await assert_no_analysis(session_factory, case.id)
