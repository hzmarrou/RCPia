"""The safety rules, and the three ways each one has to behave.

Every rule gets a **positive** test (fires on the patient it is for), a **negative** test (does
not fire on a patient it is not for), and an **edge case** — almost always a passage that
mentions the hazard *and already addresses it*, which must not be flagged. That third one is
where a naive keyword rule does damage: flagging EAU's *"Do not offer chemotherapy alone as
primary therapy"* would train a reader to ignore the flags.

Two properties matter more than any individual rule and are tested first:

- **An unevaluable rule is not a passed rule.** A condition needing an eGFR the ledger does not
  record is reported by name, never silently skipped. Three of seven bladder dossiers are in
  exactly that state.
- **Nothing is excluded in v1.** Every action is FLAG, so the passage list out is the passage
  list in. The exclusion path is tested against a rule built for the test, not against the
  shipped catalogue.
"""

import re

import pytest

from rcpia.evaluation.ledgers import DOSSIER_LEDGERS
from rcpia.safety_rules.catalogue import (
    RULES,
    RULES_BY_ID,
    Condition,
    Ledger,
    MatchKind,
    PassagePattern,
    SafetyAction,
    SafetyRule,
    Severity,
)
from rcpia.safety_rules.engine import Passage, SafetyEngine

ENGINE = SafetyEngine()


def passage(text: str, chunk_id: str = "c1") -> Passage:
    return Passage(chunk_id=chunk_id, text=text)


def fired(report, rule_id: str) -> bool:
    return any(a.rule_id == rule_id for a in report.annotations)


def unevaluated(report, rule_id: str) -> bool:
    return any(u.rule_id == rule_id for u in report.unevaluated)


# --------------------------------------------------------------------------------------
# The two properties that outrank the individual rules
# --------------------------------------------------------------------------------------


def test_a_rule_with_no_measurement_is_unevaluated_not_silently_passed() -> None:
    """`"the cisplatin check could not run"` and `"no cisplatin concern"` are different
    statements, and a board shown the second when the first is true has been misled.
    """
    report = ENGINE.apply(
        Ledger(cancer_type="bladder"),  # no eGFR recorded
        [passage("Offer cisplatin-based neoadjuvant chemotherapy.")],
    )
    assert not fired(report, "1a-cisplatin-contraindicated")
    assert unevaluated(report, "1a-cisplatin-contraindicated")
    missing = next(u for u in report.unevaluated if u.rule_id == "1a-cisplatin-contraindicated")
    assert missing.missing_fields == ("egfr",)
    assert "does not contain egfr" in missing.message


def test_three_of_the_seven_bladder_dossiers_cannot_be_checked_for_platinum() -> None:
    """Not a hypothetical: D10, D11 and D14 record no eGFR. D14 is a pT2 high grade at 79."""
    blind = [
        d for d in ("dossier-10", "dossier-11", "dossier-14")
        if unevaluated(
            ENGINE.apply(DOSSIER_LEDGERS[d], [passage("cisplatin-based chemotherapy")]),
            "1b-unfit-any-platinum",
        )
    ]
    assert blind == ["dossier-10", "dossier-11", "dossier-14"]


def test_nothing_is_excluded_because_every_shipped_rule_flags() -> None:
    assert all(rule.action is SafetyAction.FLAG for rule in RULES)
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-1"], [passage("Give cisplatin-based chemotherapy.")]
    )
    assert report.excluded == frozenset()
    assert report.annotations  # it did fire -- it just did not remove anything


def test_the_exclusion_path_works_when_a_rule_asks_for_it() -> None:
    """Tested against a rule built here, because the shipped catalogue deliberately has none."""
    rule = SafetyRule(
        rule_id="test-exclude",
        title="test",
        condition=Condition(requires=(), test=lambda _: True, description="always"),
        passage_pattern=PassagePattern(
            kind=MatchKind.KEYWORD, requires=(re.compile("hazard"),)
        ),
        reason_template="hazardous",
        source_chunk_ids=(),
        action=SafetyAction.EXCLUDE,
    )
    report = SafetyEngine(rules=[rule]).apply(
        Ledger(), [passage("a hazard"), passage("safe", "c2")]
    )
    assert report.excluded == frozenset({"c1"})


def test_a_precomputed_rule_is_reported_as_awaiting_not_as_clean() -> None:
    """Rules 2, 3 and 6 match a clinical judgement spread across sentences. They are wired and
    inert, and the difference between *inert* and *found nothing* has to be visible.
    """
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-2"],  # MNA 7, age 85, G8 5 -- rules 2 and 3 both apply
        [passage("Radical cystectomy is the standard treatment.")],
    )
    assert "2-nutrition-before-major-surgery" in report.awaiting_precompute
    assert "3-geriatric-assessment-before-radical-treatment" in report.awaiting_precompute
    assert not fired(report, "2-nutrition-before-major-surgery")


def test_a_precomputed_rule_fires_once_a_reviewed_table_exists() -> None:
    report = SafetyEngine(
        precomputed={"2-nutrition-before-major-surgery": frozenset({"c1"})}
    ).apply(DOSSIER_LEDGERS["dossier-2"], [passage("Radical cystectomy.")])
    assert fired(report, "2-nutrition-before-major-surgery")
    assert "2-nutrition-before-major-surgery" not in report.awaiting_precompute


