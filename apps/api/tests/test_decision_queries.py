"""Mechanical D contracts. Synthetic values and fake providers, not clinical relevance labels."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rcpia.agents.bundle import build_bundle, entries_from_version
from rcpia.agents.evidence import EvidenceTrace, guideline_passages_for
from rcpia.agents.panel import job_is_compatible
from rcpia.cases.ledger import LedgerEntry, LedgerReading, apply_correction, record_version
from rcpia.query.decision import (
    FACT_PROJECTION_CONTRACT,
    RETRIEVAL_CONTRACT,
    RetrievalQuery,
    decision_search,
    pin_query,
)


def request(**changes) -> RetrievalQuery:
    return RetrievalQuery.model_validate(
        {
            "mode": "decision_targeted_v1",
            "decision": "Quelle option discuter en RCP ?",
            "selected_fact_keys": ["ctnm", "egfr"],
            **changes,
        }
    )


def entry(key, value, **changes) -> LedgerEntry:
    return LedgerEntry(
        key=key,
        label=key,
        category="disease",
        value=value,
        **{"status": "confirmed", "origin": "machine", "highlight": "neutral", **changes},
    )


def plan(entries, *, query=None, operator=None):
    return decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=entries,
        request=query or request(),
        operator_question=operator,
    )


def test_only_selected_confirmed_values_enter_query_and_neutral_terms():
    built = plan(
        [
            entry("ctnm", "cT2 N0 M0"),
            entry("egfr", "28.2"),
            entry("age", "81"),
            entry("g8_score", "8"),
            entry("medical_history", "pembrolizumab"),
        ]
    )
    assert "28.2" in built.question
    assert "81" not in built.question and "g8" not in built.question.lower()
    assert "pembrolizumab" not in built.built.concepts.neutral
    assert "inéligible" not in built.question and "ineligible" not in built.question
    assert built.provenance.approval == "not_attested"


@pytest.mark.parametrize("status", ["missing", "uncertain", "conflicting", "inferred"])
def test_unresolved_values_do_not_enter_query_or_lexical_neutral_terms(status):
    result = plan([entry("egfr", "cisplatin M1", status=status, highlight="amber")])
    assert "cisplatin" not in result.question and "M1" not in result.question
    assert result.built.concepts.neutral == ()
    assert any(status in note for note in result.notes)
    assert not result.provenance.selected_facts[1].used


@pytest.mark.parametrize("origin", ["human", "machine"])
def test_confirmed_status_does_not_require_human_origin(origin):
    result = plan([entry("egfr", "28.2", origin=origin)])
    used = result.provenance.selected_facts[1]
    assert used.used and used.origin == origin
    assert result.provenance.approval == "not_attested"


@pytest.mark.parametrize("status", ["uncertain", "conflicting", "inferred"])
def test_unresolved_ctnm_vetoes_pathological_fallback(status):
    result = plan(
        [
            entry("ctnm", "cT2 N0 M1", status=status, highlight="amber"),
            entry("ptnm", "pT2 N0 M0"),
        ]
    )
    assert result.built.filter.disease_setting is None
    assert result.provenance.staging_veto == ["ctnm"]
    assert "M1" not in result.built.concepts.neutral


@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        "cT2 N0 M0 ?",
        "not a stage",
        "cT2 N0 M1 ou cT2 N0 M0",
        "cT2 N0 M1 ; cT3 N1 M0",
        "cT2 N0 M0 ; pT3 N0 M0",
    ],
)
def test_ambiguous_stage_text_vetoes_narrowing(targeted, value):
    result = decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=[entry("ctnm", value), entry("ptnm", "pT2 N0 M0")],
        request=request() if targeted else None,
        operator_question=None,
    )
    assert result.built.filter.disease_setting is None
    assert result.provenance.staging_veto == ["ctnm"]
    assert "M1" not in result.built.concepts.neutral


def test_identical_repeated_stage_components_are_not_competing():
    result = plan([entry("ctnm", "cT2 N0 M0 ; cT2 N0 M0")])
    assert result.built.filter.disease_setting == "bladder_mibc_non_metastatic"


def test_competing_readings_cannot_be_laundered_by_confirmed_status():
    result = plan(
        [
            entry(
                "ctnm",
                "cT2 N0 M1",
                readings=(
                    LedgerReading("cT2 N0 M1"),
                    LedgerReading("cT2 N0 M0"),
                ),
            )
        ]
    )
    assert result.built.filter.disease_setting is None
    assert "M1" not in result.question


def test_operator_wins_verbatim_but_request_and_selected_lexical_facts_are_audited():
    operator = "  Question opérateur, sans réécriture.  "
    result = plan([entry("egfr", "28.2")], operator=operator)
    assert result.question == operator
    assert result.provenance.effective_mode == "operator_verbatim"
    assert result.provenance.request.decision == request().decision


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "unknown"},
        {"decision": None},
        {"decision": " "},
        {"selected_fact_keys": []},
        {"selected_fact_keys": ["egfr", "egfr"]},
        {"selected_fact_keys": [123]},
        {"extra": True},
    ],
)
def test_invalid_requests_fail_strictly(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_unknown_scope_duplicate_ledger_and_oversized_queries_are_refused():
    with pytest.raises(ValueError, match="unknown"):
        plan([], query=request(selected_fact_keys=["not_a_field"]))
    with pytest.raises(ValueError, match="Duplicate ledger"):
        plan([entry("egfr", "1"), entry("egfr", "2")])
    with pytest.raises(ValueError, match="no clipping"):
        plan([entry("egfr", "x" * 4000)])


def test_case_question_is_resolved_and_pinned_without_a_template_fallback():
    query = request(decision_source="case", decision=None)
    pinned = pin_query(query, cancer_type="bladder", case_question="Question enregistrée")
    assert pinned.decision == "Question enregistrée"
    with pytest.raises(ValueError, match="pinned"):
        pin_query(query, cancer_type="bladder", case_question=None)
    with pytest.raises(ValueError, match="match"):
        pin_query(pinned, cancer_type="bladder", case_question="Nouvelle question")


def test_job_compatibility_includes_decision_mode_keys_and_exact_operator():
    query = request()
    job = SimpleNamespace(
        payload={
            "ledger_version": 1,
            "clinical_question": "Question",
            "retrieval_contract": RETRIEVAL_CONTRACT,
            "retrieval_query": query.model_dump(mode="json"),
        }
    )
    assert job_is_compatible(job, ledger_version=1, question="Question", retrieval_query=query)
    for other in (None, request(decision="Autre décision"), request(selected_fact_keys=["egfr"])):
        assert not job_is_compatible(
            job,
            ledger_version=1,
            question="Question",
            retrieval_query=other,
        )
    assert not job_is_compatible(
        job,
        ledger_version=1,
        question="question",
        retrieval_query=query,
    )


async def test_real_human_correction_supersedes_machine_readings(session_factory):
    from tests.test_agent_runs import seed_case

    user, case = await seed_case(session_factory)
    async with session_factory() as session:
        await apply_correction(
            session,
            case=case,
            user=user,
            fact_type="ctnm",
            value="cT2 N0 M0",
            note="Synthetic reviewer correction.",
        )
        version = await record_version(session, case=case, change_reason="Synthetic correction")
        entries = entries_from_version(version)
        corrected = next(e for e in entries if e.key == "ctnm")
        assert corrected.corrected and corrected.origin == "human"
        assert len(corrected.readings) == 1
        built = plan(entries)
        assert built.provenance.selected_facts[0].used
        bundle = await build_bundle(
            session,
            case=case,
            version=version,
            agent_role="medical_oncology",
            retrieval_query=request(),
        )
        assert bundle.bundle.inputs.first_pass is False
        assert bundle.bundle.inputs.retrieval_query.execution == "not_run"
        baseline = await build_bundle(
            session,
            case=case,
            version=version,
            agent_role="medical_oncology",
        )
        assert baseline.bundle.inputs.retrieval_query.request is None
        assert baseline.bundle.inputs.first_pass is True
        assert (
            baseline.bundle.inputs.retrieval_query.fact_projection_contract
            == FACT_PROJECTION_CONTRACT
        )
        from rcpia.agents.schemas import TaskBundle

        historical = baseline.bundle.model_dump(mode="json")
        historical["inputs"].pop("retrieval_query")
        restored = TaskBundle.model_validate(historical)
        assert restored.inputs.retrieval_query is None
        assert restored.inputs.first_pass is True


async def test_evidence_uses_two_unchanged_budgets_and_same_trace(monkeypatch):
    from rcpia.agents import evidence
    from rcpia.evidence.retrieval import RetrievalReport
    from rcpia.query.questions import SUPPORTIVE_QUESTION
    from tests.test_agent_supportive_retrieval import FakeEmbedder

    calls = []

    async def search(session, **kwargs):
        calls.append(kwargs)
        return RetrievalReport(chunks=[])

    monkeypatch.setattr(evidence, "search", search)
    trace = EvidenceTrace()
    await guideline_passages_for(
        None,
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=[entry("egfr", "28.2")],
        embedder=FakeEmbedder(),
        reranker=None,
        retrieval_query=request(),
        trace=trace,
    )
    assert [call["limit"] for call in calls] == [10, 4]
    assert calls[1]["question"] == SUPPORTIVE_QUESTION
    assert not calls[1]["concepts"]
    assert calls[0]["filter"] == calls[1]["filter"]
    assert trace.query_provenance["question"] == calls[0]["question"]
    assert trace.query_provenance["execution"] == "complete"


async def test_invalid_request_never_calls_provider():
    from unittest.mock import AsyncMock

    embedder = SimpleNamespace(embed=AsyncMock())
    with pytest.raises(ValueError, match="unknown"):
        await guideline_passages_for(
            None,
            cancer_type="bladder",
            agent_role="medical_oncology",
            entries=[],
            embedder=embedder,
            reranker=None,
            retrieval_query=request(selected_fact_keys=["unknown"]),
        )
    embedder.embed.assert_not_called()


@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.parametrize("status", ["missing", "uncertain", "conflicting", "inferred"])
def test_shared_projection_excludes_unresolved_facts_from_both_modes(targeted, status):
    result = decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=[
            entry("ctnm", "cT2 N0 M0"),
            entry("egfr", "erdafitinib M1", status=status),
        ],
        request=request() if targeted else None,
        operator_question=None,
    )
    assert "erdafitinib" not in result.question
    assert "M1" not in result.built.concepts.neutral
    assert "erdafitinib" not in result.built.concepts.neutral
    assert result.provenance.fact_projection_contract == FACT_PROJECTION_CONTRACT
    excluded = next(f for f in result.provenance.excluded_facts if f.key == "egfr")
    assert excluded.reason == status and not excluded.used


@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.parametrize("status", ["uncertain", "conflicting", "inferred"])
def test_both_modes_veto_ambiguous_clinical_stage_without_leaking_its_tokens(targeted, status):
    result = decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=[
            entry("ctnm", "cT2 N0 M1", status=status),
            entry("ptnm", "pT2 N0 M0"),
        ],
        request=request() if targeted else None,
        operator_question=None,
    )
    assert result.built.filter.disease_setting is None
    assert "M1" not in result.built.concepts.neutral
    assert "cT2" not in result.built.concepts.neutral
    assert result.provenance.staging_veto == ["ctnm"]
    assert "métastatique" not in result.question


def test_usable_confirmed_baseline_matches_legacy_query_filter_and_concepts():
    from rcpia.query.builder import Fact, build_filter
    from rcpia.query.questions import question_for

    entries = [
        entry("ctnm", "cT2 N0 M0"),
        entry("egfr", "28.2"),
        entry("medical_history", "BCG"),
        entry("age", "81"),
        entry("g8_score", "8"),
    ]
    legacy = build_filter(
        "bladder",
        [Fact(e.key, e.value) for e in entries],
        agent_role="medical_oncology",
    )
    result = decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=entries,
        request=None,
        operator_question=None,
    )
    assert result.built == legacy
    assert result.question == question_for("bladder", legacy.decision.setting, "medical_oncology")
    assert result.notes == ()
    assert result.provenance.request is None


def test_missing_ctnm_does_not_promote_pathology_into_current_clinical_extent():
    result = decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=[entry("ctnm", None, status="missing"), entry("ptnm", "pT2 N0 M0")],
        request=None,
        operator_question=None,
    )
    assert result.built.filter.disease_setting is None
    assert not result.provenance.staging_veto
    assert "not substituted" in result.provenance.setting_reason


def test_legacy_baseline_job_cannot_match_current_baseline_contract():
    job = SimpleNamespace(payload={"ledger_version": 1, "clinical_question": None})
    assert not job_is_compatible(job, ledger_version=1, question=None)
    job.payload["retrieval_contract"] = RETRIEVAL_CONTRACT
    assert job_is_compatible(job, ledger_version=1, question=None)


async def test_worker_refuses_legacy_job_and_legacy_cached_opinion_before_provider():
    from unittest.mock import AsyncMock

    from rcpia.agents.service import run_agent
    from rcpia.core.models import JobStatus

    session = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(None, None)])),
    )
    adapter = SimpleNamespace(run=AsyncMock())
    job = SimpleNamespace(id="synthetic-job", payload={"agent_role": "medical_oncology"})
    assert (await run_agent(session, job, adapter=adapter, blobs=None)).status is JobStatus.failed
    session.scalar.assert_not_called()
    job.payload["retrieval_contract"] = RETRIEVAL_CONTRACT
    assert (await run_agent(session, job, adapter=adapter, blobs=None)).status is JobStatus.failed
    adapter.run.assert_not_called()


@pytest.mark.parametrize("targeted", [False, True])
def test_operator_text_is_verbatim_despite_shared_staging_veto(targeted):
    operator = "Question explicite sur le M1 non confirmé"
    result = decision_search(
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=[entry("ctnm", "cT2 N0 M1", status="conflicting")],
        request=request() if targeted else None,
        operator_question=operator,
    )
    assert result.question == operator
    assert result.built.filter.disease_setting is None
    assert "M1" not in result.built.concepts.neutral
    assert result.provenance.effective_mode == "operator_verbatim"
