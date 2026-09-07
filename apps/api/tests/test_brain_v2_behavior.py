"""Hermetic V2 contract regressions, not a simulated model benchmark.

The outputs below are HANDWRITTEN witnesses and counterexamples. They exercise the
real parsing/validation and pure PPS assembly functions without a provider, runtime,
database or real dossier. Passing proves those contracts still accept/reject the
specified structures, NOT that either brain produces them. Model behavior must be
reviewed separately against ``BrainCase.expected`` on identical V1/V2 inputs.

Acceptance tests explicitly labelled "mechanical" document gaps between today's
validators and the stronger semantic oracle; they must not be counted as safe model
answers. In particular, neither valid citations nor attested words prove entailment.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from rcpia.agents.bundle import SAFETY_CONSTRAINTS
from rcpia.agents.opinions import opinion_of
from rcpia.agents.pi_adapter import PiOutputInvalid, PiRunResult, PiUsage
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOutput,
    CitationInWrongChannel,
    SynthesisOutput,
    TaskBundle,
    UnattestedAgreement,
    UncitedEvidence,
    UngroundedRecommendation,
    check_citations,
    check_synthesis,
    output_model_for,
)
from rcpia.agents.service import _validated_output
from rcpia.core.models import FactStatus
from rcpia.ops.brain_v2_cases import (
    COMMON_CRITERIA,
    SPECIALIST_ROLES,
    SPECIALIST_SCENARIOS,
    SYNTHESIS_SCENARIOS,
    SYNTHETIC_NOTICE,
    BrainCase,
    shadow_cases,
)
from rcpia.pps.generator import (
    agent_label,
    build_decision,
    build_framework,
    build_panel,
    build_treatments,
)
from rcpia.pps.schemas import (
    DocumentAssertsADecision,
    PpsDocument,
    SocialAssessment,
    SupportCare,
    Synthesis,
)


def _case(scenario: str, role: str = SPECIALIST_ROLES[0]) -> BrainCase:
    return next(
        case
        for case in shadow_cases()
        if case.case_id == f"synthetic-{scenario}-{role}"
    )


def _payload(case: BrainCase) -> dict[str, Any]:
    """A handwritten allowed structure, never presented as a model-produced answer."""
    inputs = case.bundle.inputs
    payload: dict[str, Any] = {
        "case_id": inputs.case_id,
        "agent_role": inputs.agent_role,
        "key_facts_used": ["[CONTEXT_A] documenté."],
        "recommendation": "Proposer [OPTION_A] sous [CONDITION_A] documentée.",
        "rationale": "Proposition abstraite provisoire à relire, non une décision.",
        "evidence_references": [{"id": "ev-001", "supports": "Condition de [OPTION_A]."}],
    }
    if inputs.agent_role == SYNTHESIS_ROLE:
        first, second = inputs.agent_opinions
        roles = [first.agent_role, second.agent_role]
        if "atomic-convergence" in case.case_id:
            payload["convergences"] = [
                {
                    "point": "Proposition de [OPTION_A].",
                    "agents_agreeing": roles,
                    "support": [
                        {"role": opinion.agent_role, "quote": opinion.recommendation}
                        for opinion in inputs.agent_opinions
                    ],
                }
            ]
            payload["coverage_gaps"] = [
                {
                    "point": "Rejet de [OPTION_B].",
                    "raised_by": [
                        {"role": first.agent_role, "quote": first.rejected_options[0].option}
                    ],
                    "silent": [second.agent_role],
                }
            ]
        elif "divergence" in case.case_id:
            payload["recommendation"] = "Présenter les deux positions sur [OPTION_A] à la RCP."
            payload["divergences"] = [
                {
                    "point": "Proposition de [OPTION_A].",
                    "agent_a": {
                        "role": first.agent_role,
                        "position": "Proposition",
                        "quote": first.recommendation,
                    },
                    "agent_b": {
                        "role": second.agent_role,
                        "position": "Opposition explicite",
                        "quote": second.recommendation,
                    },
                    "clinical_impact": "L'option ne peut être présentée comme partagée.",
                    "resolution_required": True,
                }
            ]
        elif "withheld-and-missing-opinion" in case.case_id:
            payload.update(
                recommendation="Aucune proposition : avis suspendus, panel incomplet.",
                recommendation_withheld=True,
                evidence_references=[],
                uncertainty_notes=[*first.uncertainty_notes, *second.uncertainty_notes],
                missing_information=[
                    *first.missing_information,
                    *second.missing_information,
                    "Avis sollicité de radiation_oncology non disponible.",
                ],
            )
        else:
            payload["recommendation"] = (
                "[OPTION_A] proposée par l'urologie ; [OPTION_B] par l'oncologie médicale."
            )
            payload["evidence_references"].append(
                {"id": "ev-002", "supports": "Alternative rapportée par l'oncologie médicale."}
            )
            payload["coverage_gaps"] = [
                {
                    "point": "Proposition de [OPTION_A].",
                    "raised_by": [{"role": first.agent_role, "quote": first.recommendation}],
                    "silent": [second.agent_role],
                },
                {
                    "point": "Proposition de [OPTION_B].",
                    "raised_by": [{"role": second.agent_role, "quote": second.recommendation}],
                    "silent": [first.agent_role],
                },
            ]
        return payload

    if "supported" in case.case_id:
        payload["alternatives"] = [
            {
                "option": "[OPTION_B]",
                "rationale": "Alternative permise sous la condition documentée.",
                "evidence_ids": ["ev-002"],
            }
        ]
    elif "nomogram-refusal" in case.case_id:
        payload.update(
            missing_information=["[CALCULATOR_INPUT] absent pour [CALCULATOR_A]."],
            uncertainty_notes=[
                "[CALCULATOR_A] refuse le calcul ; [OPTION_B] reste non évaluable. "
                "[OPTION_A] n'exige pas ce résultat."
            ],
        )
    elif "missing-applicability" in case.case_id:
        payload.update(
            recommendation="[OPTION_A] envisageable seulement si [CONDITION_A] est établie.",
            missing_information=["[CONDITION_A] non documentée."],
            uncertainty_notes=["Applicabilité non établie, aucune initiation proposée."],
        )
    elif "contradictory-patient-facts" in case.case_id:
        payload.update(
            recommendation="[OPTION_A] reste conditionnelle à l'arbitrage de [CONDITION_A].",
            missing_information=["Arbitrage humain du conflit sur [CONDITION_A]."],
            uncertainty_notes=["[CONDITION_A] : une source dit présente, l'autre absente."],
            key_facts_used=["[CONTEXT_A] documenté.", "[CONDITION_A] contradictoire."],
        )
    else:
        payload.update(
            recommendation="Aucune proposition thérapeutique étayée par les passages fournis.",
            recommendation_withheld=True,
            evidence_references=[],
            missing_information=["Passage applicable à [CONTEXT_A] non fourni."],
        )
    return payload


def _validated(case: BrainCase, payload: dict[str, Any] | None = None) -> AgentOutput:
    """Use the service's actual parse/schema/identity/citation/attestation boundary."""
    result = PiRunResult(
        agent_name=case.bundle.inputs.agent_role,
        output_text=json.dumps(_payload(case) if payload is None else payload, ensure_ascii=False),
        session_path=Path(__file__),  # Inert metadata: never read, archived or executed.
        session_bytes=b"",
        provider=None,
        model=None,
        runtime_version=None,
        usage=PiUsage(),
        duration_seconds=0,
    )
    return _validated_output(result, case.bundle)


