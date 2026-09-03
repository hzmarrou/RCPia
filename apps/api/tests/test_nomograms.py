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

# Shorthand for "nothing here should block a calculation": confirmed no prior therapy, and
# confirmed adenocarcinoma. Every reference-vector and arithmetic test below uses this, so
# the histology gate added for finding 3 does not turn every one of them into a fixture for
# that gate instead of for what it actually tests.
ELIGIBLE = {
    "hormone_therapy": Tristate.no,
    "radiation_therapy": Tristate.no,
    "is_prostate_adenocarcinoma": Tristate.yes,
}

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
        is_prostate_adenocarcinoma=Tristate.yes,
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
            systematic_total_cores=21, is_prostate_adenocarcinoma=Tristate.yes,
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


# --------------------------------------------------------------------------------------
# Histology: confirmed adenocarcinoma to calculate, unknown to ask, confirmed other to refuse
# --------------------------------------------------------------------------------------
#
# Finding 3 from the PR review: neither calculator inspected histology at all, so a prostate
# case with confirmed neuroendocrine or small-cell disease and otherwise complete numeric
# inputs received `CALCULATED` -- a number from a model validated only on adenocarcinoma.
# `applicability`/`eligibility` already distinguish "outside the model's population"
# (`NOT_APPLICABLE`) from "an input is missing" (`NOT_POSSIBLE`); histology needed to be
# routed into that same distinction rather than being ignored.


def test_a_named_other_histology_is_not_applicable_for_briganti() -> None:
    """Confirmed non-adenocarcinoma is not a data gap: no measurement makes this model fit a
    tumour it was never validated on."""
    other_histology = ClinicalRecord(
        psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4, clinical_t_stage_dre="T2a",
        systematic_positive_cores=5, systematic_total_cores=21,
        is_prostate_adenocarcinoma=Tristate.no,
    )
    result = briganti.evaluate(other_histology)

    assert result.status is Status.not_applicable
    assert Reason.not_prostate_adenocarcinoma in result.reason_codes


def test_unknown_histology_is_not_possible_for_briganti_not_calculated() -> None:
    """The defect, as a regression test: complete numeric inputs and unrecorded histology used
    to reach `CALCULATED`. Unknown must ask, not compute."""
    unrecorded_histology = ClinicalRecord(
        psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4, clinical_t_stage_dre="T2a",
        systematic_positive_cores=5, systematic_total_cores=21,
    )
    result = briganti.evaluate(unrecorded_histology)

    assert result.status is Status.not_possible
    assert Reason.eligibility_unconfirmed in result.reason_codes
    assert "is_prostate_adenocarcinoma" in result.missing_fields


def test_confirmed_adenocarcinoma_still_calculates_for_briganti() -> None:
    """The gate does not turn eligible patients away: this is `CASE_A` in all but histology."""
    confirmed = ClinicalRecord(
        psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4, clinical_t_stage_dre="T2a",
        systematic_positive_cores=5, systematic_total_cores=21,
        is_prostate_adenocarcinoma=Tristate.yes,
    )
    result = briganti.evaluate(confirmed)

    assert result.status is Status.calculated


def test_a_named_other_histology_is_not_applicable_for_mskcc() -> None:
    other_histology = ClinicalRecord(
        age_years=70, psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
        clinical_t_stage_dre="T2a", systematic_positive_cores=5, systematic_total_cores=21,
        hormone_therapy=Tristate.no, radiation_therapy=Tristate.no,
        is_prostate_adenocarcinoma=Tristate.no,
    )
    result = mskcc.evaluate(other_histology)

    assert result.status is Status.not_applicable
    assert Reason.not_prostate_adenocarcinoma in result.reason_codes


def test_unknown_histology_is_not_possible_for_mskcc_not_calculated() -> None:
    unrecorded_histology = ClinicalRecord(
        age_years=70, psa_ng_ml=7.53, gleason_primary=3, gleason_secondary=4,
        clinical_t_stage_dre="T2a", systematic_positive_cores=5, systematic_total_cores=21,
        hormone_therapy=Tristate.no, radiation_therapy=Tristate.no,
    )
    result = mskcc.evaluate(unrecorded_histology)

    assert result.status is Status.not_possible
    assert Reason.eligibility_unconfirmed in result.reason_codes
    assert "is_prostate_adenocarcinoma" in result.missing_fields


