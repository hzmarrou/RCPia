"""Review/runtime equivalence and executable JSON examples, not model evaluation."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from rcpia.agents.pi_adapter import PiAdapter
from rcpia.agents.schemas import (
    SynthesisOutput,
    TaskBundle,
    check_citations,
    check_synthesis,
    output_model_for,
)
from rcpia.ops.agent_brains import (
    BEGIN,
    END,
    PROMOTIONS,
    REPOSITORY_ROOT,
    expected_brains,
    payload,
)
from rcpia.ops.brain_v2_cases import shadow_cases, synthesis_availability_errors

REVIEW_ONLY = {
    "nutrition": "agent-nutrition-brain-v2.md",
    "palliative_supportive": "agent-palliative-supportive-brain-v2.md",
    "pathology": "agent-pathology-brain-v2.md",
    "radiology": "agent-radiology-brain-v2.md",
}
SECTIONS = (
    "identity", "mission", "scope", "inputs", "evidence_policy", "reasoning_protocol",
    "safety_constraints", "output_contract", "examples", "uncertainty_rules",
    "prohibitions", "style",
)


@pytest.mark.parametrize("role,document", {**PROMOTIONS, **REVIEW_ONLY}.items())
def test_all_eight_review_payloads_have_balanced_sections_and_valid_examples(
    role: str, document: str,
) -> None:
    text = (REPOSITORY_ROOT / "docs" / "implementation" / document).read_text(
        encoding="utf-8",
    )
    tags = re.findall(
        r"<(/?)(" + "|".join((*SECTIONS, "example")) + r")(?:\s[^>]*)?>", text,
    )
    stack: list[str] = []
    for closing, name in tags:
        if closing:
            assert stack and stack.pop() == name
        else:
            stack.append(name)
    assert not stack
    assert [name for closing, name in tags if not closing and name != "example"] == list(
        SECTIONS,
    )
    contract = text.split("<output_contract>", 1)[1].split("</output_contract>", 1)[0]
    examples = re.findall(r"```json\s*\n(.*?)\n```", contract, re.DOTALL)
    assert len(examples) == 1
    example = json.loads(examples[0])
    assert example["agent_role"] == role
    output_model_for(role).model_validate(example)


def test_all_four_active_brains_match_review_payloads(tmp_path: Path) -> None:
    adapter = PiAdapter(agents_root=REPOSITORY_ROOT / "agents", work_root=tmp_path)
    expected = expected_brains(REPOSITORY_ROOT)
    assert {path.parent.name for path in expected} == set(PROMOTIONS)
    for destination, content in expected.items():
        assert adapter.agent_dir(destination.parent.name) / "brain.md" == destination
        # Git's Windows checkout may use CRLF; no other content normalization is allowed.
        assert destination.read_text(encoding="utf-8").encode("utf-8") == content


@pytest.mark.parametrize("role", REVIEW_ONLY)
def test_review_only_roles_do_not_acquire_runtime_brains(role: str) -> None:
    assert not (REPOSITORY_ROOT / "agents" / role / "brain.md").exists()


@pytest.mark.parametrize(
    "text",
    [
        "<identity>incomplete</identity>",
        f"{BEGIN}<identity>x</identity>{END}",
        f"{END}{BEGIN}<identity>x</identity></style>",
        f"{BEGIN}<identity>x</identity></style>{END}trailing instructions",
        f"{BEGIN}{BEGIN}<identity>x</identity></style>{END}",
    ],
)
def test_malformed_review_boundaries_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        payload(text)


def test_editorial_text_is_excluded_without_changing_prompt_content() -> None:
    body = "<identity>\nIdentity.\n</identity>\n<style>\nStyle.\n</style>"
    assert payload(f"Editorial text.\n{BEGIN}\n\n{body}\n{END}\n") == body + "\n"


def test_recorded_synthesis_availability_evidence_has_matching_inputs_and_outputs() -> None:
    """Audit recorded model structures, not semantic correctness or future model reliability."""
    path = (
        REPOSITORY_ROOT / "docs" / "implementation"
        / "agent-brains-v2-synthesis-availability.json"
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))
    cases = {
        case.case_id: case for case in shadow_cases()
        if case.bundle.inputs.agent_role == "rcp_synthesis"
    }
    assert {record["case_id"] for record in evidence["cases"]} == set(cases)
    assert len(evidence["cases"]) == len(cases)
    for record in evidence["cases"]:
        case = cases[record["case_id"]]
        bundle = TaskBundle.model_validate({
            **evidence["bundle_template"], "inputs": record["inputs"],
        })
        assert bundle == case.bundle
        assert record["expected"] == case.expected
        assert record["bundle_sha256"] == hashlib.sha256(
            bundle.model_dump_json(by_alias=True).encode("utf-8")
        ).hexdigest()
        output = SynthesisOutput.model_validate(record["output"])
        assert output.case_id == bundle.inputs.case_id
        assert output.recommendation_withheld == case.expected_withheld
        check_citations(output, bundle)
        check_synthesis(output, bundle)
        assert record["contract_valid"] is True
        assert synthesis_availability_errors(output, bundle) == (
            record["synthesis_availability_errors"]
        ) == []
        prior = SynthesisOutput.model_validate(record["prior_output"])
        assert prior.case_id == bundle.inputs.case_id
        check_citations(prior, bundle)
        check_synthesis(prior, bundle)
        assert synthesis_availability_errors(prior, bundle) == record["prior_availability_errors"]