# --------------------------------------------------------------------------------------
# Rule 1a / 1b -- the platinum bands
# --------------------------------------------------------------------------------------

UNQUALIFIED = "Preferred radiosensitizing regimen: Cisplatin alone."
QUALIFIED = "Offer cisplatin-based neoadjuvant chemotherapy to eligible patients."


def test_1b_fires_on_the_gfr_28_patient_and_1a_does_not() -> None:
    """Dossier 1 is unfit for *any* platinum, not merely cisplatin-ineligible. The single rule
    this replaced would have recommended carboplatin, which is contraindicated too.
    """
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-1"], [passage(UNQUALIFIED)])
    assert fired(report, "1b-unfit-any-platinum")
    assert not fired(report, "1a-cisplatin-contraindicated")


def test_1a_fires_on_the_gfr_30_patient_and_1b_does_not() -> None:
    """Dossier 2 at exactly 30 sits in the carboplatin band. EAU says `GFR < 30` for unfit, so
    30 is *not* below it -- the boundary the corrected rule was written to get right.
    """
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-2"], [passage(UNQUALIFIED)])
    assert fired(report, "1a-cisplatin-contraindicated")
    assert not fired(report, "1b-unfit-any-platinum")


def test_neither_platinum_rule_fires_at_a_healthy_clearance() -> None:
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-13"], [passage(UNQUALIFIED)])  # eGFR 85
    assert not fired(report, "1a-cisplatin-contraindicated")
    assert not fired(report, "1b-unfit-any-platinum")


def test_a_passage_that_already_names_eligibility_is_not_flagged() -> None:
    """**The edge case that matters most.** The guidelines mostly qualify themselves -- AUA says
    "eligible patients", AFU says "patients eligibles" -- which is why the hazard lives in
    regimen tables rather than in recommendation sections, and why the forbidden set is small.
    """
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-1"], [passage(QUALIFIED)])
    assert not fired(report, "1b-unfit-any-platinum")


def test_a_passage_recommending_against_chemotherapy_is_not_flagged() -> None:
    """EAU 6.10.2.a says *"Do not offer chemotherapy alone as primary therapy"*. Flagging the
    correct answer as a hazard is how a safety layer teaches people to ignore it.
    """
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-1"],
        [passage("Do not offer cisplatin-based chemotherapy alone as primary therapy.")],
    )
    assert not fired(report, "1b-unfit-any-platinum")


def test_1b_covers_carboplatin_not_only_cisplatin() -> None:
    """The whole point of the split: at eGFR 28 the carboplatin fallback is contraindicated."""
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-1"], [passage("Gemcitabine and carboplatin is preferred.")]
    )
    assert fired(report, "1b-unfit-any-platinum")


def test_the_reason_names_the_setting_specific_carboplatin_advice() -> None:
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-2"], [passage(UNQUALIFIED)])
    reason = next(
        a.reason for a in report.annotations if a.rule_id == "1a-cisplatin-contraindicated"
    )
    assert "insufficient evidence for carboplatin as neoadjuvant" in reason
    assert "30" in reason


# --------------------------------------------------------------------------------------
# Rule 1a-contested -- the divergence
# --------------------------------------------------------------------------------------


def test_the_contested_band_carries_both_positions() -> None:
    report = ENGINE.apply(
        Ledger(cancer_type="bladder", egfr=55, performance_status=1,
               significant_comorbidity=False),
        [passage("Cisplatin-based chemotherapy is recommended.")],
    )
    annotation = next(
        a for a in report.annotations if a.rule_id == "1a-contested-cisplatin-50-60"
    )
    assert "EAU" in annotation.reason and "ESMO" in annotation.reason
    assert annotation.divergence


def test_the_contested_band_is_unevaluable_for_every_real_dossier() -> None:
    """No dossier records a performance status, so the rule cannot be evaluated for any of
    them. Kept because it fires on patient 16 -- and honest about firing on nobody today.
    """
    for ledger in DOSSIER_LEDGERS.values():
        report = ENGINE.apply(ledger, [passage("cisplatin")])
        assert not fired(report, "1a-contested-cisplatin-50-60")


def test_comorbidity_moves_the_contested_band_into_plain_contraindication() -> None:
    report = ENGINE.apply(
        Ledger(cancer_type="bladder", egfr=55, performance_status=2,
               significant_comorbidity=True),
        [passage(UNQUALIFIED)],
    )
    assert fired(report, "1a-cisplatin-contraindicated")
    assert not fired(report, "1a-contested-cisplatin-50-60")


# --------------------------------------------------------------------------------------
# Rule 1c -- patient-level, not about any passage
# --------------------------------------------------------------------------------------


def test_1c_fires_on_the_patient_with_no_passage_attached() -> None:
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-2"], [])
    annotation = next(a for a in report.annotations if a.rule_id == "1c-confirm-borderline-gfr")
    assert annotation.is_patient_level
    assert annotation.chunk_id is None
    assert "timed urine collection" in annotation.reason


