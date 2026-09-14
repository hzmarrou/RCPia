"""Unknown is not a negative, regardless of extractor, origin or snapshot age."""

import json
import uuid
from copy import deepcopy
from dataclasses import asdict, replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents import routes as analysis_routes
from rcpia.agents.bundle import build_bundle, entries_from_version
from rcpia.auth.dependencies import current_user
from rcpia.cases import ledger, ledger_routes, service
from rcpia.cases.completeness import assess, satisfies_blocking
from rcpia.cases.extraction import FactCandidate, extract_from_text, resolve
from rcpia.cases.fact_schema import is_unknown_value, required_fields_for
from rcpia.cases.facts import _needs_narrative_reading, _to_rows
from rcpia.cases.llm_extractor import parse_response
from rcpia.core.db import get_session
from rcpia.core.models import (
    CancerType,
    Case,
    CaseLedgerVersion,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    User,
    UserRole,
)
from rcpia.nomograms.contract import Status
from rcpia.nomograms.record import Tristate, as_number, as_tristate
from rcpia.nomograms.service import evaluate_all
from rcpia.pps.generator import build_missing
from rcpia.query.decision import decision_search, exclusion, usable_facts

UNKNOWN = [
    None, "", " \t\n", "-", "?", "Non renseigné", "non renseigne", "Non renseignée",
    " NON\u00a0RENSEIGNÉES. ", "(Non renseigné)", "« Non renseigné ! »", "inconnu",
    "Inconnue.", "UNKNOWN", "not documented", "not recorded", "not provided",
    "non documenté", "Non précisé", "NR", "N.R.", "n/r", " N / R ",
]
KNOWN = [
    "Non", "no", "muscle vu non infiltré", "Aucune allergie", "RAS", "R.A.S.",
    "néant", "Sans particularité", "0", "Carcinome urothélial - haut grade",
    "Non renseigné : carcinome urothélial décrit dans le rapport",
    "Carcinome urothélial ; antécédents inconnus",
    "unknown primary carcinoma", "Non infiltré ?", "Oui ?", "score inconnu 7",
]


def entry(
    value: str | None, *, key: str = "muscle_invasion", origin: str = "machine",
) -> ledger.LedgerEntry:
    return ledger.LedgerEntry(
        key=key, label=ledger.display_label(key), category="pathology", value=value,
        status="confirmed", origin=origin, highlight="neutral",
        fact_id=str(uuid.uuid4()), source_document_id=str(uuid.uuid4()),
        source_chunk_id=str(uuid.uuid4()), source_excerpt=f"Champ synthétique : {value}",
        corrected=origin == "human", note="Note de relecture" if origin == "human" else None,
    )


@pytest.mark.parametrize("value", UNKNOWN)
def test_whole_unknown_markers(value):
    assert is_unknown_value(value)
    assert not satisfies_blocking("muscle_invasion", value)
    assert not satisfies_blocking("histology", value)
    assert as_tristate(value) is Tristate.unknown
    assert as_number(value) is None
    assert _needs_narrative_reading(value, "muscle_invasion")


@pytest.mark.parametrize("value", KNOWN)
def test_not_substring_matching_or_negative_inference(value):
    assert not is_unknown_value(value)


@pytest.mark.parametrize("value", ["Non", "muscle vu non infiltré"])
def test_real_muscle_negatives_answer_the_gate(value):
    assert satisfies_blocking("muscle_invasion", value)


def test_ras_keeps_context_dependent_meaning():
    assert ledger.project_entry(entry("RAS", key="allergies")).value == "RAS"
    assert ledger.project_entry(entry("RAS", key="histology")).value is None
    assert not satisfies_blocking("muscle_invasion", "RAS")
    assert as_tristate("RAS") is Tristate.unknown


@pytest.mark.parametrize("origin", ["machine", "human"])
@pytest.mark.parametrize("value", ["Non renseigné", "UNKNOWN", "NR", ""])
def test_projection_keeps_literal_source_without_mutating_old_confirmation(origin, value):
    old = entry(value, origin=origin)
    projected = ledger.project_entry(old)
    assert projected.value is None
    assert projected.status == "missing"
    assert projected.highlight == "red"
    assert projected.source_excerpt == old.source_excerpt
    assert projected.fact_id == old.fact_id
    assert projected.readings[0].value == value
    assert value in projected.note
    if origin == "human":
        assert old.note in projected.note
    assert old.status == "confirmed" and old.value == value
    assert ledger.project_entry(projected) == projected
    report = assess(
        [entry("Carcinome urothélial", key="histology"), old],
        cancer_type=CancerType.bladder,
    )
    assert not report.can_run_analysis
    assert [(issue.key, issue.highlight) for issue in report.blocking] == [
        ("muscle_invasion", "red"),
    ]
    assert exclusion(old) is not None
    assert usable_facts([old]) == []