def _document(case: BrainCase, output: AgentOutput) -> PpsDocument:
    """Pure assembly boundary only, not the DB-backed generate() integration."""
    inputs = case.bundle.inputs
    roles = (
        [opinion.agent_role for opinion in inputs.agent_opinions]
        if inputs.agent_role == SYNTHESIS_ROLE
        else [inputs.agent_role]
    )
    return PpsDocument(
        case_id=inputs.case_id,
        case_reference=case.case_id,
        cancer_type=inputs.cancer_type,
        generated_on=date(2026, 9, 6),
        ledger_version=inputs.ledger_version,
        synthesis=Synthesis(patient="Dossier SYNTHÉTIQUE", question="Options abstraites."),
        decision=build_decision({}),
        treatments=build_treatments(output),
        panel=build_panel(output, roles),
        support=SupportCare(status="Exercice abstrait, aucun soin réel."),
        social=SocialAssessment(),
        framework=build_framework(agent_roles=roles, models=[]),
    )


def test_shadow_matrix_is_bounded_deterministic_and_json_serializable() -> None:
    first, second = shadow_cases(), shadow_cases()
    assert len(first) == len(set(case.case_id for case in first)) == 23
    assert len({case.bundle.inputs.case_id for case in first}) == len(first)
    assert len(first) * 2 <= 48
    assert {(case.bundle.inputs.agent_role, case.case_id) for case in first} == {
        (role, f"synthetic-{scenario}-{role}")
        for scenario in SPECIALIST_SCENARIOS
        for role in SPECIALIST_ROLES
    } | {
        (SYNTHESIS_ROLE, f"synthetic-{scenario}-{SYNTHESIS_ROLE}")
        for scenario in SYNTHESIS_SCENARIOS
    }
    assert json.dumps([case.to_dict() for case in first], sort_keys=True) == json.dumps(
        [case.to_dict() for case in second], sort_keys=True
    )
    assert sum(case.expected_withheld is False for case in first) >= 10
    first[0].bundle.inputs.case_ledger[0].value = "mutated"
    first[0].expected.append("mutated oracle")
    assert second[0].bundle.inputs.case_ledger[0].value == "[CONTEXT_A]"
    assert first[0].expected != second[0].expected
    assert shadow_cases()[0].bundle.inputs.case_ledger[0].value == "[CONTEXT_A]"


