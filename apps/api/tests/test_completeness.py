"""Context-sensitive completeness.

Two judgements are being tested. What each cancer type needs, and — more importantly —
when missing information should stop the analysis rather than merely be declared. RCPia
is decision support: an agent that says "I cannot advise without the muscle status" is
useful; one that silently reasons without it is dangerous, and one that refuses to run
over a missing bone scan is useless.
"""

import pytest

from rcpia.cases.completeness import BLOCKING_FIELDS, assess, satisfies_blocking
from rcpia.cases.extraction import extract_from_text, resolve
from rcpia.cases.fact_schema import absence_answers, required_fields_for
from rcpia.cases.ledger import LedgerEntry, display_label, highlight_for
from rcpia.core.models import CancerType, FactStatus

COMPLETE_BLADDER = {
    "sex": "Femme",
    "age": "85 ans",
    "medical_history": "HTA",
    "illness_history": "Hématurie",
    "ct_tap": "Épaississement pariétal",
    "haemoglobin": "11.6",
    "creatinine": "17.33",
    "egfr": "30",
    "histology": "Carcinome urothélial",
    "ptnm": "p T2",
    "grade": "Haut",
    "muscle_invasion": "Oui",
}


def entries_from(
    values: dict[str, str | None], *, amber: set[str] | None = None
) -> list[LedgerEntry]:
    amber = amber or set()
    entries: list[LedgerEntry] = []
    for key, value in values.items():
        if key in amber:
            status = FactStatus.conflicting
        elif value is None:
            status = FactStatus.missing
        else:
            status = FactStatus.confirmed
        entries.append(
            LedgerEntry(
                key=key,
                label=display_label(key),
                category="biology",
                value=value,
                status=str(status),
                origin="machine",
                highlight=highlight_for(status),
            )
        )
    return entries


def test_a_complete_bladder_case_has_no_issues() -> None:
    report = assess(entries_from(COMPLETE_BLADDER), cancer_type=CancerType.bladder)

    assert report.issues == ()
    assert report.can_run_analysis is True


def test_a_missing_non_blocking_field_is_declared_but_does_not_stop_analysis() -> None:
    """Most gaps become a stated limitation, not a barrier."""
    values = dict(COMPLETE_BLADDER)
    values["haemoglobin"] = None

    report = assess(entries_from(values), cancer_type=CancerType.bladder)

    assert report.can_run_analysis is True
    assert "haemoglobin" in report.missing_keys
    assert any("manquante" in note for note in report.limitation_notes())


@pytest.mark.parametrize("key", ["histology", "muscle_invasion"])
def test_a_missing_blocking_field_stops_the_analysis(key: str) -> None:
    """Without histology or muscle status there is no safe bladder proposal to make."""
    values = dict(COMPLETE_BLADDER)
    values[key] = None

    report = assess(entries_from(values), cancer_type=CancerType.bladder)

    assert report.can_run_analysis is False
    assert [issue.key for issue in report.blocking] == [key]
    assert "analyse impossible" in report.blocking[0].message


def test_a_contradictory_blocking_field_is_as_bad_as_an_absent_one() -> None:
    """A dossier saying both "muscle invaded" and "not invaded" cannot be reasoned from."""
    report = assess(
        entries_from(COMPLETE_BLADDER, amber={"muscle_invasion"}),
        cancer_type=CancerType.bladder,
    )

    assert report.can_run_analysis is False
    assert report.blocking[0].key == "muscle_invasion"
    assert "contradictoires" in report.blocking[0].message


def test_a_contradictory_ordinary_field_is_only_flagged() -> None:
    report = assess(
        entries_from(COMPLETE_BLADDER, amber={"creatinine"}), cancer_type=CancerType.bladder
    )

    assert report.can_run_analysis is True
    assert "creatinine" in report.uncertain_keys


def test_requirements_differ_by_cancer_type() -> None:
    bladder = {field.key for field in required_fields_for(CancerType.bladder)}
    prostate = {field.key for field in required_fields_for(CancerType.prostate)}

    assert "muscle_invasion" in bladder and "muscle_invasion" not in prostate
    assert "psa_total" in prostate and "psa_total" not in bladder
    # Renal function gates cisplatin in bladder cancer, so it is required there.
    assert "egfr" in bladder


def test_a_prostate_case_missing_its_psa_is_declared_not_blocked() -> None:
    report = assess(
        entries_from(
            {
                "sex": "Homme",
                "age": "63 ans",
                "psa_total": None,
                "histology": "Adénocarcinome",
                # Present, so the PSA is the only gap under test. Without a Gleason the
                # case cannot be risk-stratified at all and blocks for that reason.
                "gleason": "7 (3+4)",
            }
        ),
        cancer_type=CancerType.prostate,
    )

    assert "psa_total" in report.missing_keys
    assert report.can_run_analysis is True, "a PSA gap is a limitation, not a barrier"


def test_a_prostate_case_without_a_gleason_cannot_be_risk_stratified() -> None:
    """Gleason decides surveillance versus active treatment as squarely as muscle invasion
    decides conservative treatment versus cystectomy."""
    report = assess(
        entries_from({"histology": "Adénocarcinome acinaire", "gleason": None}),
        cancer_type=CancerType.prostate,
    )

    assert report.can_run_analysis is False
    assert any(issue.key == "gleason" for issue in report.blocking)