@pytest.mark.parametrize("origin", [FactOrigin.machine, FactOrigin.human])
async def test_live_old_fact_projects_without_rewriting_row(monkeypatch, origin):
    case = Case(id=uuid.uuid4(), organization_id=uuid.uuid4(), cancer_type=CancerType.bladder)
    fact = ExtractedFact(
        id=uuid.uuid4(), fact_type="muscle_invasion", value="Non renseigné",
        status=FactStatus.confirmed, origin=origin, source_excerpt="Muscle : Non renseigné",
    )
    monkeypatch.setattr(ledger, "current_readings", AsyncMock(return_value={
        "muscle_invasion": [fact],
    }))
    session = AsyncMock(spec=AsyncSession)
    entries = await ledger.build_entries(session, case=case)
    projected = next(item for item in entries if item.key == "muscle_invasion")
    assert projected.status == "missing" and projected.highlight == "red"
    assert projected.readings[0].value == "Non renseigné"
    assert fact.status is FactStatus.confirmed and fact.value == "Non renseigné"
    session.execute.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("origin", ["machine", "human"])
async def test_old_snapshot_bundle_retrieval_and_missing_view_share_projection(origin):
    old = entry("Non renseigné", origin=origin)
    version = CaseLedgerVersion(
        id=uuid.uuid4(), version=1,
        ledger={"entries": [asdict(entry("Carcinome urothélial", key="histology")),
                            asdict(old)]},
        missing_fields=[], uncertain_fields=[],
    )
    original = deepcopy(version.ledger)
    entries = entries_from_version(version)
    projected = next(item for item in entries if item.key == old.key)
    assert projected.status == "missing" and projected.value is None
    planned = decision_search(
        cancer_type="bladder", agent_role="urology_oncology", entries=entries,
        request=None, operator_question=None,
    )
    assert "muscle_invasion" in planned.provenance.unusable_keys
    session = AsyncMock(spec=AsyncSession)
    # No database needed: source page metadata is independent of fact semantics.
    session.execute.return_value = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []
    built = await build_bundle(
        session,
        case=Case(id=uuid.uuid4(), cancer_type=CancerType.bladder),
        agent_role="urology_oncology", version=version,
    )
    assert not built.report.can_run_analysis
    assert "muscle_invasion" in built.bundle.inputs.missing_fields
    assert any(item.label == old.label and item.highlight == "red"
               for item in build_missing(built.report))
    field = next(f for f in built.bundle.inputs.case_ledger if f.key == old.key)
    assert field.status == "missing" and field.value is None
    assert field.source_ids
    assert any("Non renseigné" in s.text for s in built.bundle.inputs.source_snippets)
    assert version.ledger == original
    assert version.missing_fields == []


@pytest.mark.parametrize("value", ["Non renseigné", "non renseignée.", "unknown", "NR"])
def test_deterministic_intake_keeps_source_and_stores_missing(value):
    text = f"Infiltration du muscle : {value}"
    chunk_id, document_id = uuid.uuid4(), uuid.uuid4()
    candidates = extract_from_text(text, chunk_id=chunk_id, document_id=document_id)
    fact = next(f for f in resolve(candidates, cancer_type=CancerType.bladder)
                if f.key == "muscle_invasion")
    assert fact.status is FactStatus.missing
    assert fact.value == value and fact.excerpt == text
    rows = _to_rows(
        Case(id=uuid.uuid4(), organization_id=uuid.uuid4()), fact, "synthetic-generation",
    )
    assert len(rows) == 1
    assert rows[0].status is FactStatus.missing and rows[0].value == value
    assert rows[0].source_chunk_id == chunk_id
    assert rows[0].source_document_id == document_id
    assert rows[0].source_excerpt == text


