"""Regressions for the findings of the post-Phase-8 review.

Each test here corresponds to a defect a reviewer found in shipped code, and each was
demonstrated on a real dossier rather than a fixture. They live together so the reasons
stay attached to the checks.
"""

from pathlib import Path

import pytest

from rcpia.cases.completeness import assess, satisfies_blocking
from rcpia.cases.extraction import (
    TRUNCATION_MARKER,
    extract_from_text,
    resolve,
    starts_a_new_report,
)
from rcpia.cases.ledger import LedgerEntry, display_label, highlight_for
from rcpia.core.models import CancerType
from rcpia.pps.schemas import SupportCare, SupportItem
from rcpia.rules.approval import SUGGESTION_CAVEAT, Authority
from rcpia.rules.support import STANDING, suggested_items

DOSSIERS = Path(r"C:\Users\hzmarrou\OneDrive\python\learning\pi\rcpia\docs")


def ledger(text: str, cancer_type: CancerType) -> list[LedgerEntry]:
    return [
        LedgerEntry(
            key=fact.key,
            label=display_label(fact.key),
            category="x",
            value=fact.value,
            status=str(fact.status),
            origin="machine",
            highlight=highlight_for(fact.status),
        )
        for fact in resolve(extract_from_text(text), cancer_type=cancer_type)
    ]


def dossier(name: str) -> str:
    path = DOSSIERS / f"{name}.md"
    if not path.exists():  # pragma: no cover - the dossiers are not in the repository
        pytest.skip(f"{path} not available")
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# RAS must not satisfy a blocking field
# --------------------------------------------------------------------------------------


def test_ras_never_answers_a_blocking_field() -> None:
    """`RAS` means the clinician looked and found nothing to report. That is information
    about a symptom and nonsense about a histology — there is no tumour with no
    histological type."""
    assert satisfies_blocking("histology", "RAS") is False
    assert satisfies_blocking("gleason", "RAS") is False
    assert satisfies_blocking("histology", "") is False
    assert satisfies_blocking("histology", None) is False

    assert satisfies_blocking("histology", "Adénocarcinome acinaire") is True
    assert satisfies_blocking("gleason", "7 (3+4)") is True


def test_a_gleason_must_look_like_a_score() -> None:
    """A grade field holding prose is not a grade."""
    assert satisfies_blocking("gleason", "non évalué") is False
    assert satisfies_blocking("isup", "indéterminé") is False
    assert satisfies_blocking("isup", "2") is True


def test_a_real_dossier_of_ras_grades_cannot_be_analysed() -> None:
    """dossier8 records `Type histologique : RAS`, `Gleason : RAS` and `ISUP : RAS`. Every
    one was read as a confirmed value, so the analysis ran on a prostate case with no
    diagnosis and no grade."""
    report = assess(
        ledger(dossier("dossier8"), CancerType.prostate),
        cancer_type=CancerType.prostate,
    )

    assert report.can_run_analysis is False
    blocked = {issue.key for issue in report.blocking}
    assert {"histology", "gleason"} <= blocked


# --------------------------------------------------------------------------------------
# A report must stop at the next imaging study
# --------------------------------------------------------------------------------------


def test_a_modality_heading_ends_the_previous_report() -> None:
    assert starts_a_new_report("PET Choline") is True
    assert starts_a_new_report("IRM du bassin") is True
    assert starts_a_new_report("Scintigraphie osseuse") is True
    # Sub-headings inside a report are not studies and must not end it.
    assert starts_a_new_report("Technique") is False
    assert starts_a_new_report("Résultat") is False
    assert starts_a_new_report("Au total") is False


def test_the_prostate_mri_no_longer_swallows_the_next_study() -> None:
    """In dossier 6 it absorbed a PET Choline; in dossier 12 a spinal MRI, a pelvic MRI and
    a PET-PSMA, before truncating mid-report at the character cap."""
    for name in ("dossier6", "dossier12"):
        facts = {
            fact.key: fact
            for fact in resolve(
                extract_from_text(dossier(name)), cancer_type=CancerType.prostate
            )
        }
        mri = facts["prostate_mri"].value or ""
        assert "Choline" not in mri, name
        assert "PSMA" not in mri, name
        assert TRUNCATION_MARKER not in mri, name


def test_the_swallowed_studies_are_captured_as_their_own_fields() -> None:
    facts = {
        fact.key: fact
        for fact in resolve(
            extract_from_text(dossier("dossier12")), cancer_type=CancerType.prostate
        )
    }

    for key in ("pet_ct", "spinal_mri", "pelvic_mri"):
        assert facts.get(key) is not None and facts[key].value, key


