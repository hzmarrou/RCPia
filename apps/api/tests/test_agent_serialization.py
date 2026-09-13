"""Lossless formatting only: the size gate and Pi share this wire representation."""

import json
import re

import pytest

from rcpia.agents.bundle import MAX_BUNDLE_BYTES
from rcpia.agents.schemas import GuidelinePassage, LedgerField, TaskBundle
from rcpia.agents.serialization import bundle_bytes
from tests.test_pi_adapter import bundle


def test_compact_attachment_preserves_every_nonformatting_token_and_round_trips() -> None:
    task = bundle()
    text = '  \u00e9\u202f\U0001f9ea\t"quoted, : { } [ ]" \\\\" \r\nline\nnext\r  '
    task.inputs.source_snippets[0].text = text
    task.inputs.case_ledger = [
        LedgerField(
            key="fixture",
            label="Synthetic field",
            value=text,
            status="uncertain",
            source_ids=["snip-001"],
        )
    ]
    task.inputs.guideline_passages = [
        GuidelinePassage(
            id="fixture.guideline",
            source="Synthetic source",
            version="2026",
            section="Original heading > Continued",
            page=3,
            page_end=5,
            clinical_status="poc_unreviewed",
            text=text + "| Header  | Value |\r\n| --- | --- |\r\n|  padded  cell |  0  |\n",
        )
    ]
    task.output_format.schema_["examples"] = [
        {" spaced  key ": [None, False, True, 0, -1, 1.25, 1e-20, text], "empty": {}}
    ]
    before = task.model_dump(mode="json", by_alias=True)
    previous = json.dumps(before, ensure_ascii=False, indent=2, allow_nan=False)
    actual = bundle_bytes(task)

    # Match complete JSON string literals first so their whitespace/escapes survive.
    nonformatting_tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[^ \t\r\n]', previous)
    assert actual.decode("utf-8") == "".join(nonformatting_tokens)
    assert len(actual) < len(previous.encode("utf-8"))
    assert json.loads(actual) == before
    assert TaskBundle.model_validate_json(actual) == task
    assert task.model_dump(mode="json", by_alias=True) == before
    assert bundle_bytes(task) == actual
    assert "schema" in json.loads(actual)["output_format"]
    assert "schema_" not in json.loads(actual)["output_format"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_numbers_keep_the_existing_encoder_behavior(value: float) -> None:
    task = bundle()
    task.output_format.schema_["examples"] = [value]
    try:
        previous = json.dumps(
            task.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except ValueError:
        with pytest.raises(ValueError, match="Out of range float"):
            bundle_bytes(task)
    else:
        assert json.loads(bundle_bytes(task)) == json.loads(previous)


def test_lossless_formatting_does_not_raise_the_bundle_limit() -> None:
    assert MAX_BUNDLE_BYTES == 223_232
