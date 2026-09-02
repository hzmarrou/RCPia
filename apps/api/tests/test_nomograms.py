"""Nomograms: what they compute, and what they refuse.

**The reference vectors are the point of this file.** Two patients were entered on
mskcc.org/nomograms/prostate/pre_op and their outputs recorded; the same two patients run
through this code must reproduce them. Coefficient transcription was verified separately and
was not enough on its own -- a first version of the PSA spline divided both terms by
`(k4-k1)²`, a conventional scaling these coefficients were not fitted against, and it drove
one estimate to 99.98% where the calculator says 93%. Every constant was correct; the assembly
was not. Only the vectors caught it.
"""

import pytest

from rcpia.nomograms import briganti, mskcc
from rcpia.nomograms.contract import Reason, Status
from rcpia.nomograms.record import ClinicalRecord, Tristate, record_from_facts
from rcpia.nomograms.service import evaluate_all

ELIGIBLE = {"hormone_therapy": Tristate.no, "radiation_therapy": Tristate.no}

# Entered on the MSKCC calculator; outputs recorded from the results page.
CASE_A = ClinicalRecord(
    age_years=70, psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
    clinical_t_stage_dre="T2a", systematic_positive_cores=5, systematic_total_cores=21,
    **ELIGIBLE,
)
CASE_A_SITE = {
    "ocd_percent": 59, "ece_percent": 40, "lni_percent": 3, "svi_percent": 2,
    "progression_free_5y_percent": 78,
}
# The same patient shape without core counts, which selects the other coefficient set.
CASE_B = ClinicalRecord(
    age_years=72, psa_ng_ml=34.25, gleason_primary=4, gleason_secondary=3,
    clinical_t_stage_dre="T2b", **ELIGIBLE,
)
CASE_B_SITE = {
    "ocd_percent": 5, "ece_percent": 93, "lni_percent": 51, "svi_percent": 51,
    "progression_free_5y_percent": 14,
}


# --------------------------------------------------------------------------------------
# MSKCC reference vectors
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "expected", "variant"),
    [(CASE_A, CASE_A_SITE, "cores"), (CASE_B, CASE_B_SITE, "full")],
)
def test_mskcc_reproduces_the_published_calculator(record, expected, variant) -> None:
    result = mskcc.evaluate(record)

    assert result.status is Status.calculated
    assert result.inputs_used["model_variant"] == variant
    for endpoint, site_value in expected.items():
        assert round(result.estimates[endpoint]) == site_value, endpoint


def test_the_two_coefficient_sets_are_genuinely_different() -> None:
    """Supplying core counts selects a different published model, not a refinement of one.

    Guards the variant switch: if both branches silently used the same coefficients the
    vectors above would still pass, because each was recorded under one branch only.
    """
    with_cores = mskcc.evaluate(CASE_A)
    without = mskcc.evaluate(
        ClinicalRecord(
            age_years=70, psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
            clinical_t_stage_dre="T2a", **ELIGIBLE,
        )
    )

    assert with_cores.estimates["lni_percent"] != without.estimates["lni_percent"]


def test_the_unreliable_endpoints_are_absent() -> None:
    """Cancer-specific survival and the ten-year figure are not published here.

    Ours gave 0.14% where the calculator says 2%, and 4.3% where it says 42% -- the horizon is
    fifteen years and the implementation evaluated it at five. Absent beats wrong by a factor
    of ten, and neither is an output the clinician named as used.
    """
    estimates = mskcc.evaluate(CASE_A).estimates

    assert not any("death" in key or "survival" in key for key in estimates)
    assert not any("10y" in key for key in estimates)


# --------------------------------------------------------------------------------------
# Eligibility: unknown is not "no"
# --------------------------------------------------------------------------------------