def test_narrative_placeholder_is_retained_but_never_claimed_as_filled():
    text = "Infiltration du muscle : Non renseigné."
    raw = json.dumps({"facts": [{
        "field": "muscle_invasion", "value": "Non renseigné", "source": text,
    }]})
    parsed = parse_response(raw, dossier_text=text, wanted=["muscle_invasion"])
    assert parsed.still_missing == ["muscle_invasion"]
    fact = next(f for f in resolve(parsed.candidates, cancer_type=CancerType.bladder)
                if f.key == "muscle_invasion")
    assert fact.status is FactStatus.missing and fact.readings[0].excerpt == text


@pytest.mark.parametrize("invented", ["Non", "0", "Normal"])
def test_llm_cannot_turn_a_quoted_unknown_field_into_a_negative_or_result(invented):
    text = "Infiltration du muscle : Non renseigné."
    parsed = parse_response(
        json.dumps({"facts": [{
            "field": "muscle_invasion", "value": invented, "source": text,
        }]}),
        dossier_text=text, wanted=["muscle_invasion"],
    )
    assert not parsed.candidates and parsed.still_missing == ["muscle_invasion"]
    assert "non renseignée" in parsed.rejected[0].reason


def test_a_gap_can_be_filled_but_two_real_answers_still_conflict():
    gap = FactCandidate(key="muscle_invasion", value="NR", excerpt="Muscle : NR")
    no = FactCandidate(key="muscle_invasion", value="Non", excerpt="Muscle : Non")
    yes = replace(no, value="Oui", excerpt="Muscle : Oui")
    for candidates, expected in [
        ([gap, no], FactStatus.confirmed),
        ([gap, no, yes], FactStatus.conflicting),
        ([gap, replace(gap, value="Non renseigné")], FactStatus.missing),
    ]:
        fact = next(f for f in resolve(candidates, cancer_type=CancerType.bladder)
                    if f.key == "muscle_invasion")
        assert fact.status is expected


async def test_recovered_value_keeps_the_unknown_reading_and_its_provenance():
    gap = FactCandidate(
        key="muscle_invasion", value="NR", excerpt="Muscle : NR",
        chunk_id=uuid.uuid4(), document_id=uuid.uuid4(),
    )
    answer = replace(gap, value="Non", excerpt="Muscle : Non", chunk_id=uuid.uuid4())
    fact = next(f for f in resolve([gap, answer], cancer_type=CancerType.bladder)
                if f.key == "muscle_invasion")
    rows = _to_rows(Case(id=uuid.uuid4()), fact, "synthetic-generation")
    assert [(r.value, r.status) for r in rows] == [
        ("Non", FactStatus.confirmed), ("NR", FactStatus.missing),
    ]
    assert rows[1].source_chunk_id == gap.chunk_id
    assert rows[1].source_excerpt == gap.excerpt
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(reversed(rows))
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result
    live = await ledger.build_entries(
        session, case=Case(id=uuid.uuid4(), cancer_type=CancerType.bladder),
    )
    recovered = next(e for e in live if e.key == "muscle_invasion")
    assert recovered.value == "Non" and recovered.status == "confirmed"
    assert {r.value for r in recovered.readings} == {"Non", "NR"}
    assert exclusion(recovered) is None


@pytest.mark.parametrize("value", ["Non renseigné", "NR", "", None, "Non"])
async def test_human_correction_appends_with_safe_status_and_original_note(monkeypatch, value):
    previous = ExtractedFact(id=uuid.uuid4(), value="Non renseigné", status=FactStatus.confirmed)
    monkeypatch.setattr(ledger, "current_facts", AsyncMock(return_value={
        "muscle_invasion": previous,
    }))
    session = AsyncMock(spec=AsyncSession)
    correction = await ledger.apply_correction(
        session, case=Case(id=uuid.uuid4(), cancer_type=CancerType.bladder),
        user=User(id=uuid.uuid4()), fact_type="muscle_invasion", value=value,
        note="Vérification synthétique du champ",
    )
    assert correction.status is (
        FactStatus.missing if is_unknown_value(value) else FactStatus.confirmed
    )
    assert correction.value == value and correction.note == "Vérification synthétique du champ"
    assert correction.supersedes_fact_id == previous.id
    assert correction.origin is FactOrigin.human
    assert previous.value == "Non renseigné" and previous.status is FactStatus.confirmed
    session.execute.assert_not_called()


