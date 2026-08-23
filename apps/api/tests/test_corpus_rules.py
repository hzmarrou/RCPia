"""Routing rules, and the checks that make them falsifiable.

The rules are the only stage of the corpus build carrying clinical judgment, and the reason the
corpus stalled at ten documents. Every property here exists because getting it wrong is silent:
a rule that matches nothing, a rule shadowed by a broader one above it, or a rule claiming both
halves of a heading that appears twice all leave a corpus that loads, validates, and routes
passages to the wrong clinical situation.
"""

import json

import pytest

from rcpia.corpus.rules import (
    Audit,
    RoutingRules,
    Rule,
    RuleError,
    audit,
    flatten,
    load_rules,
    normalise,
    parse_rules,
    route,
)

# Modelled on AFU's bladder TVNIM guideline, which is where the ordering trap is real:
# "haut risque" is a substring of "tres haut risque".
TVNIM = {
    "document_id": "afu.bladder-tvnim.2024-2026",
    "default": {"setting": "bladder_nmibc_unspecified_risk"},
    "rules": [
        {"match_section": "tres haut risque", "setting": "bladder_nmibc_very_high_risk"},
        {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
        {"match_section": "cis isole", "setting": "bladder_nmibc_cis"},
    ],
}

TVNIM_PATHS = [
    ["Traitement", "TVNIM à très haut risque"],
    ["Traitement", "TVNIM à haut risque"],
    ["Traitement", "CIS isolé"],
    ["Épidémiologie"],
]


def build(**overrides: object) -> RoutingRules:
    document = {**TVNIM, **overrides}
    return parse_rules(document)


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------


def test_headings_match_regardless_of_case_and_accents() -> None:
    """Headings and dossier labels come out of the same PDF extraction and carry the same
    inconsistencies, so they get the same treatment."""
    assert normalise("  TRÈS Haut   Risque ") == "tres haut risque"
    assert flatten(["Traitement", "CIS isolé"]) == "traitement > cis isole"


def test_the_first_matching_rule_wins_so_order_is_correctness() -> None:
    """**The trap this format exists to make visible.** `haut risque` matches inside `tres haut
    risque`. In a Python `if` tree the ordering is invisible until someone reorders it."""
    rules = build()

    assert route(rules, ["Traitement", "TVNIM à très haut risque"]).setting == (
        "bladder_nmibc_very_high_risk"
    )
    assert route(rules, ["Traitement", "TVNIM à haut risque"]).setting == (
        "bladder_nmibc_high_risk"
    )


def test_reversing_the_order_changes_the_answer() -> None:
    """Stated as a test rather than a comment: with the broad rule first, the specific heading
    routes to the wrong setting and nothing anywhere fails."""
    reversed_rules = build(
        rules=[
            {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
            {"match_section": "tres haut risque", "setting": "bladder_nmibc_very_high_risk"},
        ]
    )

    assert route(reversed_rules, ["Traitement", "TVNIM à très haut risque"]).setting == (
        "bladder_nmibc_high_risk"
    )


def test_an_unclaimed_heading_falls_to_the_default_and_says_so() -> None:
    decision = route(build(), ["Épidémiologie"])

    assert decision.matched_by_default
    assert decision.setting == "bladder_nmibc_unspecified_risk"


# --------------------------------------------------------------------------------------
# A rule that matches nothing
# --------------------------------------------------------------------------------------


def test_a_rule_matching_no_heading_is_reported_dead() -> None:
    """**Guidelines are revised annually and headings get renamed.** A rule matching nothing
    leaves its passages on the default, which is indistinguishable from successful routing."""
    rules = build(
        rules=[
            {"match_section": "carcinome in situ multifocal", "setting": "bladder_nmibc_cis"},
        ]
    )

    report = audit(rules, TVNIM_PATHS)

    assert [r.match_section for r in report.dead] == ["carcinome in situ multifocal"]
    assert "matches no heading" in report.problems[0]


def test_a_rule_shadowed_by_an_earlier_one_names_the_rule_that_shadows_it() -> None:
    """"This rule never fires" is not actionable on its own; "because `haut risque` comes
    first" is."""
    rules = build(
        rules=[
            {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
            {"match_section": "tres haut risque", "setting": "bladder_nmibc_very_high_risk"},
        ]
    )

    report = audit(rules, TVNIM_PATHS)

    assert len(report.shadowed) == 1
    earlier, later = report.shadowed[0]
    assert earlier.match_section == "haut risque"
    assert later.match_section == "tres haut risque"
    assert "can never fire" in report.problems[0]


def test_a_shadowed_rule_is_not_also_reported_dead() -> None:
    """They ask for different fixes. A dead rule needs its text corrected; a shadowed one needs
    moving. Reporting both would send the reader to change the wrong thing."""
    rules = build(
        rules=[
            {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
            {"match_section": "tres haut risque", "setting": "bladder_nmibc_very_high_risk"},
        ]
    )

    report = audit(rules, TVNIM_PATHS)

    assert report.dead == []


# --------------------------------------------------------------------------------------
# Headings that occur twice
# --------------------------------------------------------------------------------------

# `FOCUS` really does appear twice in one SFFPO document, with different content beneath each,
# and only the first was wanted.
REPEATED_PATHS = [
    ["Fiche 1", "FOCUS"],
    ["Fiche 2", "FOCUS"],
]

REPEATED = {
    "document_id": "sffpo.souffrance-psychique.2018",
    "default": {"setting": "not_specified", "reason": "not_determinable"},
    "rules": [
        {
            "match_section": "focus",
            "setting": "not_specified",
            "reason": "general_principle",
        }
    ],
}


def test_a_rule_matching_a_repeated_heading_is_reported_ambiguous() -> None:
    """A rule claiming both halves when one was meant is silent and wrong. The audit refuses to
    guess which."""
    report = audit(parse_rules(REPEATED), REPEATED_PATHS)

    assert [r.match_section for r in report.ambiguous] == ["focus"]
    assert "occurs more than once" in report.problems[0]


def test_pinning_the_occurrence_resolves_the_ambiguity() -> None:
    document = {
        **REPEATED,
        "rules": [
            {
                "match_section": "focus",
                "setting": "not_specified",
                "reason": "general_principle",
                "occurrence": 1,
            }
        ],
    }
    rules = parse_rules(document)

    assert audit(rules, REPEATED_PATHS).ambiguous == []
    assert not route(rules, ["Fiche 1", "FOCUS"], occurrence=1).matched_by_default
    assert route(rules, ["Fiche 2", "FOCUS"], occurrence=2).matched_by_default


def test_a_heading_occurring_once_needs_no_occurrence() -> None:
    """Requiring it everywhere would make every rule file noisier to no purpose, and noise is
    what stops rules being reviewed."""
    assert audit(build(), TVNIM_PATHS).ambiguous == []


# --------------------------------------------------------------------------------------
# Absence must state its reason
# --------------------------------------------------------------------------------------


def test_routing_to_not_specified_without_a_reason_is_refused() -> None:
    """`general_principle` and `not_determinable` mean opposite things: guidance that genuinely
    spans settings, versus routing that failed. 26% of the v4 corpus is the second, and
    collapsing the two is what hid that."""
    with pytest.raises(RuleError, match="without a usable `reason`"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "not_specified"},
                "rules": [],
            }
        )


def test_an_unknown_reason_is_refused() -> None:
    with pytest.raises(RuleError, match="without a usable `reason`"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "not_specified", "reason": "seemed_vague"},
                "rules": [],
            }
        )