def test_the_blocking_list_stays_short() -> None:
    """If everything blocks, nothing runs; this is a deliberate product decision."""
    for cancer_type, keys in BLOCKING_FIELDS.items():
        assert len(keys) <= 3, f"{cancer_type} blocks on too many fields"


def test_the_reference_dossier_is_complete_enough_to_analyse() -> None:
    """The real bladder dossier must not be refused by our own rules."""
    from tests.test_extraction import REFERENCE_DOSSIER

    if not REFERENCE_DOSSIER.exists():  # pragma: no cover - fixture is committed
        pytest.skip("reference dossier not available")

    resolved = resolve(
        extract_from_text(REFERENCE_DOSSIER.read_text(encoding="utf-8")),
        cancer_type=CancerType.bladder,
    )
    entries = [
        LedgerEntry(
            key=fact.key,
            label=display_label(fact.key),
            category="unknown",
            value=fact.value,
            status=str(fact.status),
            origin="machine",
            highlight=highlight_for(fact.status),
        )
        for fact in resolved
    ]

    report = assess(entries, cancer_type=CancerType.bladder)

    assert report.can_run_analysis is True, [issue.message for issue in report.blocking]


# --- `RAS` where absence cannot be an answer ---------------------------------------------------


def test_absence_answers_a_symptom_but_not_an_identity() -> None:
    """The distinction the whole rule rests on: `RAS` is a finding about a symptom and an empty
    box on a diagnosis."""
    assert absence_answers("haematuria")
    assert absence_answers("previous_rcp_decisions")

    for key in ("histology", "gleason", "isup", "ctnm", "ptnm"):
        assert not absence_answers(key), f"{key} must not accept RAS as a value"


def test_a_grade_of_ras_does_not_satisfy_a_blocking_field() -> None:
    """Already true before this change, and kept because it is the property that matters."""
    assert not satisfies_blocking("gleason", "RAS")
    assert not satisfies_blocking("histology", "néant")
    assert satisfies_blocking("gleason", "9 (5+4)")


def test_the_ras_rule_covers_every_blocking_field() -> None:
    """A blocking field that accepted `RAS` would stage a patient from an empty box.

    Checked against `BLOCKING_FIELDS` rather than a restated list, so a new blocking field
    cannot be added without deciding what absence means for it.
    """
    for cancer_type, keys in BLOCKING_FIELDS.items():
        for key in keys:
            assert not absence_answers(key), (
                f"{key} blocks analysis for {cancer_type} but would accept RAS"
            )


def test_a_score_is_a_number_or_it_was_not_done() -> None:
    """**The second half of the rule.** The identity fields were the first: `RAS` cannot answer
    a histology. Nor can it answer a score, and an agent put it better than we would --
    *« Score G8 chiffré ; "RAS" n'est pas interprétable comme résultat. »*

    Measured before the change: `g8_score` read `RAS` in fourteen dossiers of fifteen and
    rendered as filled in, so no clinician was ever prompted while the specialists asked for it
    repeatedly.
    """
    for key in ("g8_score", "ipss", "mna", "adl", "iief5", "who_status", "pallia10"):
        assert not absence_answers(key), f"{key} must not accept RAS as a score"


def test_an_ambiguous_core_count_is_not_an_answer() -> None:
    """Worse than absent. Nothing distinguishes "no positive cores" from "not counted", and a
    nomogram taking the percentage as an input gives very different answers for the two."""
    assert not absence_answers("cores_positive")


def test_ras_still_answers_a_negative_result() -> None:
    """**Where the rule stops.** An ECBU reading `RAS` is a sterile sample -- a result, not an
    empty box -- and treating it as missing would ask a clinician to re-enter something they
    already answered. `pregnancy` is a yes/no question, not a measurement."""
    assert absence_answers("urine_culture")
    assert absence_answers("pregnancy")
    assert absence_answers("allergies")
    assert absence_answers("family_history")


def test_the_new_score_rule_blocks_nothing() -> None:
    """`BLOCKING_FIELDS` is a separate and much smaller set. Making a score visible must not
    stop an analysis: a missing G8 is worth asking about and is not a reason to refuse a
    dossier."""
    blocking = set(BLOCKING_FIELDS[CancerType.prostate]) | set(BLOCKING_FIELDS[CancerType.bladder])

    for key in ("g8_score", "ipss", "mna", "adl", "iief5", "who_status", "cores_positive"):
        assert key not in blocking, f"{key} must not become a blocking field"


def test_prior_radiotherapy_is_a_field_the_ledger_can_hold() -> None:
    """MSKCC disqualifies on prior hormone therapy *or* prior radiotherapy and asks both
    explicitly. `hormone_therapy` existed; this did not, so the answer had nowhere to go and
    the page could not even show it in red."""
    from rcpia.cases.fact_schema import FIELDS_BY_KEY

    assert "radiation_therapy" in FIELDS_BY_KEY
    assert FIELDS_BY_KEY["radiation_therapy"].applies_to(CancerType.prostate)
    assert FIELDS_BY_KEY["radiation_therapy"].applies_to(CancerType.bladder)
