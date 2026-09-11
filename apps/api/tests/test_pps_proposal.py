import copy
import io
import json
from pathlib import Path

import pytest
from docx import Document
from pydantic import ValidationError

from rcpia.agents.pi_adapter import PiOutputInvalid, PiRunResult, PiUsage
from rcpia.agents.pps_proposal import PpsProposal
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    DecisionLedSynthesisOutput,
    SynthesisOutput,
    UnattestedAgreement,
    agent_output_json_schema,
    check_synthesis,
)
from rcpia.agents.service import _validated_output
from rcpia.pps.docx import render_fiche_docx, to_bytes
from rcpia.pps.proposal import apply_proposal
from rcpia.pps.render import render_markdown
from rcpia.pps.schemas import AI_ORIGIN_TITLE, DocumentAssertsADecision
from rcpia.pps.sections import SECTION_TITLES
from tests.test_pi_adapter import bundle
from tests.test_pps_docx import _minimal, _text


def proposal_payload() -> dict:
    action = {
        "action": "Réaliser le bilan documenté.",
        "timing": "Avant traitement", "responsible": "Spécialité concernée",
        "conditions": ["Ne pas supposer l'éligibilité acquise."], "evidence_ids": ["snip-001"],
    }
    yes = {
        "answer": "oui", "rationale": "Clarifier l'indication dans cette situation.",
        "conditions": ["Vérifier la donnée manquante."], "evidence_ids": ["snip-001"],
    }
    no = {**yes, "answer": "non", "rationale": "Non retenu dans cette proposition."}
    return {
        "decision": "Retenir la stratégie A si le bilan le permet ; sinon stratégie B.",
        "intent": "bilan_prealable", "rationale": "Motif documenté du choix.",
        "evidence_ids": ["snip-001"],
        "referrals": {"oncogenetics": yes, "oncogeriatrics": no, "fertility": yes},
        "options": [
            {
                "rank": 1, "title": "Stratégie A", "rationale": "Premier choix documenté.",
                "conditions": ["Si le bilan le permet."], "steps": [action],
                "evidence_ids": ["snip-001"],
            },
            {
                "rank": 2, "title": "Stratégie B", "rationale": "Alternative documentée.",
                "conditions": ["Si A est contre-indiquée."], "steps": [action],
                "evidence_ids": ["snip-001"],
            },
        ],
        "palliative_care": no, "supportive_care": [action],
        "support_location": "Consultations hospitalières proposées.",
        "support_timing": "Organiser dès maintenant.",
        "social_plan": "Évaluer les besoins sociaux.",
        "carers_plan": "Identifier les aidants sans présumer de leur disponibilité.",
        "discharge_plan": "Préparer le retour selon le bilan d'autonomie.",
        "clinical_trial": no, "follow_up": action,
        "missing_information": ["Donnée manquante conservée."],
        "uncertainties": ["Limite clinique conservée."],
    }


def synthesis_payload() -> dict:
    return {
        "case_id": "case-1", "agent_role": SYNTHESIS_ROLE,
        "recommendation": proposal_payload()["decision"], "rationale": "Motif documenté.",
        "missing_information": ["Autre information requise."],
        "uncertainty_notes": ["Réserve du spécialiste."],
        "evidence_references": [{"id": "snip-001", "supports": "Les données fournies."}],
        "convergences": [{
            "point": "Une position documentée.",
            "agents_agreeing": ["urology_oncology", "medical_oncology"],
            "support": [
                {"role": role, "quote": "Une position documentée."}
                for role in ["urology_oncology", "medical_oncology"]
            ],
        }],
        "pps_proposal": proposal_payload(),
    }


def proposed_document():
    return apply_proposal(
        _minimal(), DecisionLedSynthesisOutput.model_validate(synthesis_payload())
    )


def test_future_schema_requires_decisions_while_legacy_outputs_still_read() -> None:
    schema = agent_output_json_schema(SYNTHESIS_ROLE, decision_led=True)
    assert "pps_proposal" in schema["required"]
    old = synthesis_payload()
    del old["pps_proposal"]
    assert "pps_proposal" not in SynthesisOutput.model_validate(old).model_dump()
    with pytest.raises(ValidationError):
        DecisionLedSynthesisOutput.model_validate(old)
    old["pps_proposal"] = None
    with pytest.raises(ValidationError):
        DecisionLedSynthesisOutput.model_validate(old)


