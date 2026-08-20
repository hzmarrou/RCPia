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
