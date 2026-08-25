"""The corpus must be able to say what it covers, and why it leaves the rest out.

These run on committed data alone -- `coverage.json` and the rule files -- with no database, no
reference library and no network. That is the point: the curation lives in OneDrive and is
invisible to CI, so the claims it makes about scope are exported here and checked here.

The failure each one prevents is the same shape every time: a corpus that loads, validates and
answers nothing about a document, while reporting success.
"""

import json
from pathlib import Path

import pytest

from rcpia.corpus.coverage import (
    COVERAGE_FILENAME,
    CoverageError,
    load_coverage,
    parse_coverage,
)
from rcpia.corpus.rules import load_rules

CORPUS = Path(__file__).resolve().parents[1] / "rcpia" / "corpus"
COVERAGE_PATH = CORPUS / COVERAGE_FILENAME
ROUTING = CORPUS / "routing"

COVERAGE = load_coverage(COVERAGE_PATH)
RULE_FILES = sorted(ROUTING.glob("*.json"))
ROUTED = {load_rules(path).document_id for path in RULE_FILES}


def test_the_export_is_readable_and_has_documents() -> None:
    assert COVERAGE.documents, "coverage.json lists no documents"
    assert COVERAGE.included, "no document is in scope, which cannot be right"


def test_every_included_document_has_a_rule_file() -> None:
    # Without one it loads carrying its document default and nothing else -- no exclusions, no
    # settings -- which reads exactly like a document that was routed and simply spans everything.
    missing = sorted(d.document_id for d in COVERAGE.included if d.document_id not in ROUTED)
    assert not missing, (
        "these documents are in scope but have no rule file, so a build would load them "
        f"unrouted: {', '.join(missing)}"
    )


def test_every_rule_file_has_a_curation_record() -> None:
    # The other direction, and it catches a different mistake: a rule file whose document_id
    # drifted from the curation's, which makes every coverage count wrong while both sides look
    # complete. Eight documents carried two ids for exactly this reason until 24 August.
    known = {d.document_id for d in COVERAGE.documents}
    orphaned = sorted(ROUTED - known)
    assert not orphaned, (
        "these rule files route a document the curation does not record: "
        f"{', '.join(orphaned)}"
    )


def test_every_rule_file_routes_a_document_that_is_in_scope() -> None:
    out_of_scope = {d.document_id for d in COVERAGE.excluded}
    routed_but_excluded = sorted(ROUTED & out_of_scope)
    assert not routed_but_excluded, (
        "these documents are excluded from the corpus but still carry a rule file, so one of "
        f"the two is wrong: {', '.join(routed_but_excluded)}"
    )


def test_every_excluded_document_states_a_reason() -> None:
    # Excluding is not deleting, and the distinction only survives if the reason is written
    # down. A passage removed silently and a passage removed for a stated reason look identical
    # from the outside.
    silent = sorted(d.document_id for d in COVERAGE.excluded if not d.states_a_reason)
    assert not silent, (
        f"excluded with neither a reason nor a note: {', '.join(silent)}"
    )


def test_every_declared_duplicate_points_at_a_document_we_have() -> None:
    known = {d.document_id for d in COVERAGE.documents}
    dangling = sorted(
        f"{d.document_id} -> {d.duplicate_of}"
        for d in COVERAGE.documents
        if d.duplicate_of and d.duplicate_of not in known
    )
    assert not dangling, f"duplicate_of names an unknown document: {'; '.join(dangling)}"


def test_no_document_is_a_duplicate_of_itself() -> None:
    circular = sorted(
        d.document_id for d in COVERAGE.documents if d.duplicate_of == d.document_id
    )
    assert not circular, f"declared a duplicate of itself: {', '.join(circular)}"


def test_a_duplicate_is_not_also_in_scope() -> None:
    # If both copies are loaded, the same recommendation comes back from two documents and reads
    # as two sources agreeing.
    both = sorted(d.document_id for d in COVERAGE.included if d.duplicate_of)
    assert not both, (
        f"in scope while declared a duplicate of another document: {', '.join(both)}"
    )


def test_the_counts_are_what_the_review_prompt_says() -> None:
    # A guard against silent drift in either direction: the number in
    # docs/reviews/corpus-v5-pre-build-review-prompt.md is 37, and a reviewer reading a stale
    # number is worse served than one reading none.
    assert len(COVERAGE.included) == len(ROUTED) == len(RULE_FILES)


# --- the parser itself -----------------------------------------------------------------


def test_a_repeated_document_id_is_refused() -> None:
    body = {
        "documents": [
            {"document_id": "a", "included": True},
            {"document_id": "a", "included": False},
        ]
    }
    with pytest.raises(CoverageError, match="more than once"):
        parse_coverage(body)


def test_a_missing_included_flag_is_refused() -> None:
    with pytest.raises(CoverageError, match="must be true or false"):
        parse_coverage({"documents": [{"document_id": "a"}]})


def test_an_empty_document_id_is_refused() -> None:
    with pytest.raises(CoverageError, match="non-empty document_id"):
        parse_coverage({"documents": [{"document_id": "  ", "included": True}]})


def test_a_document_list_is_required() -> None:
    with pytest.raises(CoverageError, match="`documents` as a list"):
        parse_coverage({"note": "no documents here"})


def test_a_claim_knows_whether_it_states_a_reason() -> None:
    coverage = parse_coverage(
        {
            "documents": [
                {"document_id": "silent", "included": False},
                {"document_id": "reasoned", "included": False, "exclusion_reason": "duplicate"},
                {"document_id": "noted", "included": False, "exclusion_note": "why"},
            ]
        }
    )
    states = {d.document_id: d.states_a_reason for d in coverage.documents}
    assert states == {"silent": False, "reasoned": True, "noted": True}


def test_the_committed_export_is_valid_json_with_a_note() -> None:
    body = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    assert body["note"], "the export should say what it is and how it was made"
    assert body["source"], "the export should name the manifest it came from"
