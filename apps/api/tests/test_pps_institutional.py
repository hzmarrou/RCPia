import io
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document
from pydantic import ValidationError

from rcpia.agents.pi_adapter import PiRunResult, PiUsage
from rcpia.agents.pps_fiche import InstitutionalFiche
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOpinion,
    ConciseSynthesisOutput,
    InstitutionalSynthesisOutput,
    agent_output_json_schema,
    output_model_from_schema,
)
from rcpia.agents.service import _validated_output
from rcpia.core.models import AgentRun, CancerType, Case, CaseLedgerVersion
from rcpia.evaluation.pps_review import CaseInput, Manifest, PlanInput, create_package
from rcpia.pps import generator
from rcpia.pps.docx import render_fiche_docx, to_bytes
from rcpia.pps.proposal import apply_proposal
from rcpia.pps.render import render_markdown
from tests.test_pi_adapter import bundle
from tests.test_pps_docx import _minimal, _text
from tests.test_pps_fiche import fiche_payload
from tests.test_pps_proposal import synthesis_payload


def institutional_payload() -> dict:
    return {
        **fiche_payload(),
        "contract": "rcpia.pps-fiche.v2",
        "diagnosis": "Diagnostic documenté, stade clinique à préciser.",
        "comorbidities": "Antécédents notés RAS dans le dossier.",
        "question": "Quelle stratégie pour ce dossier ?",
        "steps": [
            {"title": "Bilan", "items": [
                "Confirmer la condition.", "Contrôler la fonction rénale.",
            ]},
            {"title": "Traitement si critères réunis", "items": ["Stratégie A sous condition."]},
        ],
        "alternatives": [{
            "rank": 2, "title": "Stratégie B si A impossible",
            "items": ["Recours conditionnel à B."],
        }],
        "support": [{"title": "Nutrition", "items": ["Évaluer les apports."]}],
        "discharge_destination": "domicile",
        "discharge": "Retour après stabilisation si autonomie et soins compatibles.",
        "reference_scope": "non_documente",
        "references": "Source synthétique.",
    }


def institutional_document():
    return apply_proposal(_minimal(), InstitutionalSynthesisOutput.model_validate({
        **synthesis_payload(), "pps_fiche": institutional_payload(),
    }))


def test_recorded_contract_selects_new_and_old_models_without_guessing() -> None:
    current = agent_output_json_schema(SYNTHESIS_ROLE, institutional=True)
    old = agent_output_json_schema(SYNTHESIS_ROLE, concise=True)
    assert output_model_from_schema(SYNTHESIS_ROLE, current) is InstitutionalSynthesisOutput
    assert output_model_from_schema(SYNTHESIS_ROLE, old) is ConciseSynthesisOutput
    current["$defs"]["InstitutionalFiche"]["properties"]["contract"]["const"] = "unknown"
    with pytest.raises(ValueError, match="inconnu"):
        output_model_from_schema(SYNTHESIS_ROLE, current)


def test_normal_service_accepts_the_institutional_shape_it_requested() -> None:
    task = bundle()
    task.inputs.agent_role = SYNTHESIS_ROLE
    task.inputs.agent_opinions = [
        AgentOpinion(agent_role=role, recommendation="Une position documentée.")
        for role in ("urology_oncology", "medical_oncology")
    ]
    task.output_format.schema_ = agent_output_json_schema(SYNTHESIS_ROLE, institutional=True)
    payload = {
        **synthesis_payload(), "case_id": task.inputs.case_id,
        "pps_fiche": institutional_payload(),
    }
    result = PiRunResult(
        agent_name=SYNTHESIS_ROLE, output_text=json.dumps(payload),
        session_path=Path(__file__), session_bytes=b"", provider=None, model=None,
        runtime_version=None, usage=PiUsage(), duration_seconds=0,
    )
    assert isinstance(_validated_output(result, task), InstitutionalSynthesisOutput)