def test_classify_prostate_histology_matches_only_named_terms() -> None:
    """Unmatched text must stay `unknown`, never fall through to `no`.

    A classifier that guessed `no` for anything it did not recognise would report a real
    adenocarcinoma phrased unusually as an inapplicable histology -- the opposite failure,
    and just as unsafe as the one this gate exists to catch.
    """
    from rcpia.nomograms.record import classify_prostate_histology

    assert classify_prostate_histology("Adénocarcinome acinaire, Gleason 3+4") is Tristate.yes
    assert classify_prostate_histology("adenocarcinome") is Tristate.yes
    assert classify_prostate_histology("Carcinome neuroendocrine à petites cellules") is (
        Tristate.no
    )
    assert classify_prostate_histology("Carcinome épidermoïde") is Tristate.no
    assert classify_prostate_histology("Sarcome prostatique") is Tristate.no
    assert classify_prostate_histology("Compte-rendu en cours de relecture") is Tristate.unknown
    assert classify_prostate_histology(None) is Tristate.unknown
    assert classify_prostate_histology("RAS") is Tristate.unknown


def test_a_negated_exclusion_stays_unknown_not_no() -> None:
    """The finding this test exists for: a report naming a histology only to rule it out
    was read the same as one confirming it. "Absence de composante neuroendocrine" and
    "adénocarcinome" are opposite findings and were both routed to the same conclusion.

    Unknown, not `no`, because ruling out *this* histology does not by itself confirm
    adenocarcinoma -- the report may simply not say. Treating a ruled-out exclusion as
    confirmed adenocarcinoma would be a different, equally unsafe guess in the other
    direction.
    """
    from rcpia.nomograms.record import classify_prostate_histology

    assert classify_prostate_histology(
        "Absence de composante neuroendocrine"
    ) is Tristate.unknown
    assert classify_prostate_histology(
        "Pas de composante sarcomatoïde"
    ) is Tristate.unknown
    assert classify_prostate_histology(
        "Composante neuroendocrine non retrouvée"
    ) is Tristate.unknown
    assert classify_prostate_histology(
        "Sans contingent urothélial"
    ) is Tristate.unknown


def test_adenocarcinoma_confirmed_alongside_a_ruled_out_histology_is_still_yes() -> None:
    """A real pathology report shape: the tumour is named, and a differential is excluded
    in the same sentence. The exclusion must not shadow the positive finding."""
    from rcpia.nomograms.record import classify_prostate_histology

    assert classify_prostate_histology(
        "Adénocarcinome acinaire, sans contingent neuroendocrine"
    ) is Tristate.yes


def test_a_real_exclusion_in_a_later_clause_still_returns_no() -> None:
    """Negation is checked per clause, not by scanning the whole report for any negation
    word: a ruled-out histology in one sentence must not suppress a genuine exclusion
    named in another."""
    from rcpia.nomograms.record import classify_prostate_histology

    assert classify_prostate_histology(
        "Absence de composante sarcomatoïde. Carcinome épidermoïde confirmé."
    ) is Tristate.no


def test_a_denied_adenocarcinoma_is_not_read_as_confirmed() -> None:
    """Re-review finding: the first negation fix only checked the exclusion list. A denied
    adenocarcinoma finding named exactly the term this classifier looks for and was read as
    confirming it -- the more dangerous direction to get wrong, since it is what feeds a
    nomogram a number for a cancer the report explicitly says the patient does not have.

    Verified end to end at the time this was found: `Briganti.evaluate` and `mskcc.evaluate`
    both returned `CALCULATED` for a record built from this exact sentence.
    """
    from rcpia.nomograms.record import classify_prostate_histology

    result = classify_prostate_histology("Absence d'adénocarcinome prostatique.")

    assert result is not Tristate.yes
    assert result is Tristate.unknown


def test_a_comma_joined_denial_does_not_suppress_an_unrelated_confirmation() -> None:
    """Re-review finding: a clause was split only on sentence terminators, so a negation for
    one finding, comma-joined to a confirmed and unrelated second finding in what reads as
    one sentence, suppressed the second finding along with the first."""
    from rcpia.nomograms.record import classify_prostate_histology

    result = classify_prostate_histology(
        "Pas d'emboles vasculaires, carcinome neuroendocrine confirmé"
    )

    assert result is Tristate.no


def test_the_two_findings_do_not_bleed_into_each_other_either_direction() -> None:
    """The comma split cuts both ways: a confirmed finding, comma-joined to an unrelated
    denial, must not itself be suppressed."""
    from rcpia.nomograms.record import classify_prostate_histology

    result = classify_prostate_histology(
        "Adénocarcinome acinaire confirmé, pas d'emboles vasculaires"
    )

    assert result is Tristate.yes


