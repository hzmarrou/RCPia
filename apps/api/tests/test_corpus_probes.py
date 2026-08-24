"""Did the routing route?

A gate, not a score. These tests check the gate closes — a probe that cannot fail is a green
light for a broken corpus, which is the failure mode the whole rebuild exists to prevent.
"""

import json
from pathlib import Path

import pytest

from rcpia.corpus.probes import (
    PROBES_FILENAME,
    Probe,
    ProbeError,
    ProbeFilter,
    admits,
    load_probes,
    parse_probes,
    run_probe,
    run_probes,
)
from rcpia.corpus.rules import load_rules

CORPUS = Path(__file__).resolve().parents[1] / "rcpia" / "corpus"
PROBES = CORPUS / PROBES_FILENAME
ROUTING = CORPUS / "routing"
V4 = Path(
    r"C:\Users\hzmarrou\OneDrive\python\learning\RCPia\data\_prepared"
    r"\releases\rcpia-corpus-v4.0\chunks.jsonl"
)


def chunk(**overrides: object) -> dict:
    base = {
        "document_id": "afu.bladder-tvnim.2024-2026",
        "cancer_types": ["bladder"],
        "disease_setting": "bladder_nmibc_high_risk",
        "not_specified_reason": None,
        "clinical_purpose": ["therapy_indication"],
        "agent_roles": ["urology_oncology"],
        "retrieval_eligible": True,
    }
    return {**base, **overrides}


BLADDER = ProbeFilter(cancer_type="bladder", disease_setting="bladder_nmibc_high_risk")


def test_the_cancer_type_is_never_relaxed() -> None:
    assert admits(chunk(), BLADDER)
    assert not admits(chunk(cancer_types=["prostate"]), BLADDER)


def test_a_passage_excluded_from_retrieval_is_never_admitted() -> None:
    # A reference list is written and kept; it must never answer a question.
    assert not admits(chunk(retrieval_eligible=False), BLADDER)


def test_a_universal_passage_is_admitted_under_any_setting() -> None:
    universal = chunk(disease_setting="not_specified", not_specified_reason="general_principle")
    assert admits(universal, BLADDER)


def test_a_passage_the_rules_could_not_place_is_not_universal() -> None:
    # `not_determinable` is the routing admitting it could not place the passage. Treating that
    # as "applies everywhere" would let routing debt leak into every question.
    unplaced = chunk(disease_setting="not_specified", not_specified_reason="not_determinable")
    assert not admits(unplaced, BLADDER)


def test_general_passages_are_admitted_alongside_the_purpose_asked_for() -> None:
    wanted = ProbeFilter(cancer_type="bladder", clinical_purpose=("staging",))
    assert admits(chunk(clinical_purpose=["general"]), wanted)
    assert admits(chunk(clinical_purpose=["staging"]), wanted)
    assert not admits(chunk(clinical_purpose=["follow_up"]), wanted)


def test_the_role_filter_applies() -> None:
    renal = ProbeFilter(cancer_type="bladder", agent_role="renal_safety")
    assert not admits(chunk(), renal)
    assert admits(chunk(agent_roles=["renal_safety"]), renal)


def test_a_probe_fails_when_the_document_it_needs_stops_answering() -> None:
    # What a renamed heading looks like from here: the rules still parse, the audit is clean,
    # and the document silently stops reaching the question it was loaded for.
    probe = Probe(
        question="BCG dans une TVNIM à haut risque",
        filter=BLADDER,
        must_reach=("afu.bladder-tvnim.2024-2026",),
    )
    assert run_probe(probe, [chunk()]).passed
    moved = chunk(disease_setting="bladder_nmibc_unspecified_risk")
    result = run_probe(probe, [moved])
    assert not result.passed
    assert result.unreached == ("afu.bladder-tvnim.2024-2026",)
    assert "never reached" in result.describe()


def test_a_probe_fails_when_it_reaches_what_it_must_not() -> None:
    probe = Probe(
        question="Une question TVNIM ne doit pas atteindre le métastatique",
        filter=ProbeFilter(cancer_type="bladder"),
        must_not_reach={"disease_setting": ("bladder_metastatic",)},
    )
    assert run_probe(probe, [chunk()]).passed
    leaked = run_probe(probe, [chunk(disease_setting="bladder_metastatic")])
    assert not leaked.passed
    assert leaked.forbidden == ("disease_setting=bladder_metastatic",)


def test_a_forbidden_document_is_caught_too() -> None:
    probe = Probe(
        question="prostate must not reach bladder",
        filter=ProbeFilter(cancer_type="bladder"),
        must_not_reach={"document_id": ("afu.bladder-tvnim.2024-2026",)},
    )
    assert not run_probe(probe, [chunk()]).passed


def test_an_empty_probe_file_is_refused() -> None:
    with pytest.raises(ProbeError, match="empty gate"):
        parse_probes([])


def test_a_probe_that_asserts_nothing_is_refused() -> None:
    with pytest.raises(ProbeError, match="asserts nothing"):
        parse_probes([{"question": "q", "filter": {"cancer_type": "bladder"}}])


def test_a_probe_on_a_cancer_nothing_carries_is_refused() -> None:
    # It would admit nothing, satisfy its must_not_reach clause vacuously, and pass.
    with pytest.raises(ProbeError, match="not one this build covers"):
        parse_probes(
            [{"question": "q", "filter": {"cancer_type": "renal"}, "must_reach": ["x"]}]
        )


def test_an_unimplemented_setting_is_refused() -> None:
    with pytest.raises(ProbeError, match="not implemented"):
        parse_probes(
            [
                {
                    "question": "q",
                    "filter": {"cancer_type": "bladder", "disease_setting": "TYPO_SETTING"},
                    "must_reach": ["x"],
                }
            ]
        )


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ProbeError, match="unknown fields"):
        parse_probes(
            [{"question": "q", "filter": {"cancer_type": "bladder"}, "must_rech": ["x"]}]
        )


def test_must_not_reach_can_only_name_fields_a_probe_can_check() -> None:
    with pytest.raises(ProbeError, match="not a field a probe can check"):
        parse_probes(
            [
                {
                    "question": "q",
                    "filter": {"cancer_type": "bladder"},
                    "must_not_reach": {"publisher": ["x"]},
                }
            ]
        )


def test_the_committed_probes_parse() -> None:
    probes = load_probes(PROBES)
    assert len(probes) >= 10, "a dozen probes was the plan; fewer is a thinner gate"


def test_every_document_a_probe_requires_has_rules() -> None:
    # A probe naming a document with no rule file can never pass, and would sit red forever
    # rather than telling anyone anything.
    routed = {load_rules(f).document_id for f in ROUTING.glob("*.json")}
    for probe in load_probes(PROBES):
        for document_id in probe.must_reach:
            assert document_id in routed, (
                f"probe {probe.question!r} requires {document_id}, which has no rule file"
            )


def test_the_committed_probes_pass_against_the_corpus_they_describe() -> None:
    if not V4.exists():
        pytest.skip("the corpus sources are not reachable from here (OneDrive)")
    lines = V4.read_text(encoding="utf-8").splitlines()
    chunks = [json.loads(line) for line in lines if line.strip()]
    afu = [c for c in chunks if str(c.get("document_id", "")).startswith("afu.")]
    failures = [r.describe() for r in run_probes(load_probes(PROBES), afu) if not r.passed]
    assert not failures, "\n".join(failures)