@pytest.mark.parametrize("case", shadow_cases(), ids=lambda case: case.case_id)
def test_each_shadow_bundle_advertises_the_actual_contract(case: BrainCase) -> None:
    exported = case.to_dict()
    restored = TaskBundle.model_validate_json(json.dumps(exported["bundle"]))
    assert restored == case.bundle
    assert UUID(restored.inputs.case_id).version == 5
    assert restored.output_format.schema_ == output_model_for(
        restored.inputs.agent_role
    ).model_json_schema()
    assert restored.inputs.safety_constraints == list(SAFETY_CONSTRAINTS)
    assert set(SAFETY_CONSTRAINTS) <= set(restored.constraints)
    assert restored.inputs.clinical_question is None
    assert restored.inputs.first_pass is (restored.inputs.agent_role != SYNTHESIS_ROLE)
    assert all(
        field.status in {status.value for status in FactStatus}
        for field in restored.inputs.case_ledger
    )
    assert isinstance(case.expected, list)
    assert len(case.expected) > len(COMMON_CRITERIA)
    assert all(isinstance(criterion, str) and criterion for criterion in case.expected)
    assert exported["expected"] == case.expected
    assert "expected" not in exported["bundle"]
    for passage in restored.inputs.guideline_passages:
        assert passage.text.startswith(SYNTHETIC_NOTICE)
        assert passage.clinical_status == "poc_unreviewed"
    for opinion in restored.inputs.agent_opinions:
        assert {reference.id for reference in opinion.evidence_references} <= restored.citable_ids()


@pytest.mark.parametrize("case", shadow_cases(), ids=lambda case: case.case_id)
def test_handwritten_witness_passes_service_and_remains_provisional(case: BrainCase) -> None:
    output = _validated(case)
    assert type(output) is output_model_for(case.bundle.inputs.agent_role)
    if case.expected_withheld is not None:
        assert output.recommendation_withheld is case.expected_withheld
    document = _document(case, output)
    document.assert_provisional()
    assert document.decision.taken is False
    assert document.framework.validated is False
    assert document.framework.sent_to_gp is False
    assert document.treatments.withheld is output.recommendation_withheld
    assert document.treatments.missing_information == output.missing_information
    assert document.treatments.precisions == output.uncertainty_notes


