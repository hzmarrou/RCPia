import io
import json
from pathlib import Path

import pytest
from docx import Document
from pydantic import ValidationError

from rcpia.agents.pi_adapter import PiOutputInvalid, PiRunResult, PiUsage
from rcpia.agents.pps_fiche import ConciseFiche
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOpinion,
    ConciseSynthesisOutput,
    DecisionLedSynthesisOutput,
    agent_output_json_schema,
    output_model_for,
)
from rcpia.agents.service import _validated_output
from rcpia.pps.docx import render_fiche_docx, render_probatoire_docx, to_bytes
from rcpia.pps.proposal import apply_proposal
from rcpia.pps.render import render_markdown
from tests.test_pi_adapter import bundle
from tests.test_pps_docx import _minimal, _text
from tests.test_pps_proposal import synthesis_payload


def fiche_payload() -> dict:
    return {
        "decision": "Stratégie A si le bilan le permet ; sinon B.",
        "oncogenetics": "Orientation selon les données familiales.",
        "oncogeriatrics": "Non retenue dans cette proposition.",
        "fertility": "Consulter si un projet parental est exprimé.",
        "steps": [{
            "title": "Bilan préalable", "detail": "Clarifier l'éligibilité avant traitement.",
        }],
        "alternatives": [{"rank": 2, "detail": "Stratégie B si A est contre-indiquée."}],
        "precisions": "La condition A reste à vérifier ; aucune éligibilité ne doit être supposée.",
        "palliative_care": "Non retenue en première intention.",
        "treatment_status": "Après bilan.",
        "support": [{"title": "Accompagnement", "detail": "Évaluer les besoins du patient."}],
        "carers": "Équipe soignante.", "location": "Consultation.",
        "support_status": "Dès maintenant.",
        "social": "Situation à clarifier.", "family": "Identifier les aidants disponibles.",
        "discharge": "Retour selon autonomie.", "comment": "Organiser les aides nécessaires.",
        "references": "Référentiel fourni.", "clinical_trial": "Non retenu à ce stade.",
        "review": "Après réception du bilan.", "covered_option_ranks": [1, 2],
    }


def document_with_fiche():
    output = ConciseSynthesisOutput.model_validate({
        **synthesis_payload(), "pps_fiche": fiche_payload(),
    })
    return apply_proposal(_minimal(), output)


def test_current_schema_requires_concise_fiche_without_upgrading_archives() -> None:
    schema = agent_output_json_schema(SYNTHESIS_ROLE, decision_led=True, concise=True)
    assert {"pps_proposal", "pps_fiche"} <= set(schema["required"])
    assert output_model_for(SYNTHESIS_ROLE, decision_led=True) is DecisionLedSynthesisOutput
    assert output_model_for(SYNTHESIS_ROLE, concise=True) is ConciseSynthesisOutput
    with pytest.raises(ValidationError):
        ConciseSynthesisOutput.model_validate(synthesis_payload())
    assert DecisionLedSynthesisOutput.model_validate(synthesis_payload())


@pytest.mark.parametrize("text", [
    "Condition : aptitude.", "Délai : après bilan.", "Intervenants : urologue.",
    "Informations requises : bilan.", "à déterminer",
])
def test_technical_subheadings_cannot_return_as_normal_fiche_prose(text: str) -> None:
    with pytest.raises(ValidationError):
        ConciseFiche.model_validate({**fiche_payload(), "precisions": text})


def test_length_limits_refuse_instead_of_truncating_clinical_text() -> None:
    value = fiche_payload()
    value["decision"] = "mot " * 70 + "mot."
    with pytest.raises(ValidationError, match="70 mots"):
        ConciseFiche.model_validate(value)
    assert value["decision"] == "mot " * 70 + "mot."
    with pytest.raises(ValidationError):
        ConciseFiche.model_validate({**fiche_payload(), "oncogenetics": "x" * 241})
    for value in ("Texte conditionnel\u00ad", "Phrase interrompue symptom “ “"):
        with pytest.raises(ValidationError, match="coupée"):
            ConciseFiche.model_validate({**fiche_payload(), "precisions": value})


def test_concise_options_cannot_disappear_or_change_rank() -> None:
    value = fiche_payload()
    value["alternatives"] = []
    with pytest.raises(ValidationError, match="alternatives"):
        ConciseSynthesisOutput.model_validate({**synthesis_payload(), "pps_fiche": value})
    value = fiche_payload()
    value["oncogenetics"] = "Avis non, malgré la case Oui du plan."
    with pytest.raises(ValidationError, match="contredit"):
        ConciseSynthesisOutput.model_validate({**synthesis_payload(), "pps_fiche": value})
    value = fiche_payload()
    value["covered_option_ranks"] = [2, 1]
    with pytest.raises(ValidationError, match="options"):
        ConciseSynthesisOutput.model_validate({**synthesis_payload(), "pps_fiche": value})


def test_normal_service_validates_the_concise_output_contract() -> None:
    task = bundle()
    task.inputs.agent_role = SYNTHESIS_ROLE
    task.inputs.agent_opinions = [
        AgentOpinion(agent_role=role, recommendation="Une position documentée.")
        for role in ("urology_oncology", "medical_oncology")
    ]
    task.output_format.schema_ = agent_output_json_schema(
        SYNTHESIS_ROLE, decision_led=True, concise=True,
    )
    payload = {**synthesis_payload(), "pps_fiche": fiche_payload(),
               "case_id": task.inputs.case_id}

    def result():
        return PiRunResult(
            agent_name=SYNTHESIS_ROLE, output_text=json.dumps(payload),
            session_path=Path(__file__), session_bytes=b"", provider=None, model=None,
            runtime_version=None, usage=PiUsage(), duration_seconds=0,
        )

    assert isinstance(_validated_output(result(), task), ConciseSynthesisOutput)
    del payload["pps_fiche"]
    with pytest.raises(PiOutputInvalid, match="schéma"):
        _validated_output(result(), task)


def test_normal_exports_are_concise_and_details_remain_in_probatory_document() -> None:
    document = document_with_fiche()
    original = document.model_dump(mode="json")
    fiche = Document(io.BytesIO(to_bytes(render_fiche_docx(document))))
    markdown = render_markdown(document)
    for text in (_text(fiche), markdown):
        assert "Stratégie A si le bilan le permet ; sinon B." in text
        assert "La condition A reste à vérifier" in text
        assert "Stratégie B si A est contre-indiquée" in text
        assert "[X] Oui" in text and "[X] Non" in text
        for forbidden in (
            "Condition :", "Délai :", "Intervenants :", "INFORMATIONS REQUISES",
            "Justification", "Version des données", "Avis analysés", "REGISTRE DES SOURCES",
        ):
            assert forbidden not in text
    assert fiche.paragraphs[-1].text == "Prochaine réévaluation : Après réception du bilan."
    assert markdown.rstrip().endswith("Prochaine réévaluation : Après réception du bilan.")
    probatory = _text(render_probatoire_docx(document))
    assert "PROPOSITION DÉTAILLÉE ET CONDITIONS" in probatory
    assert "Ne pas supposer l'éligibilité acquise." in probatory
    assert "Autre information requise." in probatory
    assert document.model_dump(mode="json") == original
    assert document.proposal.model_dump() == synthesis_payload()["pps_proposal"] | {
        "contract": "rcpia.pps-proposal.v1",
    }
    assert not document.framework.validated and not document.decision.taken
