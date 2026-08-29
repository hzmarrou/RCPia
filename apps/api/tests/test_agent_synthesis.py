"""The synthesis agent reads the specialists, and only the synthesis agent may.

Two properties are being protected here, and they pull in opposite directions. Every first pass
must be independent — a panel whose members read each other is one opinion wearing several
names. The synthesis must not be independent — it exists to compare. The tests below check that
the boundary falls in exactly one place.
"""

import pytest
from pydantic import ValidationError

from rcpia.agents.opinions import opinion_of
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOpinion,
    AgentOutput,
    BundleInputs,
    Convergence,
    Divergence,
    EvidenceReference,
    SynthesisOutput,
    agent_output_json_schema,
    output_model_for,
)


def an_output(role: str = "urology_oncology", **overrides: object) -> dict:
    payload: dict = {
        "agent_role": role,
        "case_id": "case-1",
        "recommendation": "Cystectomie radicale après évaluation.",
        "rationale": "Motif.",
        "evidence_references": [
            {"id": "afu.bladder-tvim.2024-2026.standard.01.0107", "supports": "Le standard."}
        ],
    }
    payload.update(overrides)
    return payload


# --- Which schema each role answers under -----------------------------------------------------


def test_only_the_synthesis_role_gets_the_synthesis_schema() -> None:
    assert output_model_for(SYNTHESIS_ROLE) is SynthesisOutput
    for role in ("urology_oncology", "medical_oncology", "radiology", ""):
        assert output_model_for(role) is AgentOutput


def test_the_bundle_advertises_the_schema_the_answer_is_checked_against() -> None:
    """The bundle's schema and the validator must be the same choice, or a synthesis is asked
    for fields it is then rejected for returning."""
    synthesis = agent_output_json_schema(SYNTHESIS_ROLE)["properties"]
    specialist = agent_output_json_schema("urology_oncology")["properties"]

    assert {"convergences", "divergences", "supportive_care_plan"} <= set(synthesis)
    assert not {"convergences", "divergences", "supportive_care_plan"} & set(specialist)


def test_a_specialist_may_not_return_synthesis_fields() -> None:
    """The schema is the enforcement, not the brain. A urologist reporting a convergence has
    read another agent's opinion, and that is the property the panel depends on."""
    with pytest.raises(ValidationError):
        AgentOutput.model_validate(an_output(convergences=[]))


# --- What a synthesis must say ----------------------------------------------------------------


def test_a_synthesis_reporting_neither_agreement_nor_disagreement_is_rejected() -> None:
    """A single plan with no visible provenance is what the synthesis exists to prevent."""
    with pytest.raises(ValidationError, match="convergence ou une divergence"):
        SynthesisOutput.model_validate(an_output(SYNTHESIS_ROLE))


def test_a_synthesis_that_withholds_a_recommendation_need_not_have_found_structure() -> None:
    """An agent that cannot conclude is not required to have compared first."""
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            recommendation_withheld=True,
            missing_information=["Aucun avis de spécialité exploitable."],
        )
    )
    assert output.convergences == []


def test_one_agent_cannot_constitute_a_convergence() -> None:
    """Recording a lone opinion as agreement would manufacture consensus from one voice."""
    with pytest.raises(ValidationError):
        Convergence(point="Cystectomie indiquée", agents_agreeing=["urology_oncology"])


def test_a_divergence_has_no_field_for_the_right_answer() -> None:
    """RCPia exposes disagreement and does not arbitrate it. If a `preferred` field ever
    appears here, that decision was made by accident."""
    assert "preferred" not in Divergence.model_fields
    assert "resolution" not in Divergence.model_fields
    assert set(Divergence.model_fields) == {
        "point",
        "agent_a",
        "agent_b",
        "clinical_impact",
        "resolution_required",
    }


def test_a_synthesis_reporting_a_divergence_validates() -> None:
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            divergences=[
                {
                    "point": "Carboplatine en néoadjuvant",
                    "agent_a": {"role": "urology_oncology", "position": "non évoqué"},
                    "agent_b": {
                        "role": "medical_oncology",
                        "position": "exclu, aucun bénéfice de survie démontré",
                    },
                    "clinical_impact": "Change le traitement néoadjuvant proposé.",
                    "resolution_required": True,
                }
            ],
        )
    )
    assert output.divergences[0].agent_b.role == "medical_oncology"


# --- What the bundle carries ------------------------------------------------------------------


def test_a_first_pass_bundle_carries_no_opinions_by_default() -> None:
    """The independence property, stated as a default rather than trusted to callers."""
    inputs = BundleInputs(
        case_id="case-1",
        agent_role="urology_oncology",
        cancer_type="bladder",
        ledger_version=1,
    )
    assert inputs.agent_opinions == []
    assert inputs.first_pass is True


def test_an_opinion_keeps_the_role_and_the_grounds_it_will_be_quoted_on() -> None:
    """A divergence must name who held which position, so the role travels with the opinion,
    and the citations travel with it so the synthesis quotes grounds rather than paraphrase."""
    output = AgentOutput.model_validate(
        an_output(
            "medical_oncology",
            contraindications=["Inéligibilité au cisplatine : DFG 30 mL/min."],
        )
    )
    opinion = opinion_of(output)

    assert opinion.agent_role == "medical_oncology"
    assert opinion.contraindications == ["Inéligibilité au cisplatine : DFG 30 mL/min."]
    assert opinion.evidence_references[0] == EvidenceReference(
        id="afu.bladder-tvim.2024-2026.standard.01.0107", supports="Le standard."
    )


def test_a_withheld_opinion_survives_the_reduction() -> None:
    """An abstention is neither agreement nor disagreement, so the synthesis has to be able to
    see that it was an abstention."""
    output = AgentOutput.model_validate(
        an_output(
            "radiology",
            recommendation_withheld=True,
            uncertainty_notes=["Imagerie non relue."],
        )
    )
    assert opinion_of(output).recommendation_withheld is True


def test_carrying_opinions_ends_independence_without_an_operator_question() -> None:
    """`first_pass` is derived from what the bundle holds, not from the role that asked.

    Building the synthesis bundle first failed against this validator, because it derived
    independence from `clinical_question` alone. That was the check working: the premise that
    an operator question was the only way to lose independence had stopped being true.
    """
    inputs = BundleInputs(
        case_id="case-1",
        agent_role=SYNTHESIS_ROLE,
        cancer_type="bladder",
        ledger_version=1,
        agent_opinions=[
            AgentOpinion(agent_role="urology_oncology", recommendation="Cystectomie.")
        ],
    )
    assert inputs.first_pass is False
    assert inputs.clinical_question is None


def test_a_caller_may_not_declare_itself_independent_while_holding_opinions() -> None:
    """Silently correcting a safety flag would hide a caller that misunderstood it."""
    with pytest.raises(ValidationError, match="first_pass"):
        BundleInputs(
            case_id="case-1",
            agent_role=SYNTHESIS_ROLE,
            cancer_type="bladder",
            ledger_version=1,
            first_pass=True,
            agent_opinions=[
                AgentOpinion(agent_role="urology_oncology", recommendation="Cystectomie.")
            ],
        )