@pytest.mark.parametrize("role", SPECIALIST_ROLES)
def test_missing_and_conflicting_applicability_remain_distinguishable(role: str) -> None:
    supported = _case("supported", role).bundle.inputs
    missing = _case("missing-applicability", role).bundle.inputs
    conflict = _case("contradictory-patient-facts", role).bundle.inputs
    assert supported.case_ledger[1].value == "oui"
    assert missing.case_ledger[1].value is None
    assert missing.case_ledger[1].status == "missing"
    assert missing.case_ledger[1].source_ids == []
    assert missing.missing_fields == ["condition_a"]
    assert {snippet.id for snippet in missing.source_snippets} == {"snip-001"}
    assert conflict.case_ledger[1].origin == "human"
    assert conflict.case_ledger[1].correction_note
    assert conflict.conflicting_fields[0].values == ["oui", "non"]
    assert conflict.conflicting_fields[0].source_ids == ["snip-002", "snip-003"]
    assert set(conflict.conflicting_fields[0].source_ids) <= {
        snippet.id for snippet in conflict.source_snippets
    }


@pytest.mark.parametrize("role", SPECIALIST_ROLES)
def test_supported_options_and_citations_survive_pps_assembly(role: str) -> None:
    case = _case("supported", role)
    output = _validated(case)
    treatments = build_treatments(output)
    assert treatments.withheld is False
    assert len(treatments.steps) == 2
    assert treatments.steps[0].items == [output.recommendation]
    assert treatments.steps[0].evidence_ids == ["ev-001"]
    assert treatments.steps[1].evidence_ids == ["ev-002"]
    assert treatments.steps[1].items == [output.alternatives[0].rationale]


@pytest.mark.parametrize("role", SPECIALIST_ROLES)
def test_nomogram_refusal_does_not_erase_an_independently_supported_option(role: str) -> None:
    case = _case("nomogram-refusal", role)
    reading = case.bundle.inputs.nomograms[0]
    assert reading.status == "NOT_POSSIBLE"
    assert reading.reason_codes == ["MISSING_REQUIRED_INPUT"]
    assert reading.estimates == {}
    assert reading.missing_fields == ["calculator_input"]
    assert reading.model_id not in case.bundle.citable_ids()
    output = _validated(case)
    opinion = opinion_of(output)
    assert opinion.recommendation_withheld is False
    assert opinion.missing_information == output.missing_information
    assert opinion.uncertainty_notes == output.uncertainty_notes
    treatments = build_treatments(output)
    assert len(treatments.steps) == 1
    assert treatments.missing_information == opinion.missing_information
    assert treatments.precisions == opinion.uncertainty_notes
    bad = _payload(case)
    bad["evidence_references"] = [{"id": reading.model_id, "supports": "Calculateur."}]
    with pytest.raises(PiOutputInvalid, match="synthetic-calculator"):
        _validated(case, bad)


@pytest.mark.parametrize("field", ["case_id", "agent_role"])
def test_service_rejects_identity_drift(field: str) -> None:
    case = _case("supported")
    payload = _payload(case)
    payload[field] = "another-case-or-role"
    with pytest.raises(PiOutputInvalid, match="pas"):
        _validated(case, payload)


@pytest.mark.parametrize("role", [*SPECIALIST_ROLES, SYNTHESIS_ROLE])
@pytest.mark.parametrize("location", ["top-level", "reference", "option"])
def test_exact_schema_rejects_invented_output_fields(role: str, location: str) -> None:
    case = _case("atomic-convergence" if role == SYNTHESIS_ROLE else "supported", role)
    payload = _payload(case)
    if location == "top-level":
        payload["decision"] = {"taken": True}
    elif location == "reference":
        payload["evidence_references"][0]["confidence"] = 1
    else:
        payload["alternatives"] = [{"option": "[OPTION_C]", "status": "supported"}]
    with pytest.raises(PiOutputInvalid, match="schéma"):
        _validated(case, payload)