def test_1c_does_not_fire_below_the_band() -> None:
    """Dossier 1 at 28 is unambiguously unfit -- there is nothing borderline to confirm."""
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-1"], [])
    assert not fired(report, "1c-confirm-borderline-gfr")


# --------------------------------------------------------------------------------------
# Rule 4 -- active surveillance in high-risk prostate
# --------------------------------------------------------------------------------------


def test_4_fires_on_high_risk_by_stage_alone() -> None:
    """Dossier 9 is Gleason 7 and ISUP 3 -- not high risk by grade -- but T3a and PSA 34."""
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-9"],
        [passage("Active surveillance may be offered to selected patients.")],
    )
    assert fired(report, "4-active-surveillance-in-high-risk-prostate")


def test_4_does_not_fire_on_favourable_intermediate_risk() -> None:
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-3"],  # Gleason 7, ISUP 2, PSA 7.5, no T3
        [passage("Active surveillance may be offered to selected patients.")],
    )
    assert not fired(report, "4-active-surveillance-in-high-risk-prostate")


def test_4_does_not_flag_a_passage_that_states_the_high_risk_exclusion() -> None:
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-6"],
        [passage("Active surveillance is not recommended in high-risk disease.")],
    )
    assert not fired(report, "4-active-surveillance-in-high-risk-prostate")


# --------------------------------------------------------------------------------------
# Rule 5 -- hormone-naive guidance for a castration-resistant patient
# --------------------------------------------------------------------------------------


def test_5_fires_for_the_castration_resistant_patient() -> None:
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-8"],
        [passage("In hormone-sensitive metastatic disease, offer ADT with docetaxel.")],
    )
    assert fired(report, "5-hormone-naive-passage-for-crpc-patient")


def test_5_does_not_fire_for_a_patient_not_on_adt() -> None:
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-6"],
        [passage("In hormone-sensitive metastatic disease, offer ADT with docetaxel.")],
    )
    assert not fired(report, "5-hormone-naive-passage-for-crpc-patient")


def test_5_does_not_flag_a_passage_that_names_castration_resistance() -> None:
    report = ENGINE.apply(
        DOSSIER_LEDGERS["dossier-8"],
        [passage("For castration-resistant disease after ADT, offer abiraterone.")],
    )
    assert not fired(report, "5-hormone-naive-passage-for-crpc-patient")


# --------------------------------------------------------------------------------------
# Rules 2, 3 and 6 -- conditions tested; matching awaits the precompute
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule_id", "fires_on", "not_on"),
    [
        ("2-nutrition-before-major-surgery", "dossier-2", "dossier-15"),
        ("3-geriatric-assessment-before-radical-treatment", "dossier-2", "dossier-1"),
        ("6-recurrent-nmibc-prior-therapy", "dossier-15", "dossier-11"),
    ],
)
def test_the_precomputed_rules_conditions_select_the_right_patients(
    rule_id: str, fires_on: str, not_on: str
) -> None:
    """The condition is testable even though the passage match is not yet built."""
    rule = RULES_BY_ID[rule_id]
    assert rule.condition.test(DOSSIER_LEDGERS[fires_on])
    assert not rule.condition.test(DOSSIER_LEDGERS[not_on])


# --------------------------------------------------------------------------------------
# Catalogue invariants
# --------------------------------------------------------------------------------------


def test_every_rule_is_unreviewed_and_says_so() -> None:
    """No clinician has approved any of these. Anything the engine emits must carry that."""
    assert all(rule.clinical_status == "poc_unreviewed" for rule in RULES)
    report = ENGINE.apply(DOSSIER_LEDGERS["dossier-1"], [passage(UNQUALIFIED)])
    assert all(a.clinical_status == "poc_unreviewed" for a in report.annotations)


def test_every_rule_id_is_unique_and_every_reason_template_renders() -> None:
    assert len({rule.rule_id for rule in RULES}) == len(RULES)
    for rule in RULES:
        # A template naming a field the ledger does not have would raise at the worst moment.
        rule.reason_template.format(
            **{name: "x" for name in Ledger.__dataclass_fields__}
        )


def test_a_sourced_rule_cites_the_guideline_it_came_from() -> None:
    """A rule with no citation cannot be checked by the clinician who reviews it. The three
    awaiting precompute are exempt -- their sources arrive with the reviewed table.
    """
    awaiting = {
        "2-nutrition-before-major-surgery",
        "4-active-surveillance-in-high-risk-prostate",
        "6-recurrent-nmibc-prior-therapy",
    }
    for rule in RULES:
        if rule.rule_id not in awaiting:
            assert rule.source_chunk_ids, f"{rule.rule_id} cites nothing"


def test_severity_is_reserved_for_what_a_guideline_calls_a_contraindication() -> None:
    contraindications = {r.rule_id for r in RULES if r.severity is Severity.CONTRAINDICATION}
    assert contraindications == {
        "1a-cisplatin-contraindicated",
        "1b-unfit-any-platinum",
        "4-active-surveillance-in-high-risk-prostate",
    }