@pytest.mark.parametrize("rank", [0, 2, 3])
def test_rank_one_is_unique_and_first(rank: int) -> None:
    payload = proposal_payload()
    payload["options"][0]["rank"] = rank
    with pytest.raises(ValidationError):
        PpsProposal.model_validate(payload)


def test_blank_decisions_and_default_undecided_checkboxes_are_rejected() -> None:
    payload = proposal_payload()
    payload["decision"] = " \n "
    with pytest.raises(ValidationError):
        PpsProposal.model_validate(payload)
    payload = proposal_payload()
    payload["referrals"]["oncogenetics"]["answer"] = "a_determiner"
    with pytest.raises(ValidationError):
        PpsProposal.model_validate(payload)


def test_every_nested_citation_must_be_allowed_and_in_the_source_register() -> None:
    output = DecisionLedSynthesisOutput.model_validate(synthesis_payload())
    task = bundle()
    output.pps_proposal.follow_up.evidence_ids = ["snip-999"]
    with pytest.raises(UnattestedAgreement, match="absents"):
        check_synthesis(output, task)
    output.pps_proposal.follow_up.evidence_ids = ["snip-001"]
    output.evidence_references = []
    with pytest.raises(UnattestedAgreement, match="evidence_references"):
        check_synthesis(output, task)


def test_proposed_boxes_do_not_grant_human_approval_or_modify_legacy_rules() -> None:
    document = proposed_document()
    document.assert_provisional()
    assert [r.answer for r in document.decision.referrals] == ["oui", "non", "oui"]
    assert not document.decision.taken and not document.framework.validated
    assert not document.framework.sent_to_gp
    broken = document.model_copy(deep=True)
    broken.decision.referrals[0].answer = "non"
    with pytest.raises(DocumentAssertsADecision):
        broken.assert_provisional()
    broken = document.model_copy(deep=True)
    broken.proposal = None
    with pytest.raises(DocumentAssertsADecision):
        broken.assert_provisional()


def test_word_and_markdown_share_all_decisions_conditions_and_reference_sections() -> None:
    document = proposed_document()
    before = copy.deepcopy(document.model_dump())
    word = Document(io.BytesIO(to_bytes(render_fiche_docx(document))))
    text = _text(word)
    markdown = render_markdown(document)
    for rendered in (text, markdown):
        assert rendered.count(AI_ORIGIN_TITLE) == 1
        for title in SECTION_TITLES:
            assert title in rendered
        for required in (
            "Option retenue", "Alternative", "Stratégie A", "Stratégie B",
            "Avant traitement", "Si A est contre-indiquée.", "Informations requises",
            "Donnée manquante conservée.", "Autre information requise.",
            "Réserve du spécialiste.", "Limite clinique conservée.", "Proches / Aidants",
            "Prise en charge palliative", "Statut soins support",
        ):
            assert required in rendered
        assert "À déterminer" not in rendered
        assert "en attente de validation RCP" not in rendered
    assert document.model_dump() == before
    assert document.banner in text
    assert text.index("1. ☒ Option retenue") < text.index("2. ☐ Alternative")


def test_legacy_exports_do_not_acquire_invented_proposal() -> None:
    document = _minimal()
    assert document.proposal is None
    assert AI_ORIGIN_TITLE not in _text(render_fiche_docx(document))
    assert "À déterminer" in render_markdown(document)


def test_service_requires_the_contract_it_advertised_and_records_invalid_output() -> None:
    task = bundle()
    task.inputs.agent_role = SYNTHESIS_ROLE
    task.output_format.schema_ = agent_output_json_schema(SYNTHESIS_ROLE, decision_led=True)
    payload = synthesis_payload()
    payload["case_id"] = task.inputs.case_id
    del payload["pps_proposal"]
    result = PiRunResult(
        agent_name=SYNTHESIS_ROLE, output_text=json.dumps(payload),
        session_path=Path(__file__), session_bytes=b"", provider=None, model=None,
        runtime_version=None, usage=PiUsage(), duration_seconds=0,
    )
    with pytest.raises(PiOutputInvalid, match="schéma"):
        _validated_output(result, task)