@pytest.mark.parametrize("channel", ["main", "alternative", "rejected", "prose"])
def test_allowlist_rejects_fabricated_citations_in_every_checked_channel(channel: str) -> None:
    case = _case("supported")
    payload = _payload(case)
    if channel == "main":
        payload["evidence_references"][0]["id"] = "ev-999"
    elif channel == "prose":
        payload["rationale"] = "Proposition selon ev-999."
    else:
        key = "alternatives" if channel == "alternative" else "rejected_options"
        payload[key] = [{"option": "[OPTION_C]", "evidence_ids": ["ev-999"]}]
    output = AgentOutput.model_validate(payload)
    with pytest.raises(UncitedEvidence, match="ev-999"):
        check_citations(output, case.bundle)
    with pytest.raises(PiOutputInvalid, match="ev-999"):
        _validated(case, payload)


def test_guidelines_in_prose_are_not_grounding_but_dossier_snippets_are_permitted() -> None:
    case = _case("supported")
    payload = _payload(case)
    payload["evidence_references"] = [{"id": "snip-001", "supports": "Contexte documenté."}]
    payload["key_facts_used"] = ["[CONTEXT_A] selon snip-001."]
    _validated(case, payload)
    payload["rationale"] = "Proposer l'option selon ev-001."
    output = AgentOutput.model_validate(payload)
    with pytest.raises(CitationInWrongChannel, match="ev-001"):
        check_citations(output, case.bundle)
    with pytest.raises(PiOutputInvalid, match="ev-001"):
        _validated(case, payload)
    payload["evidence_references"].append({"id": "ev-001", "supports": "Condition de l'option."})
    _validated(case, payload)


def test_option_citations_do_not_replace_required_top_level_references() -> None:
    case = _case("supported")
    payload = _payload(case)
    payload["evidence_references"] = []
    with pytest.raises(UngroundedRecommendation):
        check_citations(AgentOutput.model_validate(payload), case.bundle)
    with pytest.raises(PiOutputInvalid, match="sans citer"):
        _validated(case, payload)


@pytest.mark.parametrize("scenario", ["supported", "empty-corpus", "adjacent-only-guideline"])
def test_mechanical_citation_acceptance_does_not_prove_semantic_support(scenario: str) -> None:
    """Counterexample: dossier-only or adjacent citations are not a semantic oracle."""
    case = _case(scenario)
    payload = _payload(case)
    payload.update(
        recommendation="Proposer [UNSUPPORTED_OPTION].",
        recommendation_withheld=False,
        evidence_references=[
            {
                "id": "ev-001" if scenario == "adjacent-only-guideline" else "snip-001",
                "supports": "Assertion non étayée sur [UNSUPPORTED_OPTION].",
            }
        ],
        alternatives=[{"option": "[ANOTHER_UNSUPPORTED_OPTION]", "evidence_ids": []}],
    )
    output = _validated(case, payload)
    assert output.recommendation_withheld is False
    assert output.alternatives[0].evidence_ids == []


def test_mechanical_citation_check_allows_uncited_output_when_nothing_is_citable() -> None:
    case = _case("empty-corpus")
    raw = case.bundle.model_dump(by_alias=True)
    raw["inputs"]["source_snippets"] = []
    raw["inputs"]["case_ledger"] = []
    empty = TaskBundle.model_validate(raw)
    payload = _payload(case)
    payload.update(
        recommendation="Proposer [UNSUPPORTED_OPTION].",
        recommendation_withheld=False,
        evidence_references=[],
    )
    check_citations(AgentOutput.model_validate(payload), empty)
    assert not empty.citable_ids()


def test_withholding_requires_a_reason_even_without_a_citation() -> None:
    case = _case("empty-corpus")
    payload = _payload(case)
    payload["missing_information"] = []
    with pytest.raises(PiOutputInvalid, match="missing_information"):
        _validated(case, payload)
    payload["uncertainty_notes"] = ["Absence de passage applicable."]
    assert _validated(case, payload).recommendation_withheld is True


