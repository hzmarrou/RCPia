"""Task bundle and agent output schemas.

Two properties carry clinical weight: an agent cannot be handed another agent's opinion
during the first pass, and it cannot cite evidence it was never given.
"""

import pytest
from pydantic import ValidationError

from rcpia.agents.schemas import (
    MAX_RATIONALE_CHARACTERS,
    AgentOutput,
    BundleInputs,
    ConflictingField,
    EvidenceReference,
    GuidelinePassage,
    LedgerField,
    OutputFormat,
    SourceSnippet,
    TaskBundle,
    TreatmentOption,
    UncitedEvidence,
    agent_output_json_schema,
    check_citations,
)

CASE_ID = "6f1b2c3d-4e5f-4071-8293-a4b5c6d7e8f9"


def bundle(**overrides) -> TaskBundle:
    inputs = BundleInputs(
        case_id=CASE_ID,
        agent_role="urology_oncology",
        cancer_type="bladder",
        clinical_question="Stratégie thérapeutique pour TVIM ?",
        ledger_version=1,
        case_ledger=[
            LedgerField(
                key="muscle_invasion",
                label="Infiltration du muscle",
                value="Oui",
                status="confirmed",
                source_ids=["snip-001"],
            )
        ],
        source_snippets=[
            SourceSnippet(
                id="snip-001",
                text="Infiltration du muscle : Oui",
                source_document_id="doc-1",
                source_chunk_id="chunk-1",
                page=2,
            )
        ],
        **overrides,
    )
    return TaskBundle(
        goal="Produire un avis urologique structuré et provisoire.",
        audience="Agent de synthèse RCP et relecteurs experts humains",
        inputs=inputs,
        output_format=OutputFormat(schema=agent_output_json_schema()),
        constraints=["Citez les preuves par identifiant."],
    )


def output(**overrides) -> AgentOutput:
    values = {
        "agent_role": "urology_oncology",
        "case_id": CASE_ID,
        "recommendation": "Cystectomie totale après optimisation de l'état général.",
        "rationale": "Tumeur infiltrant le muscle, patiente OMS 1.",
        "evidence_references": [
            EvidenceReference(id="snip-001", supports="Infiltration du muscle confirmée")
        ],
    }
    values.update(overrides)
    return AgentOutput(**values)


# --------------------------------------------------------------------------------------
# Independence
# --------------------------------------------------------------------------------------


def test_a_bundle_cannot_carry_another_agents_opinion() -> None:
    """The first pass is independent by construction, not by instruction."""
    with pytest.raises(ValidationError):
        BundleInputs(
            case_id=CASE_ID,
            agent_role="urology_oncology",
            cancer_type="bladder",
            ledger_version=1,
            other_agent_outputs=[{"agent": "medical_oncology", "recommendation": "..."}],
        )


def test_unknown_fields_are_refused_rather_than_ignored() -> None:
    with pytest.raises(ValidationError):
        SourceSnippet(
            id="snip-001",
            text="…",
            source_document_id="doc-1",
            smuggled="anything",
        )


def test_duplicate_citation_ids_are_refused() -> None:
    """Two snippets sharing an id would make a citation ambiguous."""
    with pytest.raises(ValidationError, match="duplicate ids"):
        BundleInputs(
            case_id=CASE_ID,
            agent_role="urology_oncology",
            cancer_type="bladder",
            ledger_version=1,
            source_snippets=[
                SourceSnippet(id="snip-001", text="a", source_document_id="doc-1"),
                SourceSnippet(id="snip-001", text="b", source_document_id="doc-1"),
            ],
        )


# --------------------------------------------------------------------------------------
# The bundle itself
# --------------------------------------------------------------------------------------


def test_a_bundle_serializes_to_the_file_pi_is_given() -> None:
    payload = bundle().model_dump(by_alias=True)

    assert set(payload) == {"goal", "audience", "inputs", "output_format", "constraints"}
    assert payload["output_format"]["type"] == "json"
    assert payload["output_format"]["schema"]["title"] == "AgentOutput"
    assert payload["inputs"]["case_ledger"][0]["key"] == "muscle_invasion"


def test_citable_ids_cover_snippets_and_guidelines() -> None:
    task = bundle(
        guideline_passages=[
            GuidelinePassage(id="ev-001", text="…", source="EAU 2026", section="6.3")
        ]
    )

    assert task.citable_ids() == {"snip-001", "ev-001"}


