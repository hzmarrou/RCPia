"""Choosing a nomogram version, and refusing to run one.

The coefficients are not implemented — deliberately, and the tests say so. What *is* built is
the part that decides which published model a dossier supports, and the part that declines
rather than approximates. Both are safety properties: the clinician's requirement is that the
model must not estimate a risk instead of calling the validated calculator, and a calculator
that quietly falls back to a coarser version is doing the same thing one level down.
"""

import pytest

from rcpia.tools.base import Refusal, refuse, select_version
from rcpia.tools.briganti import (
    BRIGANTI,
    VERSIONS,
    index_lesion_diameter,
    selected_version,
)

# A dossier carrying systematic-biopsy data only — no mpMRI measurements.
SYSTEMATIC = {
    "psa_total": "7.4",
    "isup": "2",
    "ctnm": "cT2a",
    "cores_positive": "4",
    "cores_total": "12",
}
# The same patient with the imaging the later versions need. Briganti 2023 additionally
# requires a negative PSMA PET/CT, which is a condition on the patient rather than an input.
WITH_MRI = {**SYSTEMATIC, "mri_index_lesion_mm": "14", "targeted_cores_positive": "3"}
WITH_PSMA = {**WITH_MRI, "psma_pet_result": "negative"}


# --------------------------------------------------------------------------------------
# Version selection
# --------------------------------------------------------------------------------------


def test_the_newest_version_the_dossier_supports_is_chosen() -> None:
    assert selected_version(WITH_PSMA) == "2023"


def test_a_dossier_without_imaging_falls_back_rather_than_guessing() -> None:
    """**Not the newest version outright.** Briganti 2023 takes inputs 2012 does not, and
    running it with a guessed lesion diameter would give a number that looks more modern and
    is less true."""
    assert selected_version(SYSTEMATIC) == "2012"


def test_partial_imaging_selects_the_version_it_actually_supports() -> None:
    """mpMRI without targeted biopsy is 2019, not 2023."""
    facts = {**SYSTEMATIC, "mri_index_lesion_mm": "14"}
    assert selected_version(facts) == "2019"


def test_ras_is_not_a_value() -> None:
    """`RAS` is the clinician writing "nothing to report". For a measurement that means the
    test was not done — not that the result was zero — so it cannot satisfy an input."""
    facts = {**WITH_PSMA, "mri_index_lesion_mm": "RAS"}
    assert selected_version(facts) == "2012"


def test_a_dossier_missing_a_core_input_supports_no_version() -> None:
    facts = {k: v for k, v in SYSTEMATIC.items() if k != "psa_total"}
    assert selected_version(facts) is None


# --------------------------------------------------------------------------------------
# Refusing
# --------------------------------------------------------------------------------------


def test_a_refusal_names_what_is_missing() -> None:
    """"Cannot compute" is not useful. "Cannot compute, needs the PSA" is an action."""
    facts = {k: v for k, v in SYSTEMATIC.items() if k != "psa_total"}
    result = BRIGANTI.compute(facts)

    assert isinstance(result, Refusal)
    assert "psa_total" in result.missing
    assert "psa_total" in result.reason


def test_a_refusal_says_which_version_each_missing_fact_would_unlock() -> None:
    """That turns a gap into a decision the RCP can act on: add the mpMRI lesion diameter and
    Briganti 2023 becomes available for this patient."""
    facts = {k: v for k, v in SYSTEMATIC.items() if k != "psa_total"}
    result = BRIGANTI.compute(facts)

    assert isinstance(result, Refusal)
    assert result.unlocks["mri_index_lesion_mm"] == ("2019", "2023")
    assert result.unlocks["targeted_cores_positive"] == ("2023",)


def test_a_refusal_reports_the_nearest_version_not_the_newest() -> None:
    """Listing everything 2023 needs would bury the one fact that unlocks anything."""
    facts = {k: v for k, v in SYSTEMATIC.items() if k != "psa_total"}
    result = BRIGANTI.compute(facts)

    assert isinstance(result, Refusal)
    # 2012 is one fact away; 2023 is three. The refusal names the one.
    assert result.missing == ("psa_total",)


# --------------------------------------------------------------------------------------
# What is deliberately not built
# --------------------------------------------------------------------------------------


def test_computing_refuses_loudly_rather_than_approximating() -> None:
    """**The coefficients are not implemented, and that failure is loud.**

    Returning an approximation would be worse than returning nothing: a plausible probability
    carrying a real version label, which nobody downstream could question. A nomogram is a
    published regression with specific weights; anything else wearing its name is a guess.
    """
    with pytest.raises(NotImplementedError, match="coefficients are not implemented"):
        BRIGANTI.compute(WITH_PSMA)