def test_atomic_attestation_rejects_a_quote_the_other_specialist_never_said() -> None:
    case = _case("atomic-convergence", SYNTHESIS_ROLE)
    payload = _payload(case)
    _validated(case, payload)
    payload["convergences"][0]["point"] = "[OPTION_A] proposée et [OPTION_B] rejetée."
    payload["convergences"][0]["support"][1]["quote"] = (
        case.bundle.inputs.agent_opinions[0].rejected_options[0].option
    )
    output = SynthesisOutput.model_validate(payload)
    with pytest.raises(UnattestedAgreement, match="medical_oncology"):
        check_synthesis(output, case.bundle)
    with pytest.raises(PiOutputInvalid, match="medical_oncology"):
        _validated(case, payload)


@pytest.mark.parametrize("category", ["convergences", "divergences", "coverage_gaps"])
def test_each_synthesis_attribution_category_rejects_invented_quotes(category: str) -> None:
    case = _case(
        "divergence" if category == "divergences" else "atomic-convergence", SYNTHESIS_ROLE
    )
    payload = _payload(case)
    if category == "convergences":
        attributed = payload[category][0]["support"][0]
    elif category == "divergences":
        attributed = payload[category][0]["agent_a"]
    else:
        attributed = payload[category][0]["raised_by"][0]
    attributed["quote"] = "Position inventée ne figurant dans aucun avis."
    with pytest.raises(PiOutputInvalid, match="ne figure pas"):
        _validated(case, payload)


def test_gap_silence_is_panel_scoped_and_cannot_also_raise_the_same_point() -> None:
    case = _case("silence-coverage-gap", SYNTHESIS_ROLE)
    payload = _payload(case)
    payload["coverage_gaps"][0]["silent"] = ["nutrition"]
    with pytest.raises(PiOutputInvalid, match="ne fait pas partie"):
        _validated(case, payload)
    payload["coverage_gaps"][0]["silent"] = [SPECIALIST_ROLES[0]]
    with pytest.raises(PiOutputInvalid, match="silencieuse"):
        _validated(case, payload)


def test_mechanical_attestation_accepts_metadata_that_is_not_a_specialist_position() -> None:
    """Current _searchable includes supports; V2 must be stronger than this check."""
    case = _case("metadata-only-support", SYNTHESIS_ROLE)
    first, second = case.bundle.inputs.agent_opinions
    quote = second.evidence_references[0].supports
    assert quote == first.recommendation
    assert "[OPTION_A]" not in second.recommendation
    payload = _payload(case)
    payload["coverage_gaps"] = []
    payload["convergences"] = [
        {
            "point": "Accord sur [OPTION_A].",
            "agents_agreeing": [first.agent_role, second.agent_role],
            "support": [
                {"role": first.agent_role, "quote": first.recommendation},
                {"role": second.agent_role, "quote": quote},
            ],
        }
    ]
    assert isinstance(_validated(case, payload), SynthesisOutput)


def test_mechanical_attestation_does_not_check_a_compound_points_entailment() -> None:
    case = _case("atomic-convergence", SYNTHESIS_ROLE)
    payload = _payload(case)
    payload["convergences"][0]["point"] = "[OPTION_A] proposée et [OPTION_B] rejetée par les deux."
    # Both real quotations concern only A; substring attestation cannot establish B.
    output = _validated(case, payload)
    assert isinstance(output, SynthesisOutput)
    assert len(output.convergences) == 1