def test_conflicting_fields_carry_both_values() -> None:
    task = bundle(
        conflicting_fields=[
            ConflictingField(
                field="creatinine", values=["17.33", "173.3"], source_ids=["snip-002"]
            )
        ]
    )

    assert task.inputs.conflicting_fields[0].values == ["17.33", "173.3"]


def test_a_conflict_needs_at_least_two_values() -> None:
    with pytest.raises(ValidationError):
        ConflictingField(field="creatinine", values=["17.33"])


def test_a_bundle_needs_at_least_one_constraint() -> None:
    with pytest.raises(ValidationError):
        TaskBundle(
            goal="…",
            audience="…",
            inputs=bundle().inputs,
            output_format=OutputFormat(schema={}),
            constraints=[],
        )


# --------------------------------------------------------------------------------------
# The output
# --------------------------------------------------------------------------------------


def test_a_valid_output_round_trips() -> None:
    restored = AgentOutput.model_validate_json(output().model_dump_json())

    assert restored.recommendation.startswith("Cystectomie")
    assert restored.recommendation_withheld is False


def test_the_rationale_is_capped_at_one_page() -> None:
    with pytest.raises(ValidationError):
        output(rationale="x" * (MAX_RATIONALE_CHARACTERS + 1))


def test_a_rationale_at_the_limit_is_accepted() -> None:
    assert len(output(rationale="x" * MAX_RATIONALE_CHARACTERS).rationale) == (
        MAX_RATIONALE_CHARACTERS
    )


def test_an_empty_recommendation_is_refused() -> None:
    with pytest.raises(ValidationError):
        output(recommendation="")


def test_withholding_a_recommendation_requires_saying_why() -> None:
    """"I cannot advise" is a legitimate answer — but only with the reason attached."""
    with pytest.raises(ValidationError, match="missing_information"):
        output(recommendation_withheld=True)

    withheld = output(
        recommendation_withheld=True,
        recommendation="Aucune recommandation possible en l'état.",
        missing_information=["Statut d'infiltration du muscle non documenté"],
    )
    assert withheld.recommendation_withheld is True


def test_an_output_carrying_unknown_fields_is_refused() -> None:
    with pytest.raises(ValidationError):
        AgentOutput(
            agent_role="urology_oncology",
            case_id=CASE_ID,
            recommendation="…",
            rationale="…",
            confidence_score=0.97,
        )


# --------------------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------------------


def test_citations_must_point_at_evidence_the_agent_was_given() -> None:
    """A fabricated reference looks like grounding while being none."""
    task = bundle()
    invented = output(
        evidence_references=[EvidenceReference(id="ev-999", supports="Inventé")]
    )

    with pytest.raises(UncitedEvidence, match="ev-999"):
        check_citations(invented, task)


def test_citations_inside_alternatives_are_checked_too() -> None:
    task = bundle()
    sneaky = output(
        alternatives=[
            TreatmentOption(option="Radiochimiothérapie", evidence_ids=["ev-404"])
        ]
    )

    with pytest.raises(UncitedEvidence, match="ev-404"):
        check_citations(sneaky, task)


def test_valid_citations_pass() -> None:
    task = bundle(
        guideline_passages=[GuidelinePassage(id="ev-001", text="…", source="EAU 2026")]
    )
    grounded = output(
        evidence_references=[
            EvidenceReference(id="snip-001", supports="Muscle infiltré"),
            EvidenceReference(id="ev-001", supports="Recommandation EAU"),
        ],
        alternatives=[
            TreatmentOption(option="Radiochimiothérapie", evidence_ids=["ev-001"])
        ],
    )

    check_citations(grounded, task)


def test_an_agent_may_cite_nothing_when_it_was_given_nothing() -> None:
    """Until evidence ingestion exists, guideline_passages is empty; that must not force
    the agent to invent a citation."""
    task = bundle()
    ungrounded = output(
        evidence_references=[],
        uncertainty_notes=["Aucun référentiel fourni : avis fondé sur le dossier seul."],
    )

    check_citations(ungrounded, task)


def test_the_schema_given_to_the_agent_describes_the_output_model() -> None:
    schema = agent_output_json_schema()

    assert schema["title"] == "AgentOutput"
    assert "recommendation" in schema["properties"]
    assert "rationale" in schema["required"]