def test_known_prostate_blockers_remain_usable_and_do_not_attest_eligibility():
    entries = [entry("Adénocarcinome acinaire", key="histology"),
               entry("7 (3+4)", key="gleason"), entry("6", key="psa_total")]
    assert assess(entries, cancer_type=CancerType.prostate).can_run_analysis
    assert len(usable_facts(entries)) == 3
    assert ledger.adenocarcinoma_confirmation(entries) == "unknown"
    assert ledger.adenocarcinoma_confirmation([
        entry("Oui", key=ledger.ADENOCARCINOMA_CONFIRMATION),
    ]) == "unknown"


def test_synthetic_all_required_prostate_fields_remain_a_positive_control():
    values = {
        "sex": "Homme", "age": "68", "medical_history": "HTA",
        "illness_history": "Dépistage", "ct_tap": "Pas de métastase décrite",
        "haemoglobin": "13", "creatinine": "10", "egfr": "75",
        "prostate_mri": "Lésion prostatique sans extension décrite", "isup": "2",
        "histology": "Adénocarcinome acinaire - grade intermédiaire",
        "ptnm": "pT2", "gleason": "7 (3+4)", "psa_total": "6",
    }
    assert set(spec.key for spec in required_fields_for(CancerType.prostate)) <= values.keys()
    report = assess([entry(value, key=key) for key, value in values.items()],
                    cancer_type=CancerType.prostate)
    assert report.can_run_analysis and not report.issues


@pytest.mark.parametrize("therapy", ["Non renseigné", "inconnu", "not documented", "NR"])
def test_nomogram_missing_treatment_is_not_eligible_as_no_prior_treatment(therapy):
    facts = {
        "age": "68", "psa_total": "6", "gleason": "7 (3+4)", "ctnm": "cT2a N0 M0",
        "ct_dre": "cT2a", "cores_positive": "3", "cores_total": "12",
        "hormone_therapy": "Non", "radiation_therapy": "Non",
    }
    known = evaluate_all(
        facts, cancer_type="prostate", adenocarcinoma_confirmed=Tristate.yes,
    )
    assert all(result.status is Status.calculated for result in known)
    unknown = evaluate_all(
        {**facts, "hormone_therapy": therapy},
        cancer_type="prostate", adenocarcinoma_confirmed=Tristate.yes,
    )
    mskcc = next(result for result in unknown if "mskcc" in result.model_id)
    assert mskcc.status is Status.not_possible
    assert "hormone_therapy" in mskcc.missing_fields


@pytest.mark.parametrize("live_value", ["Non renseigné", "Non"])
async def test_actual_post_gates_live_and_stored_entries_without_enqueuing(monkeypatch, live_value):
    app = FastAPI()
    app.include_router(ledger_routes.router)
    app.include_router(analysis_routes.router)
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid.uuid4(), role=UserRole.uploader)
    case = Case(id=uuid.uuid4(), cancer_type=CancerType.bladder)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: user
    monkeypatch.setattr(service, "get_case", AsyncMock(return_value=case))
    raw_entries = [
        entry("Carcinome urothélial", key="histology"), entry("Non renseigné"),
    ]
    rows = {
        e.key: [ExtractedFact(
            id=uuid.uuid4(), fact_type=e.key, value=live_value if e.key == "muscle_invasion"
            else e.value, status=FactStatus.confirmed, origin=FactOrigin.machine,
            source_excerpt=e.source_excerpt,
        )] for e in raw_entries
    }
    monkeypatch.setattr(ledger, "current_readings", AsyncMock(return_value=rows))
    monkeypatch.setattr(ledger, "latest_version", AsyncMock(return_value=CaseLedgerVersion(
        version=1, ledger={"entries": [asdict(e) for e in raw_entries]},
    )))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        if live_value == "Non renseigné":
            response = await http.get(f"/cases/{case.id}/ledger")
            assert response.status_code == 200
            body = response.json()
            assert not body["can_run_analysis"]
            field = next(e for e in body["entries"] if e["key"] == "muscle_invasion")
            assert field["status"] == "missing" and field["highlight"] == "red"
            assert any(i["severity"] == "blocking" for i in body["issues"])
        for path in ("analysis", "analysis/panel"):
            payload = {"roles": ["urology_oncology"]} if path.endswith("panel") else {}
            response = await http.post(f"/cases/{case.id}/{path}", json=payload)
            assert response.status_code == 409, response.text
            assert "Infiltration du muscle" in response.json()["detail"]
    session.execute.assert_not_called()
    session.add.assert_not_called()