def test_a_reason_on_a_present_setting_is_refused() -> None:
    """A reason explains an absent setting. Attaching one to a present setting states something
    false, and it would read as though the routing were uncertain when it is not."""
    with pytest.raises(RuleError, match="carries a `reason` but routes to"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "bladder_metastatic", "reason": "general_principle"},
                "rules": [],
            }
        )


# --------------------------------------------------------------------------------------
# The file must be checkable
# --------------------------------------------------------------------------------------


def test_a_file_without_a_default_is_refused() -> None:
    """Without one, a heading no rule claims has no setting at all, and the file cannot say
    what it believes about the rest of the document."""
    with pytest.raises(RuleError, match="declares no `default`"):
        parse_rules({"document_id": "x", "rules": []})


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    """Four routing fields were lost this way in an earlier release, read by nobody and noticed
    by nothing."""
    with pytest.raises(RuleError, match="does not implement"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "bladder_metastatic"},
                "rules": [{"match_section": "a", "setting": "bladder_metastatic", "grade": "high"}],
            }
        )


def test_a_setting_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(RuleError, match="not a disease setting"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "prostate_localised_high_risk"},  # British spelling
                "rules": [],
            }
        )


def test_a_role_outside_the_vocabulary_is_refused() -> None:
    """`safety_reviewer` is the live case: an agent directory exists and the curation manifest
    uses the value, but no chunk carries it and nothing has decided what it routes to."""
    with pytest.raises(RuleError, match="does not implement"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "bladder_metastatic", "roles": ["safety_reviewer"]},
                "rules": [],
            }
        )