def test_nine_item_presentation_shape_in_normal_word_and_markdown() -> None:
    document = institutional_document()
    word = Document(io.BytesIO(to_bytes(render_fiche_docx(document))))
    markdown = render_markdown(document)
    for text in (_text(word), markdown):
        assert "Diagnostic documenté, stade clinique à préciser." in text
        assert "Antécédents notés RAS dans le dossier." in text
        assert "Quelle stratégie pour ce dossier ?" in text
        assert "1. Bilan" in text and "2. Traitement si critères réunis" in text
        assert "3. Stratégie B si A impossible" in text
        assert not re.search(r"Alternative \d", text)
        assert "[X] Domicile" in text
        assert "Retour après stabilisation si autonomie" in text
        assert "·" not in text
    bullets = [p.text for p in word.paragraphs if p.style.name == "List Bullet"]
    assert "Confirmer la condition." in bullets
    assert "Recours conditionnel à B." in bullets
    assert word.paragraphs[-1].text == "Prochaine réévaluation : Après réception du bilan."
    assert document.proposal.model_dump() == synthesis_payload()["pps_proposal"] | {
        "contract": "rcpia.pps-proposal.v1",
    }


def test_narrative_short_fields_are_enforced_without_truncation() -> None:
    for field, value in (
        ("diagnosis", "Carcinome · pTNM : pT4 · CIS : Non"),
        ("comorbidities", "OMS : 1 · RAS"),
        ("precisions", "x" * 601),
        ("review", "x" * 81),
    ):
        with pytest.raises(ValidationError):
            InstitutionalFiche.model_validate({**institutional_payload(), field: value})


def test_national_marker_is_not_fabricated_without_a_cited_national_source() -> None:
    payload = institutional_payload() | {"reference_scope": "national"}
    with pytest.raises(ValidationError, match="National"):
        InstitutionalSynthesisOutput.model_validate({**synthesis_payload(), "pps_fiche": payload})
    output = synthesis_payload()
    output["pps_proposal"]["evidence_ids"].append("afu.fixture.2026")
    assert InstitutionalSynthesisOutput.model_validate({**output, "pps_fiche": payload})


def test_shared_question_is_audited_and_does_not_change_diagnoses(tmp_path: Path) -> None:
    import hashlib

    document = institutional_document()
    data = to_bytes(render_fiche_docx(document))
    path = tmp_path / "source.docx"
    path.write_bytes(data)
    plans = [
        PlanInput(origin=origin, path=path, sha256=hashlib.sha256(data).hexdigest(), letter=letter)
        for origin, letter in (("real", "A"), ("new_rcpia", "B"), ("old_rcpia", "C"))
    ]
    question = "Question opérateur comportant une hypothèse à discuter."
    manifest = Manifest(cases=[CaseInput(alias="D01", plans=plans, comparison_question=question)])
    package = create_package(manifest, root=tmp_path, output=tmp_path / "review")
    key = json.loads((package / "coordinator" / "identity-key.json").read_text(encoding="utf-8"))
    for row in key:
        assert f"Question posée | {question}" in row["preserved_sections"][0]
        assert "Diagnostic | Diagnostic documenté, stade clinique à préciser." in (
            row["preserved_sections"][0]
        )
        assert any(
            edit["reason"] == "operator_supplied_comparison_question"
            for edit in row["masked_presentation"]
        )
    assert path.read_bytes() == data


@pytest.mark.parametrize("pinned", ["Question propre au nouveau panel ?", None])
async def test_generated_fiche_question_comes_from_recorded_inputs_not_live_case(
    monkeypatch: pytest.MonkeyPatch, pinned: str | None,
) -> None:
    task = bundle()
    case_id = uuid.UUID(task.inputs.case_id)
    case = Case(
        id=case_id, reference="SYNTHETIC-QUESTION", cancer_type=CancerType.bladder,
        clinical_question="Ancienne question du dossier ?",
    )
    version = CaseLedgerVersion(id=uuid.uuid4(), version=1, ledger={"entries": []})
    task.inputs.clinical_question = pinned
    output = InstitutionalSynthesisOutput.model_validate({
        **synthesis_payload(), "case_id": str(case_id), "pps_fiche": institutional_payload(),
    })
    run = AgentRun(
        id=uuid.uuid4(), agent_role=SYNTHESIS_ROLE, ledger_version_id=version.id,
        ledger_version=1, model="synthetic", bundle=task.model_dump(mode="json", by_alias=True),
    )
    monkeypatch.setattr(generator, "latest_runs", AsyncMock(return_value=[(run, output)]))
    monkeypatch.setattr(generator, "failed_roles", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "latest_version", AsyncMock(return_value=version))
    session = SimpleNamespace(get=AsyncMock(return_value=version))
    document = await generator.generate(session, case=case)
    expected = pinned or output.pps_fiche.question
    assert document.synthesis.question == expected
    assert "Ancienne question du dossier" not in render_markdown(document)
    assert expected in render_markdown(document)
    assert expected in _text(render_fiche_docx(document))