def test_a_dossier_denying_adenocarcinoma_does_not_reach_calculated() -> None:
    """The end-to-end reproduction from the re-review, run against both calculators: a
    dossier whose pathology report reads `record_from_facts` into a denied-adenocarcinoma
    histology, with otherwise complete inputs, must not receive a computed score from
    either. Before this fix both did."""
    record = record_from_facts(
        {
            "histology": "Absence d'adénocarcinome prostatique.",
            "age": "70", "psa_total": "7.53", "gleason": "7 (3+4)", "ct_dre": "T2a",
            "cores_positive": "5", "cores_total": "21",
            "hormone_therapy": "Non", "radiation_therapy": "Non",
        }
    )
    assert record.is_prostate_adenocarcinoma is Tristate.unknown

    assert briganti.evaluate(record).status is not Status.calculated
    assert mskcc.evaluate(record).status is not Status.calculated


def test_a_postposed_denial_of_adenocarcinoma_does_not_reach_calculated() -> None:
    """Fourth re-review finding, and the exact end-to-end regression it asked for.

    Splitting on commas fixed the previous two counterexamples and reintroduced this one:
    "adénocarcinome, non retrouvé" denies the same finding it names, after a pause, and the
    comma between them does not make it a second, independent finding. Splitting on the
    comma cut the term away from the negation describing it, the mirror image of the defect
    the comma split was added to fix. Before this fix both calculators returned `CALCULATED`.
    """
    record = record_from_facts(
        {
            "histology": "Adénocarcinome, non retrouvé.",
            "age": "70", "psa_total": "7.53", "gleason": "7 (3+4)", "ct_dre": "T2a",
            "cores_positive": "5", "cores_total": "21",
            "hormone_therapy": "Non", "radiation_therapy": "Non",
        }
    )
    assert record.is_prostate_adenocarcinoma is Tristate.unknown

    assert briganti.evaluate(record).status is not Status.calculated
    assert mskcc.evaluate(record).status is not Status.calculated


def test_a_postposed_denial_of_an_exclusion_stays_unknown_not_no() -> None:
    """The same defect's mirror image on the exclusion side: "composante neuroendocrine,
    non retrouvée" denies the exclusion term it names, and was read as confirming it."""
    from rcpia.nomograms.record import classify_prostate_histology

    result = classify_prostate_histology("Composante neuroendocrine, non retrouvée.")

    assert result is Tristate.unknown


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
            is_prostate_adenocarcinoma=Tristate.yes,
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


# --------------------------------------------------------------------------------------
# Reaching the agents
# --------------------------------------------------------------------------------------


def test_the_bundle_carries_the_calculators() -> None:
    """**The wiring, read off the code that builds the bundle.**

    A calculator no agent can reach is the fourth capability this project would have built,
    tested and left unconnected -- after the agent brains, the retriever and the narrative
    extraction pass, each of which cost weeks before anyone noticed. No behavioural test
    catches that shape, because every unit test calls the thing directly.
    """
    import inspect

    from rcpia.agents import bundle as bundle_module

    source = inspect.getsource(bundle_module.build_bundle)
    assert "evaluate_all(facts, cancer_type=" in source, (
        "build_bundle must run the calculators over the ledger"
    )
    assert "nomograms=nomograms" in source, "and hand the readings to the agent"


def test_the_agent_is_told_not_to_estimate_what_a_calculator_computed() -> None:
    """The clinician's requirement, verbatim: *nous voulons éviter que le modèle estime
    lui-même un risque sans utiliser le calculateur validé.* A figure in the bundle is not
    enough on its own -- a model handed a number and no instruction may still produce its
    own, and nothing downstream could tell which it reported."""
    import inspect

    from rcpia.agents import bundle as bundle_module

    source = inspect.getsource(bundle_module.build_bundle)
    assert "n'estimez jamais vous-même une probabilité" in source
    assert "une estimation de remplacement" in source, (
        "a refusal must also be reported rather than replaced by an estimate"
    )


def test_a_nomogram_id_is_not_mistaken_for_an_invented_citation() -> None:
    """`briganti_2012` reaching an agent's prose must not trip the citation checks: it is a
    computation over this patient's ledger, not a corpus passage to quote."""
    from rcpia.agents.schemas import citations_in_prose

    assert citations_in_prose("Briganti 2012 (briganti_2012) : 15,0 %") == set()
    assert citations_in_prose("mskcc_preoperative donne 2,8 %") == set()
