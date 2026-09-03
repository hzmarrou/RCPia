"""Three defects a reviewer found in the citation path, pinned so they cannot return.

Each one had the same shape: two pieces of code that were each defensible alone and
disagreed with one another, so the disagreement produced output that *looked* grounded.
That is the failure mode this product can least afford, and none of the three was visible
from either side on its own.
"""

import inspect

import pytest

from rcpia.agents import schemas as agent_schemas
from rcpia.agents.schemas import (
    AgentOutput,
    BundleInputs,
    CitationInWrongChannel,
    GuidelinePassage,
    OutputFormat,
    TaskBundle,
    UncitedEvidence,
    UngroundedRecommendation,
    agent_output_json_schema,
    check_citations,
)
from rcpia.agents.service import CITATION_FAILURES
from rcpia.core.config import Settings
from rcpia.pps.generator import build_evidence


class _Run:
    """The two attributes `build_evidence` reads. A real `AgentRun` needs a database."""

    def __init__(self, bundle: dict) -> None:
        self.bundle = bundle


PASSAGE_ID = "eau.bladder-mibc.2026.6-8-1-b-radiation-therapy.01.1198"


CASE_ID = "00000000-0000-0000-0000-000000000001"
PASSAGE_TEXT = "Trimodality therapy combines maximal resection with chemoradiation."


def _task_bundle() -> TaskBundle:
    """A real bundle, so the acceptance side of the disagreement is genuinely exercised."""
    return TaskBundle(
        goal="Produire un avis structuré et provisoire.",
        audience="Agent de synthèse RCP",
        inputs=BundleInputs(
            case_id=CASE_ID,
            agent_role="radiation_oncology",
            cancer_type="bladder",
            ledger_version=1,
            source_snippets=[],
            guideline_passages=[
                GuidelinePassage(
                    id=PASSAGE_ID,
                    text=PASSAGE_TEXT,
                    source="EAU Guidelines on Muscle-invasive Bladder Cancer",
                    document_id="eau-mibc-2026",
                    clinical_status="poc_unreviewed",
                )
            ],
        ),
        output_format=OutputFormat(schema=agent_output_json_schema()),
        constraints=["Citez les preuves par identifiant."],
    )


def _output(cited_id: str) -> AgentOutput:
    return AgentOutput.model_validate(
        {
            "case_id": CASE_ID,
            "agent_role": "radiation_oncology",
            "recommendation": "Radiochimiothérapie de conservation vésicale.",
            "rationale": "Option curative reconnue pour une tumeur infiltrante sélectionnée.",
            "recommendation_withheld": False,
            "evidence_references": [
                {"id": cited_id, "supports": "La trimodalité est une option curative."}
            ],
            "alternatives": [],
            "rejected_options": [],
            "contraindications": [],
            "missing_information": [],
            "key_facts_used": [],
        }
    )


def test_a_miscased_citation_still_reaches_the_evidence_register() -> None:
    """The defect: accepted as grounded, then dropped from the register.

    `check_citations` compares ids case-insensitively on purpose -- a miscased id is a
    typo, not a fabrication -- while `build_evidence` resolved them case-sensitively. An
    agent returning the id in capitals was therefore told its recommendation was grounded,
    and the passage behind it silently vanished from the fiche. The recommendation kept
    citing evidence the document no longer showed.
    """
    task = _task_bundle()
    output = _output(PASSAGE_ID.upper())

    # Accepted upstream: this is what made the omission silent rather than a failure.
    check_citations(output, task)

    run = _Run(task.model_dump(mode="json"))
    entries = build_evidence(run, output)  # type: ignore[arg-type]
    assert len(entries) == 1, "the cited passage must appear in the register"
    assert "Trimodality therapy" in entries[0].quote
    # The register shows the corpus's own spelling, not the agent's miscasing, so two
    # citations of one passage cannot read as two different sources.
    assert entries[0].id == PASSAGE_ID


def test_every_citation_refusal_is_handled_by_the_run_handler() -> None:
    """The defect: a new refusal was added to `schemas` and not to the handler.

    `CitationInWrongChannel` escaped `run_agent`, so the worker rolled back and failed the
    job generically -- losing the `AgentRun` row and its transcript link, which is the
    record a reviewer most needs when an agent cites into the wrong channel. This asserts
    the handler covers every refusal `check_citations` can raise, so the next one added
    cannot slip through the same gap.
    """
    raised_in_check = {
        name
        for name, obj in vars(agent_schemas).items()
        if inspect.isclass(obj)
        and issubclass(obj, ValueError)
        and name in inspect.getsource(agent_schemas.check_citations)
    }
    assert raised_in_check, "expected to find the refusals check_citations raises"

    handled = {cls.__name__ for cls in CITATION_FAILURES}
    missing = raised_in_check - handled
    assert not missing, (
        f"check_citations can raise {sorted(missing)}, which run_agent does not handle: "
        "the run would be lost instead of recorded as a failed AgentRun"
    )


@pytest.mark.parametrize(
    "failure", [CitationInWrongChannel, UncitedEvidence, UngroundedRecommendation]
)
def test_the_named_failures_are_the_ones_the_handler_catches(failure: type) -> None:
    assert issubclass(failure, CITATION_FAILURES)


def test_a_worker_without_configuration_convenes_nobody() -> None:
    """The defect: the promise held in three files and broke in the fourth.

    The ledger page tells the clinician the specialists will read only the version they
    approve. `AUTO_PANEL_ROLES` was emptied in the Azure template, in Compose and in
    `.env.example`, but the application default still listed all three specialities -- so a
    worker started without an explicit value went back to analysing the machine's first
    extraction, unreviewed.
    """
    assert Settings().auto_panel_role_list == []


def test_a_deployment_can_still_opt_into_an_unattended_panel() -> None:
    """Empty is a default, not a prohibition: a batch evaluation has nobody to press the
    button, and must still be able to convene."""
    settings = Settings(auto_panel_roles="urology_oncology,medical_oncology")
    assert settings.auto_panel_role_list == ["urology_oncology", "medical_oncology"]