def test_unknown_prior_therapy_blocks_rather_than_assuming_none() -> None:
    """MSKCC's own form asks both questions explicitly and disqualifies on either. A dossier
    silent on both cannot be shown to be eligible, and reading silence as "no" is the "never
    use default values for missing clinical data" rule failing without a sound."""
    silent = ClinicalRecord(
        age_years=70, psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
        clinical_t_stage_dre="T2a", systematic_positive_cores=5, systematic_total_cores=21,
    )
    result = mskcc.evaluate(silent)

    assert result.status is Status.not_possible
    assert Reason.eligibility_unconfirmed in result.reason_codes
    assert set(result.missing_fields) == {"hormone_therapy", "radiation_therapy"}


def test_declared_prior_therapy_makes_the_model_inapplicable() -> None:
    """Not a gap to fill: no further data makes this patient one the model was fitted on."""
    treated = ClinicalRecord(
        age_years=70, psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
        clinical_t_stage_dre="T2a", hormone_therapy=Tristate.yes,
        radiation_therapy=Tristate.no,
    )
    result = mskcc.evaluate(treated)

    assert result.status is Status.not_applicable
    assert Reason.model_eligibility_not_met in result.reason_codes


# --------------------------------------------------------------------------------------
# Briganti: pathway selection, never fallback
# --------------------------------------------------------------------------------------


def test_briganti_2012_matches_the_assignment_acceptance_value() -> None:
    """Dossier 3 in the implementation assignment: approximately 3.06%."""
    result = briganti.evaluate(CASE_A)

    assert result.model_id == "briganti_2012"
    assert result.estimates["lymph_node_invasion_percent"] == pytest.approx(3.06, abs=0.01)


def test_the_two_gleason_sevens_do_not_give_the_same_risk() -> None:
    """3+4 and 4+3 sum alike and weigh differently. The assignment's dossier 7 expectation was
    once 20.41%, which is what you get by firing the secondary-pattern indicator for a 4+3."""
    four_three = briganti.evaluate(
        ClinicalRecord(
            psa_ng_ml=7.53, gleason_primary=4, gleason_secondary=3,
            clinical_t_stage_dre="T2a", systematic_positive_cores=5,
            systematic_total_cores=21,
        )
    )
    three_four = briganti.evaluate(CASE_A)

    assert (
        four_three.estimates["lymph_node_invasion_percent"]
        > three_four.estimates["lymph_node_invasion_percent"]
    )


def test_a_psma_patient_is_reported_as_2023_not_computed_as_2012() -> None:
    """**Pathway selection, not "the newest version whose inputs are present".** Falling back
    would answer a different question and label it with a real model's name."""
    psma = ClinicalRecord(
        psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4, clinical_t_stage_dre="T2a",
        systematic_positive_cores=5, systematic_total_cores=21,
        psma_pet_performed=Tristate.yes,
    )
    result = briganti.evaluate(psma)

    assert result.model_id == "briganti_2023"
    assert result.status is Status.not_possible
    assert result.estimates == {}


def test_a_targeted_biopsy_patient_is_reported_as_2019() -> None:
    targeted = ClinicalRecord(
        psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4, clinical_t_stage_dre="T2a",
        systematic_positive_cores=5, systematic_total_cores=21,
        mri_targeted_biopsy_performed=Tristate.yes,
    )

    assert briganti.evaluate(targeted).model_id == "briganti_2019"


def test_documented_nodal_disease_is_not_applicable_rather_than_missing() -> None:
    node_positive = ClinicalRecord(
        psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4, clinical_t_stage_dre="T2a",
        systematic_positive_cores=5, systematic_total_cores=21,
        known_nodal_metastasis=Tristate.yes,
    )
    result = briganti.evaluate(node_positive)

    assert result.status is Status.not_applicable
    assert Reason.known_nodal_metastasis in result.reason_codes


def test_a_missing_stage_names_itself() -> None:
    """"Cannot compute" tells an RCP nothing; naming the field tells them what to write."""
    result = briganti.evaluate(
        ClinicalRecord(
            psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
            systematic_positive_cores=5, systematic_total_cores=21,
        )
    )

    assert result.status is Status.not_possible
    assert "clinical_t_stage_dre" in result.missing_fields


