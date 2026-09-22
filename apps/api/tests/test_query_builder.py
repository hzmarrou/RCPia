"""Deriving a retrieval filter from a dossier.

**The dimension under test is `disease_setting`, because it is the one that can be wrong
dangerously.** A wrong therapy line returns weaker guidance; a wrong setting returns guidance
written for a different patient, with a real citation attached. So most of these tests are about
what the builder *refuses* to conclude.

Stage strings below are controlled test inputs, not verified reconstructions of patient
histories. Actual archived-record fidelity is evaluated separately.
"""

import pytest

from rcpia.query.builder import Fact, build_filter, derive_setting
from rcpia.query.staging import parse_gleason, parse_stage


def facts(**values: str | None) -> list[Fact]:
    return [Fact(key=k, value=v) for k, v in values.items()]


# --------------------------------------------------------------------------------------
# Parsing what a French dossier actually writes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "prefix", "t", "sub", "n", "m"),
    [
        ("cT4a N1 M1a", "c", "4", "a", "1", "1"),
        ("pT2 N0 M0", "p", "2", None, "0", "0"),
        ("cT1c N0 M0", "c", "1", "c", "0", "0"),
        ("pTa", "p", "a", None, None, None),
        ("T3a", None, "3", "a", None, None),
        ("pTis N0", "p", "is", None, "0", None),
    ],
)
def test_tnm_is_read_as_written(text, prefix, t, sub, n, m) -> None:
    stage = parse_stage(text)
    assert stage is not None
    assert (stage.prefix, stage.t, stage.t_sub, stage.n, stage.m) == (prefix, t, sub, n, m)


def test_text_with_no_tnm_yields_nothing_rather_than_a_default() -> None:
    assert parse_stage("Non renseigné") is None
    assert parse_stage("") is None
    assert parse_stage(None) is None


def test_gleason_is_summed_from_its_two_components() -> None:
    assert parse_gleason("4+3") == 7
    assert parse_gleason("Gleason 5 + 4 = 9") == 9
    assert parse_gleason("8") == 8
    assert parse_gleason("non fait") is None


# --------------------------------------------------------------------------------------
# The rule that outranks the others: never conclude from what is not there
# --------------------------------------------------------------------------------------


def test_a_dossier_with_no_stage_does_not_get_a_setting() -> None:
    """The failure this guards against is silent: a missing stage becoming "low risk"."""
    decision = derive_setting("prostate", facts(psa_total="6.2", isup="1"))
    assert decision.setting is None
    assert not decision.determined
    assert decision.missing == ("ctnm", "ct_dre")


def test_localized_prostate_without_grade_or_psa_falls_back_to_the_parent() -> None:
    """Any one of stage, grade and PSA can move a patient up a band, so a missing one cannot be
    read as favourable. The risk-unspecified parent is the honest answer, and `SETTING_PARENTS`
    means it still reaches the guidance written across the bands.
    """
    decision = derive_setting("prostate", facts(ctnm="cT2a N0 M0"))
    assert decision.setting == "prostate_nonmetastatic_unspecified_risk"
    assert "psa_total" in decision.missing


def test_a_gleason_of_seven_does_not_guess_the_grade_group() -> None:
    """3+4 is grade group 2 and 4+3 is grade group 3. A bare 7 cannot tell them apart, and
    guessing moves the patient a whole band.
    """
    decision = derive_setting("prostate", facts(ctnm="cT2a N0 M0", gleason="7", psa_total="6"))
    assert decision.setting == "prostate_nonmetastatic_unspecified_risk"


# --------------------------------------------------------------------------------------
# Clinical stage, never pathological, unless the question is post-operative
# --------------------------------------------------------------------------------------


def test_the_clinical_stage_is_used_and_the_pathological_one_ignored() -> None:
    """EAU §4.5 keeps them apart. Choosing whether to operate from the pathology of the
    operation is the circularity this project already names about the Briganti nomograms.
    """
    both = facts(ctnm="cT1 N0 M0", ptnm="pT2 N0 M0")
    assert derive_setting("bladder", both).setting == "bladder_nmibc_unspecified_risk"
    assert (
        derive_setting("bladder", both, post_operative=True).setting
        == "bladder_mibc_non_metastatic"
    )