def test_two_rules_with_the_same_match_are_refused() -> None:
    """The second can never fire, and which one was meant cannot be recovered from the file."""
    with pytest.raises(RuleError, match="repeats rules for"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "bladder_metastatic"},
                "rules": [
                    {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
                    {"match_section": "Haut Risque", "setting": "bladder_nmibc_very_high_risk"},
                ],
            }
        )


def test_the_default_may_not_carry_a_match() -> None:
    """A default that matches something is a rule, and would then have an order — which the
    file does not give it."""
    with pytest.raises(RuleError, match="must not carry `match_section`"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"match_section": "anything", "setting": "bladder_metastatic"},
                "rules": [],
            }
        )


def test_an_occurrence_of_zero_is_refused() -> None:
    """Occurrences are 1-based because they count appearances, not offsets. Zero would silently
    match nothing."""
    with pytest.raises(RuleError, match="1-based integer"):
        parse_rules(
            {
                "document_id": "x",
                "default": {"setting": "bladder_metastatic"},
                "rules": [
                    {
                        "match_section": "a",
                        "setting": "bladder_metastatic",
                        "occurrence": 0,
                    }
                ],
            }
        )


# --------------------------------------------------------------------------------------
# What the audit reports rather than refuses
# --------------------------------------------------------------------------------------


def test_the_default_rate_is_reported_so_idle_rules_are_visible() -> None:
    """A rule file can be structurally perfect and route almost nothing. Length is not
    evidence; this number is."""
    report = audit(build(), TVNIM_PATHS)

    assert report.routed == 3
    assert report.defaulted == 1
    assert report.default_rate == pytest.approx(0.25)


def test_a_clean_file_reports_no_problems() -> None:
    assert audit(build(), TVNIM_PATHS).problems == []


def test_an_empty_document_does_not_divide_by_zero() -> None:
    assert Audit(document_id="x").default_rate == 0.0


# --------------------------------------------------------------------------------------
# On disk
# --------------------------------------------------------------------------------------


def test_rules_load_from_a_file(tmp_path: object) -> None:
    path = tmp_path / "afu.bladder-tvnim.2024-2026.json"  # type: ignore[operator]
    path.write_text(json.dumps(TVNIM, ensure_ascii=False), encoding="utf-8")

    rules = load_rules(path)

    assert rules.document_id == "afu.bladder-tvnim.2024-2026"
    assert len(rules.rules) == 3
    assert isinstance(rules.rules[0], Rule)


def test_a_malformed_file_names_itself(tmp_path: object) -> None:
    path = tmp_path / "broken.json"  # type: ignore[operator]
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RuleError, match="broken.json is not valid JSON"):
        load_rules(path)


def test_a_rule_matching_a_parent_and_its_children_is_not_ambiguous() -> None:
    """**The case the ambiguity check must not flag**, and the reason it tests ancestry rather
    than counting.

    Naming a parent heading is the ordinary way to route a whole section: the rule matches that
    heading and every path beneath it, which is exactly the set of passages it means. Flagging
    that would make `occurrence` mandatory on almost every rule, and a check that fires on
    correct files is a check people learn to ignore.
    """
    rules = parse_rules(
        {
            "document_id": "x",
            "default": {"setting": "not_specified", "reason": "not_determinable"},
            "rules": [{"match_section": "traitement", "setting": "bladder_mibc_non_metastatic"}],
        }
    )

    report = audit(
        rules,
        [
            ["Traitement"],
            ["Traitement", "Cystectomie"],
            ["Traitement", "Préservation vésicale"],
        ],
    )

    assert report.ambiguous == []
    assert report.routed == 3


def test_a_rule_claiming_two_unrelated_branches_is_ambiguous() -> None:
    """The same heading text under two different parents, which is the real SFFPO case: `FOCUS`
    appears beneath two separate fiches with different content, and a rule naming it takes both
    while meaning one."""
    rules = parse_rules(
        {
            "document_id": "x",
            "default": {"setting": "not_specified", "reason": "not_determinable"},
            "rules": [
                {
                    "match_section": "focus",
                    "setting": "not_specified",
                    "reason": "general_principle",
                }
            ],
        }
    )

    report = audit(rules, [["Fiche 1", "FOCUS"], ["Fiche 2", "FOCUS"]])

    assert [r.match_section for r in report.ambiguous] == ["focus"]