def test_grade_is_required_in_one_notation_only() -> None:
    """**Gleason and ISUP are the same fact**, and asking different versions for different
    notations broke the fallback: a dossier recording only ISUP supported 2023 and not 2012,
    so losing an imaging field would leave it with nothing rather than with the older model.

    Nothing derives one from the other yet, so a Gleason-only dossier supports no version and
    the refusal will name `isup` while the grade is in fact recorded. That derivation belongs
    in the ledger.
    """
    for version in VERSIONS:
        assert "gleason" not in version.required
        assert "isup" in version.required

    gleason_only = {**SYSTEMATIC, "gleason": "3+4"}
    del gleason_only["isup"]
    assert selected_version(gleason_only) is None


def test_every_version_declares_the_facts_it_needs() -> None:
    """A version with no requirements would be selectable by any dossier at all."""
    for version in VERSIONS:
        assert version.required, f"{version.version} declares no required facts"


def test_later_versions_do_not_ask_for_less() -> None:
    """Each version adds inputs rather than replacing them, so falling back to an earlier one
    is always possible when the newer inputs are absent."""
    by_year = sorted(VERSIONS, key=lambda v: v.published)
    for earlier, later in zip(by_year, by_year[1:], strict=False):
        assert earlier.required <= later.required, (
            f"{later.version} drops inputs {earlier.version} required, so a dossier could "
            "support the newer model and not the older one"
        )


def test_selection_returns_none_rather_than_a_default() -> None:
    """A default version would run on a dossier that supports none of them."""
    empty: dict[str, str] = {}
    assert select_version(VERSIONS, empty) is None
    assert isinstance(refuse("x", VERSIONS, empty), Refusal)


# --------------------------------------------------------------------------------------
# Preconditions — a model can be inapplicable with every input present
# --------------------------------------------------------------------------------------


def test_briganti_2023_needs_a_negative_psma_pet_not_merely_the_inputs() -> None:
    """**Dossier 7 is this case exactly.**

    Its mpMRI gives a 15.6 mm PI-RADS 5 index lesion and its staging is CT plus bone
    scintigraphy — no PSMA PET at all. Every number Briganti 2023 wants is present, and the
    model still does not apply, because it is validated on patients with a negative PSMA
    PET/CT. Running it anyway returns a probability drawn from a population this patient is
    not in, and nothing about that number would look wrong.
    """
    assert selected_version(WITH_MRI) == "2019"
    assert selected_version(WITH_PSMA) == "2023"


def test_a_positive_psma_pet_does_not_satisfy_the_condition() -> None:
    """"Negative" is the requirement, not "performed". A positive scan has already shown
    nodal disease, so the probability of nodal disease is no longer the question."""
    facts = {**WITH_MRI, "psma_pet_result": "positive"}
    assert selected_version(facts) == "2019"


def test_an_uninterpretable_psma_result_fails_the_condition() -> None:
    """Unknown is not satisfied. A model that runs whenever we fail to prove it should not is
    a model that runs by default."""
    facts = {**WITH_MRI, "psma_pet_result": "hyperfixation douteuse en iliaque externe"}
    assert selected_version(facts) == "2019"


def test_a_refusal_separates_a_missing_input_from_an_inapplicable_model() -> None:
    """They ask for opposite things. A missing input asks the RCP for a measurement; an unmet
    precondition says this model is the wrong instrument for this patient."""
    facts = {k: v for k, v in WITH_MRI.items() if k != "psa_total"}
    result = BRIGANTI.compute(facts)

    assert isinstance(result, Refusal)
    assert result.missing == ("psa_total",)
    assert any("PSMA" in note for note in result.inapplicable)
    assert "does not apply" in result.reason


def test_the_refusal_asks_only_for_data_a_usable_model_would_need() -> None:
    """Naming an input for a model the patient is outside anyway sends the RCP to order a
    scan for nothing."""
    facts = {k: v for k, v in SYSTEMATIC.items() if k != "psa_total"}
    result = BRIGANTI.compute(facts)

    assert isinstance(result, Refusal)
    assert result.missing == ("psa_total",)


# --------------------------------------------------------------------------------------
# The index lesion
# --------------------------------------------------------------------------------------


def test_the_index_lesion_is_the_highest_pi_rads_not_the_largest() -> None:
    """Dossier 7: target 1 at PI-RADS 5 / 15.6 mm, target 2 at PI-RADS 2 / 7 mm."""
    assert index_lesion_diameter([(5, 15.6), (2, 7.0)]) == 15.6


def test_a_larger_lesion_of_lower_pi_rads_is_not_the_index_lesion() -> None:
    """Size decides only among lesions sharing the highest score. Taking the largest outright
    would feed the model a benign-looking nodule instead of the suspicious one."""
    assert index_lesion_diameter([(5, 9.0), (3, 22.0)]) == 9.0


def test_ties_on_pi_rads_are_broken_by_size() -> None:
    assert index_lesion_diameter([(5, 9.0), (5, 14.0)]) == 14.0


def test_no_reported_lesion_yields_nothing_rather_than_zero() -> None:
    """Zero millimetres is a measurement. No lesion reported is not."""
    assert index_lesion_diameter([]) is None