def test_pathology_is_not_a_fallback_for_clinical_stage() -> None:
    decision = derive_setting("bladder", facts(ptnm="pT2 N0 M0"))
    assert decision.setting is None
    assert "not substituted" in decision.reason
    assert "ctnm" in decision.missing


# --------------------------------------------------------------------------------------
# Bladder
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("cTa N0 M0", "bladder_nmibc_unspecified_risk"),
        ("cT1 N0 M0", "bladder_nmibc_unspecified_risk"),
        ("cT2 N0 M0", "bladder_mibc_non_metastatic"),
        ("cT4a N0 M0", "bladder_mibc_non_metastatic"),
        ("cT4b N0 M0", "bladder_locally_advanced_non_metastatic"),
        ("cT3 N1 M0", "bladder_node_positive_non_metastatic"),
        ("cT4a N1 M1a", "bladder_metastatic"),
    ],
)
def test_bladder_settings_follow_muscle_invasion_then_nodes_then_metastases(
    stage: str, expected: str
) -> None:
    assert derive_setting("bladder", facts(ctnm=stage)).setting == expected


def test_recorded_cis_is_routed_to_its_own_setting() -> None:
    assert (
        derive_setting("bladder", facts(ctnm="cTa N0 M0", cis="Oui")).setting == "bladder_nmibc_cis"
    )


def test_the_nmibc_risk_band_is_not_invented() -> None:
    """The band needs grade, size, multifocality and prior history. The dossier form carries
    none of them as structured fields, so the parent is used rather than a guessed band.
    """
    decision = derive_setting("bladder", facts(ctnm="cT1 N0 M0"))
    assert decision.setting == "bladder_nmibc_unspecified_risk"
    assert "not derivable" in decision.reason


# --------------------------------------------------------------------------------------
# Prostate
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (dict(ctnm="cT1c N0 M0", isup="1", psa_total="6.2"), "prostate_localized_low_risk"),
        (dict(ctnm="cT2a N0 M0", isup="2", psa_total="7.5"),
         "prostate_localized_intermediate_risk"),
        (dict(ctnm="cT2a N0 M0", isup="1", psa_total="14"),
         "prostate_localized_intermediate_risk"),
        (dict(ctnm="cT2a N0 M0", isup="4", psa_total="8"), "prostate_localized_high_risk"),
        (dict(ctnm="cT2a N0 M0", isup="2", psa_total="34"), "prostate_localized_high_risk"),
        (dict(ctnm="cT2c N0 M0", isup="1", psa_total="6"), "prostate_localized_high_risk"),
        (dict(ctnm="cT3a N0 M0", isup="3", psa_total="34"),
         "prostate_locally_advanced_non_metastatic"),
        (dict(ctnm="cT2 N1 M0", isup="4", psa_total="30"),
         "prostate_node_positive_non_metastatic"),
        (dict(ctnm="cT3 N1 M1b", isup="5", psa_total="116"), None),
    ],
)
def test_prostate_risk_bands_follow_the_shared_definition(values: dict, expected: str) -> None:
    assert derive_setting("prostate", facts(**values)).setting == expected


def test_treatment_and_progression_mentions_do_not_establish_castration_resistance() -> None:
    decision = derive_setting(
        "prostate",
        facts(
            ctnm="cT3 N1 M1b", isup="5", psa_total="73",
            previous_rcp_decisions="ADT par triptoréline puis abiratérone",
            disease_phase="progression biologique",
        ),
    )
    assert decision.setting is None
    assert "disease_phase" in decision.missing


def test_treatment_name_alone_does_not_establish_hormone_sensitivity() -> None:
    on_adt_only = derive_setting(
        "prostate",
        facts(ctnm="cT3 N1 M1b", isup="5", psa_total="73",
              previous_rcp_decisions="ADT par triptoréline"),
    )
    assert on_adt_only.setting is None