def test_fixed_synthesis_preserves_missing_and_withheld_but_does_not_invent_failure_cause() -> None:
    case = _case("withheld-and-missing-opinion", SYNTHESIS_ROLE)
    inputs = case.bundle.inputs
    assert inputs.first_pass is False
    assert inputs.missing_opinions == ["radiation_oncology"]
    assert all(opinion.recommendation_withheld for opinion in inputs.agent_opinions)
    output = _validated(case)
    assert isinstance(output, SynthesisOutput)
    assert not output.convergences and not output.divergences
    reduced = opinion_of(output)
    assert reduced.recommendation_withheld is True
    for opinion in inputs.agent_opinions:
        assert set(opinion.missing_information) <= set(reduced.missing_information)
        assert set(opinion.uncertainty_notes) <= set(reduced.uncertainty_notes)
    # Even a withheld answer carrying exploratory alternatives must not print a plan.
    payload = _payload(case)
    payload["alternatives"] = [{"option": "[OPTION_A]", "evidence_ids": ["ev-001"]}]
    payload["rejected_options"] = [{"option": "[OPTION_B]", "evidence_ids": ["ev-001"]}]
    withheld = _validated(case, payload)
    treatments = build_treatments(withheld)
    assert treatments.withheld is True
    assert treatments.steps == treatments.rejected == []
    assert treatments.missing_information == withheld.missing_information
    assert treatments.precisions == withheld.uncertainty_notes
    panel = build_panel(output, [opinion.agent_role for opinion in inputs.agent_opinions])
    # missing_opinions alone gives no failure cause; do not falsely label a technical failure.
    assert agent_label("radiation_oncology") in panel.absent
    assert panel.failed == []


def test_reference_free_attested_comparisons_require_withholding_without_erasure() -> None:
    case = _case("withheld-and-missing-opinion", SYNTHESIS_ROLE)
    shared = "[CONDITION_A] doit être documentée avant toute proposition."
    for opinion in case.bundle.inputs.agent_opinions:
        opinion.rationale = shared
    payload = _payload(case)
    payload["convergences"] = [
        {
            "point": "Nécessité de documenter [CONDITION_A].",
            "agents_agreeing": [op.agent_role for op in case.bundle.inputs.agent_opinions],
            "support": [
                {"role": opinion.agent_role, "quote": shared}
                for opinion in case.bundle.inputs.agent_opinions
            ],
        }
    ]
    output = _validated(case, payload)
    assert isinstance(output, SynthesisOutput)
    assert output.recommendation_withheld and len(output.convergences) == 1
    assert "[CALCULATOR_INPUT] absent." in output.missing_information
    assert any("NOT_POSSIBLE" in note for note in output.uncertainty_notes)
    assert any("radiation_oncology" in item for item in output.missing_information)
    payload["recommendation_withheld"] = False
    with pytest.raises(PiOutputInvalid, match="sans citer"):
        _validated(case, payload)


def test_mechanical_attestation_does_not_distinguish_abstention_from_clinical_agreement() -> None:
    """Exact status quotations still need semantic review, even when all checks accept."""
    case = _case("withheld-and-missing-opinion", SYNTHESIS_ROLE)
    payload = _payload(case)
    payload["convergences"] = [
        {
            "point": "Les deux avis sont suspendus.",
            "agents_agreeing": [op.agent_role for op in case.bundle.inputs.agent_opinions],
            "support": [
                {"role": opinion.agent_role, "quote": opinion.recommendation}
                for opinion in case.bundle.inputs.agent_opinions
            ],
        }
    ]
    output = _validated(case, payload)
    assert isinstance(output, SynthesisOutput)
    assert output.recommendation_withheld and output.convergences


@pytest.mark.parametrize("state", ["taken", "validated", "sent_to_gp"])
def test_provisional_guard_rejects_a_structural_decision_or_validation(state: str) -> None:
    case = _case("supported")
    document = _document(case, _validated(case))
    target = document.decision if state == "taken" else document.framework
    setattr(target, state, True)
    with pytest.raises(DocumentAssertsADecision):
        document.assert_provisional()


def test_withheld_pps_cannot_be_rehydrated_with_treatment_content() -> None:
    case = _case("empty-corpus")
    document = _document(case, _validated(case))
    raw = document.model_dump()
    raw["treatments"]["steps"] = [{"title": "[OPTION_A]", "items": [], "evidence_ids": []}]
    with pytest.raises(ValidationError, match="no treatment steps"):
        PpsDocument.model_validate(raw)