def test_a_heading_repeating_across_consecutive_chunks_is_not_ambiguous() -> None:
    """**The common case, and it must stay quiet.** One section usually spans several chunks, so
    its path repeats. NCCN prostate repeats a path 207 times out of 328; treating repetition as
    ambiguity would flag every rule in the file and the check would be ignored."""
    rules = parse_rules(
        {
            "document_id": "x",
            "default": {"setting": "not_specified", "reason": "not_determinable"},
            "rules": [{"match_section": "cystectomie", "setting": "bladder_mibc_non_metastatic"}],
        }
    )

    report = audit(
        rules,
        [
            ["Traitement", "Cystectomie"],
            ["Traitement", "Cystectomie"],
            ["Traitement", "Cystectomie"],
        ],
    )

    assert report.ambiguous == []
    assert report.routed == 3


def test_a_heading_recurring_in_two_separate_places_is_ambiguous() -> None:
    """**The case separation exposes.** EAU's `5.3.5 Histopathology` appears at chunk 65 and
    again at chunk 436 — the same heading in two different parts of the guideline. A rule naming
    it takes both and means one, and no ordering fixes that.

    Measured across the ten loaded documents, only 0–8 headings per document recur this way, so
    asking for `occurrence` here costs almost nothing.
    """
    rules = parse_rules(
        {
            "document_id": "x",
            "default": {"setting": "not_specified", "reason": "not_determinable"},
            "rules": [
                {"match_section": "histopathology", "setting": "prostate_localized_high_risk"}
            ],
        }
    )

    report = audit(
        rules,
        [
            ["5.3 Pathology", "5.3.5 Histopathology"],
            ["6.1 Treatment"],
            ["5.3 Pathology", "5.3.5 Histopathology"],
        ],
    )

    assert [r.match_section for r in report.ambiguous] == ["histopathology"]


def test_a_rule_matching_two_children_of_its_own_heading_is_not_ambiguous() -> None:
    """**The false positive that running against real data exposed.**

    AFU's `nouvelles therapies pour les hauts risques` matches both
    `... > immunotherapie systemique` and `... > therapie genique endovesicale`. Different
    children, but both descend from the heading the rule names — one region, and the rule wants
    both. An earlier version compared the matched paths to each other rather than to the rule's
    own heading, and flagged this and one other correct rule in a seven-rule file.
    """
    rules = parse_rules(
        {
            "document_id": "afu.bladder-tvnim.2024-2026",
            "default": {"setting": "bladder_nmibc_unspecified_risk"},
            "rules": [
                {
                    "match_section": "nouvelles therapies pour les hauts risques",
                    "setting": "bladder_nmibc_high_risk",
                }
            ],
        }
    )

    report = audit(
        rules,
        [
            ["Nouvelles thérapies pour les hauts risques", "Immunothérapie systémique"],
            ["Nouvelles thérapies pour les hauts risques", "Thérapie génique endovésicale"],
        ],
    )

    assert report.ambiguous == []
    assert report.routed == 2


def test_a_string_prefix_is_not_treated_as_an_ancestor() -> None:
    """**A heading is not the parent of a longer heading that starts with the same words.**

    `Traitement du CIS isolé` and `Traitement du CIS isolé récidivant` are siblings: primary
    disease and recurrence, which are different clinical situations. A rule naming the first
    matches both, and that genuinely needs the author to say which.

    Comparing the matched paths character by character would find a common prefix of
    `... > traitement du cis isole`, conclude the rule sits above both, and stay silent. Only
    comparing on separator boundaries gives the true common ancestor —
    `cas particulier du cis isole` — which does not contain the rule's heading.

    The first version of this test used a needle that both strategies answered the same way, so
    it passed while the boundary logic was disabled. It is written here to separate them.
    """
    rules = parse_rules(
        {
            "document_id": "x",
            "default": {"setting": "not_specified", "reason": "not_determinable"},
            "rules": [
                {"match_section": "traitement du cis isole", "setting": "bladder_nmibc_cis"}
            ],
        }
    )

    report = audit(
        rules,
        [
            ["Cas particulier du CIS isolé", "Traitement du CIS isolé récidivant"],
            ["Cas particulier du CIS isolé", "Traitement du CIS isolé"],
        ],
    )

    assert [r.match_section for r in report.ambiguous] == ["traitement du cis isole"]
