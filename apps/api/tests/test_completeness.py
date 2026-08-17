"""Context-sensitive completeness.

Two judgements are being tested. What each cancer type needs, and — more importantly —
when missing information should stop the analysis rather than merely be declared. RCPia
is decision support: an agent that says "I cannot advise without the muscle status" is
useful; one that silently reasons without it is dangerous, and one that refuses to run
over a missing bone scan is useless.
"""

import pytest

from rcpia.cases.completeness import BLOCKING_FIELDS, assess
from rcpia.cases.extraction import extract_from_text, resolve
from rcpia.cases.fact_schema import required_fields_for
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