def test_a_stage_outside_the_model_is_inapplicable() -> None:
    """The published stage term has three levels; cT4 has none."""
    result = briganti.evaluate(
        ClinicalRecord(
            psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
            clinical_t_stage_dre="T4", systematic_positive_cores=5, systematic_total_cores=21,
        )
    )

    assert result.status is Status.not_applicable


# --------------------------------------------------------------------------------------
# Validation and normalisation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [
        ClinicalRecord(age_years=12, psa_ng_ml=7.5, gleason_primary=3, gleason_secondary=4,
                       clinical_t_stage_dre="T2a"),
        ClinicalRecord(psa_ng_ml=-1, gleason_primary=3, gleason_secondary=4,
                       clinical_t_stage_dre="T2a"),
        ClinicalRecord(psa_ng_ml=7.5, gleason_primary=3, gleason_secondary=4,
                       clinical_t_stage_dre="T2a", systematic_positive_cores=9,
                       systematic_total_cores=4),
        ClinicalRecord(psa_ng_ml=7.5, gleason_primary=3, gleason_secondary=4,
                       clinical_t_stage_dre="T2a", systematic_positive_cores=1,
                       systematic_total_cores=0),
    ],
)
def test_invalid_values_are_rejected_not_clamped(record) -> None:
    result = briganti.evaluate(record)

    assert result.status is Status.not_possible
    assert Reason.invalid_input_range in result.reason_codes


def test_ras_is_not_a_measurement() -> None:
    """`RAS` is the clinician writing "nothing to report" -- for a measurement that means the
    test was not done, not that the result was zero."""
    record = record_from_facts({"psa_total": "RAS", "age": "70 ans", "gleason": "7 (3+4)"})

    assert record.psa_ng_ml is None
    assert record.age_years == 70


def test_the_ledger_is_parsed_once_and_typed() -> None:
    record = record_from_facts(
        {
            "age": "72 ans", "psa_total": "34,25", "gleason": "7 (4+3)",
            "ct_dre": "T2b", "cores_positive": "3", "cores_total": "17",
        }
    )

    assert (record.age_years, record.psa_ng_ml) == (72, 34.25)
    assert (record.gleason_primary, record.gleason_secondary) == (4, 3)
    assert record.gleason_grade_group == 3
    assert record.clinical_t_stage_dre == "T2b"
    assert record.percent_positive_cores == pytest.approx(17.647, abs=0.001)


def test_grade_group_distinguishes_the_two_sevens() -> None:
    assert record_from_facts({"gleason": "7 (3+4)"}).gleason_grade_group == 2
    assert record_from_facts({"gleason": "7 (4+3)"}).gleason_grade_group == 3


def test_a_stage_string_without_n1_is_evidence_of_absence() -> None:
    """A recorded stage that does not mention N1 says something; no stage says nothing."""
    assert record_from_facts({"ctnm": "T2a N0 M0"}).known_nodal_metastasis is Tristate.no
    assert record_from_facts({}).known_nodal_metastasis is Tristate.unknown


# --------------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------------


def test_bladder_gets_no_prostate_nomogram() -> None:
    """Briganti and the MSKCC prostate models have nothing to say about a bladder tumour."""
    assert evaluate_all({"psa_total": "7.5"}, cancer_type="bladder") == ()


def test_every_model_answers_including_the_ones_that_refuse() -> None:
    """A fiche showing only the models that produced a number would silently drop the sentence
    telling the RCP what to obtain."""
    results = evaluate_all(
        {"age": "70", "psa_total": "7.53", "gleason": "7 (3+4)", "ct_dre": "T2a",
         "cores_positive": "5", "cores_total": "21"},
        cancer_type="prostate",
    )

    assert {r.model_family for r in results} == {"briganti", "mskcc"}
    assert all(r.explain() for r in results)