# --------------------------------------------------------------------------------------
# Historical test strings, not asserted patient truth
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dossier", "cancer", "values", "expected"),
    [
        ("dossier-1", "bladder", dict(ctnm="cT4a N1 M1a"), "bladder_metastatic"),
        ("dossier-2", "bladder", dict(ptnm="pT2 N0 M0"), None),
        ("dossier-10", "bladder", dict(ptnm="pTa"), None),
        ("dossier-11", "bladder", dict(ptnm="pT1"), None),
        ("dossier-15", "bladder", dict(ptnm="pT1"), None),
        ("dossier-6", "prostate", dict(ctnm="cT3 N1 M1b", isup="5", psa_total="116"), None),
        ("dossier-9", "prostate", dict(ctnm="cT3a N0 M0", isup="3", psa_total="34"),
         "prostate_locally_advanced_non_metastatic"),
        ("dossier-3", "prostate", dict(ctnm="cT2a N0 M0", isup="2", psa_total="7.5"),
         "prostate_localized_intermediate_risk"),
    ],
)
def test_the_recorded_dossiers_derive_the_setting_expected_of_them(
    dossier: str, cancer: str, values: dict, expected: str
) -> None:
    assert derive_setting(cancer, facts(**values)).setting == expected, dossier


def test_fully_documented_synthetic_ct3a_n0_m0_does_not_derive_intermediate_risk() -> None:
    """This positive control does not describe an archived record carrying Mx."""
    decision = derive_setting(
        "prostate", facts(ctnm="cT3a N0 M0", isup="3", psa_total="34")
    )
    assert decision.setting == "prostate_locally_advanced_non_metastatic"
    assert decision.setting != "prostate_localized_intermediate_risk"


# --------------------------------------------------------------------------------------
# The assembled filter
# --------------------------------------------------------------------------------------


def test_the_filter_carries_the_derived_setting_and_the_asked_for_dimensions() -> None:
    built = build_filter(
        "bladder",
        facts(ctnm="cT2 N0 M0"),
        agent_role="renal_safety",
        clinical_purpose=("safety", "therapy_indication"),
    )
    assert built.filter.cancer_type == "bladder"
    assert built.filter.disease_setting == "bladder_mibc_non_metastatic"
    assert built.filter.agent_role == "renal_safety"
    assert built.filter.clinical_purpose == ("safety", "therapy_indication")
    assert "therapy_line" in built.unset


def test_an_underivable_setting_leaves_the_dimension_unset_rather_than_wrong() -> None:
    """An unset dimension widens the search, which is visible and recoverable. A wrong one
    returns guidance for a different patient, which is neither.
    """
    built = build_filter("bladder", facts(age="72"))
    assert built.filter.disease_setting is None
    assert "disease_setting" in built.unset
    assert "No clinical TNM" in built.explanation


def test_the_query_explains_itself() -> None:
    built = build_filter("prostate", facts(ctnm="cT2a N0 M0"), agent_role="urology_oncology")
    assert "Disease setting:" in built.explanation
    assert "Missing for a narrower answer" in built.explanation


# --------------------------------------------------------------------------------------
# Uncertain is not confirmed
# --------------------------------------------------------------------------------------


def test_an_unconfirmed_metastasis_does_not_select_metastatic_guidance() -> None:
    """**Found by measuring, not by reasoning.** Dossier 1 records `cT4a N1 M1a (à confirmer)`.
    Treating that as established routed four pilot questions to metastatic bladder guidance —
    a different treatment intent — on a finding nobody had confirmed.
    """
    decision = derive_setting("bladder", facts(ctnm="cT4a N1 M1a (à confirmer)"))
    assert decision.setting is None
    assert "unconfirmed" in decision.reason
    assert "confirmed staging assessment" in decision.missing


def test_a_confirmed_metastasis_still_selects_metastatic_guidance() -> None:
    assert derive_setting("bladder", facts(ctnm="cT4a N1 M1a")).setting == "bladder_metastatic"


def test_prostate_refuses_to_choose_when_the_metastatic_status_is_unconfirmed() -> None:
    """Prostate has no safe fallback: metastatic and non-metastatic guidance differ in intent,
    so neither is selected rather than one being guessed.
    """
    decision = derive_setting(
        "prostate", facts(ctnm="cT3 N1 M1b (suspicion)", isup="5", psa_total="90")
    )
    assert decision.setting is None
    assert "unconfirmed" in decision.reason


@pytest.mark.parametrize(
    "hedge", ["(à confirmer)", "à confirmer", "suspicion de M1", "M1 probable", "M1 ?"]
)
def test_the_hedges_a_dossier_actually_writes_are_recognised(hedge: str) -> None:
    stage = parse_stage(f"cT4a N1 M1a {hedge}")
    assert stage is not None
    assert stage.uncertain
    assert not stage.metastatic
    assert stage.metastasis_suspected