def test_a_truncated_report_says_it_was_truncated() -> None:
    """Silence would present a partial report as a whole one."""
    long_report = "\n".join(["Résultat :", *["Ligne de compte rendu." * 20] * 40])
    facts = {
        fact.key: fact
        for fact in extract_from_text(f"TDM TAP :\n{long_report}\nHb : 12\n")
    }

    assert TRUNCATION_MARKER in (facts["ct_tap"].value or "")


# --------------------------------------------------------------------------------------
# An unapproved support threshold is a reading, not part of the care plan
# --------------------------------------------------------------------------------------


def test_a_triggered_support_item_is_marked_as_a_project_reading() -> None:
    """Hb 9.9 produces an item and Hb 10.0 produces none. That is a line the project drew,
    not a clinical distinction, so the item may not sit in the care plan unmarked."""
    items = suggested_items({"haemoglobin": "9.9"})

    assert len(items) == 1
    assert items[0].authority is Authority.project_suggestion
    assert SUGGESTION_CAVEAT in (items[0].caveat or "")


def test_a_project_reading_cannot_carry_no_caveat() -> None:
    """Enforced by the model, so no rule can emit one silently."""
    with pytest.raises(ValueError, match="canonical caveat"):
        SupportItem(
            title="Transfusion sanguine",
            detail="...",
            trigger="Hb : 9.9 g/dL",
            authority=Authority.project_suggestion,
        )


def test_an_item_triggered_by_a_value_must_declare_itself() -> None:
    with pytest.raises(ValueError, match="must say so"):
        SupportItem(title="Transfusion", detail="...", trigger="Hb : 9.9 g/dL")


def test_standing_care_and_project_readings_stay_apart() -> None:
    """Interleaving them would let an unapproved threshold read as part of the plan."""
    with pytest.raises(ValueError, match="belongs in `suggested`"):
        SupportCare(
            status="...",
            items=[
                SupportItem(
                    title="Transfusion sanguine",
                    detail="...",
                    trigger="Hb : 9.9 g/dL",
                    caveat=SUGGESTION_CAVEAT,
                    authority=Authority.project_suggestion,
                )
            ],
        )


def test_standing_items_rest_on_no_threshold() -> None:
    for item in STANDING:
        assert item.authority is Authority.none
        assert item.trigger is None


# --------------------------------------------------------------------------------------
# A grade must be a grade
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["2024", "score inconnu 7", "7 (5+5)", "non evalue", "RAS", "", "0", "11"],
)
def test_a_gleason_that_is_not_a_gleason_is_refused(value: str) -> None:
    """Testing only for a digit accepted a year as a grade, and `7 (5+5)` is internally
    inconsistent — 5+5 is 10, not 7."""
    assert satisfies_blocking("gleason", value) is False


@pytest.mark.parametrize("value", ["7 (3+4)", "9 (5+4)", "7(4+3)", "6", "3+4=7", "8 (4+4)"])
def test_a_real_gleason_is_accepted(value: str) -> None:
    assert satisfies_blocking("gleason", value) is True


@pytest.mark.parametrize("value", ["12", "0", "6", "RAS", "inconnu"])
def test_an_isup_outside_one_to_five_is_refused(value: str) -> None:
    assert satisfies_blocking("isup", value) is False


@pytest.mark.parametrize("value", ["1", "2", "5", "ISUP 3"])
def test_a_real_isup_is_accepted(value: str) -> None:
    assert satisfies_blocking("isup", value) is True


# --------------------------------------------------------------------------------------
# A modality that ends a report must also be captured
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    ["PET Choline", "PET PSMA", "TEP PSMA", "TEP-PSMA", "TEP TDM", "PET-scan"],
)
def test_every_pet_heading_is_both_a_boundary_and_a_field(heading: str) -> None:
    """`TEP PSMA` ended the previous report through the generic modality rule and matched
    no field, so its content was discarded entirely — worse than the bug it replaced."""
    assert starts_a_new_report(heading) is True

    facts = {
        fact.key: fact
        for fact in extract_from_text(f"{heading} :\nHyperfixation suspecte.\nHb : 12\n")
    }
    assert facts.get("pet_ct") is not None, heading
    assert "Hyperfixation" in (facts["pet_ct"].value or "")


def test_no_modality_heading_in_the_real_dossiers_is_silently_dropped() -> None:
    """Turns the label list from whack-a-mole into a detectable gap: any modality heading
    that ends a report must also be recognised by some field, or its content vanishes."""
    from rcpia.cases.extraction import (
        LABEL_INDEX,
        LABEL_LINE,
        modality_field,
        normalize_label,
    )

    unmatched: set[str] = set()
    for path in sorted(DOSSIERS.glob("dossier*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = LABEL_LINE.match(line)
            if match is None:
                continue
            label = match.group("label")
            if (
                starts_a_new_report(label)
                and normalize_label(label) not in LABEL_INDEX
                and modality_field(label) is None
            ):
                unmatched.add(normalize_label(label))

    assert not unmatched, f"modality headings that end a report but match no field: {unmatched}"
