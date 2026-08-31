"""The search question is derived from the setting, and every setting has one.

The defect these tests guard against is not a crash. It is a question that is *plausible* and
wrong for the patient: a metastatic man searched with a localised-disease question gets guidance
written for someone else, and nothing in the fiche says so. The mapping is therefore checked for
completeness against the staging vocabulary rather than spot-checked.
"""

import re
from pathlib import Path

from rcpia.corpus.vocabulary import AGENT_ROLES
from rcpia.query import staging
from rcpia.query.questions import (
    GENERIC_QUESTIONS,
    ROLE_GENERIC_QUESTIONS,
    ROLE_SETTING_QUESTIONS,
    SETTING_QUESTIONS,
    question_for,
)
from rcpia.query.staging import SettingDecision


def _emitted_settings() -> set[str]:
    """The settings `staging.py` can emit, taken from its own source."""
    source = Path(staging.__file__).read_text(encoding="utf-8")
    # Only the strings handed to a SettingDecision are settings; the module also names them in
    # prose, which is why the match is anchored on the constructor call.
    return set(re.findall(r'SettingDecision\(\s*"((?:bladder|prostate)_[a-z_]+)"', source))


def test_every_emitted_setting_has_a_question() -> None:
    """A setting with no entry falls back to the generic question and says nothing about it.

    That is the exact failure this module was written to remove, so it must not reappear one
    setting at a time as the staging logic grows.
    """
    missing = _emitted_settings() - set(SETTING_QUESTIONS)
    assert not missing, f"settings with no search question: {sorted(missing)}"


def test_questions_are_specific_to_their_setting() -> None:
    """No two settings share a question, and none is the generic string.

    Two settings mapping to the same text would mean the derivation was doing no work for one of
    them, which is how the previous single-question behaviour looked from the outside.
    """
    assert len(set(SETTING_QUESTIONS.values())) == len(SETTING_QUESTIONS)
    assert not set(SETTING_QUESTIONS.values()) & set(GENERIC_QUESTIONS.values())


def test_metastatic_prostate_asks_a_metastatic_question() -> None:
    """The case that exposed the defect: dossier 12, de-novo metastatic hormone-sensitive.

    It received one guideline passage — a *surgical* AFU passage — because the question never
    mentioned metastasis.
    """
    question = question_for("prostate", "prostate_metastatic_hormone_sensitive")
    assert "métastatique" in question
    assert "hormonosensible" in question
    assert question != GENERIC_QUESTIONS["prostate"]


def test_muscle_invasive_bladder_names_the_cisplatin_gate() -> None:
    """The bladder decision the corpus is most often asked about."""
    question = question_for("bladder", "bladder_mibc_non_metastatic")
    assert "cystectomie" in question
    assert "cisplatine" in question


def test_undetermined_setting_falls_back_to_the_generic_question() -> None:
    """A record that determined nothing gets a broad search, not an invented narrow one."""
    assert question_for("prostate", None) == GENERIC_QUESTIONS["prostate"]
    assert question_for("bladder", None) == GENERIC_QUESTIONS["bladder"]


def test_unmapped_setting_falls_back_rather_than_failing() -> None:
    """An unknown setting is a gap in the table, not a reason to search for nothing."""
    assert question_for("prostate", "prostate_some_future_setting") == (
        GENERIC_QUESTIONS["prostate"]
    )


def test_unknown_cancer_type_still_yields_a_question() -> None:
    """`build_filter` never derives the organ, so an unexpected one must not produce an empty
    query — it produces a broad one that names the organ it was given."""
    question = question_for("renal", None)
    assert "renal" in question
    assert question.strip()


def test_decision_setting_flows_into_the_question() -> None:
    """The contract `evidence.py` relies on: a `SettingDecision` names the question."""
    decision = SettingDecision("bladder_metastatic", "M1 recorded.", ("ctnm",))
    assert question_for("bladder", decision.setting) == SETTING_QUESTIONS["bladder_metastatic"]


# --- The role-aware layer -------------------------------------------------------------------


def test_the_three_wired_agents_ask_three_different_questions_on_the_same_patient() -> None:
    """The defect this layer exists for, restated for the third agent.

    Measured before the layer existed: urology and medical oncology were handed **identical**
    passages 10 of 10 on one bladder dossier. A filter that permits more than the question asks
    for permits nothing in practice. Adding a speciality without adding its question would
    reproduce that, and the panel would report a convergence between two agents that read the
    same page.
    """
    setting = "bladder_mibc_non_metastatic"
    questions = {
        role: question_for("bladder", setting, role)
        for role in ("urology_oncology", "medical_oncology", "radiation_oncology")
    }
    assert len(set(questions.values())) == 3
    # Each names the option it owns, and no other agent's question does.
    assert "cystectomie" in questions["urology_oncology"].lower()
    assert "cisplatine" in questions["medical_oncology"].lower()
    assert "trimodale" in questions["radiation_oncology"].lower()


def test_radiotherapy_asks_about_prostate_on_the_setting_the_dossiers_reach() -> None:
    """Mapped on unspecified risk, not on a risk band.

    The risk group needs Gleason, PSA and stage together, and those are the fields extraction
    most often leaves incomplete. A pair mapped on `prostate_localized_high_risk` would be
    correct and would almost never fire.
    """
    question = question_for(
        "prostate", "prostate_nonmetastatic_unspecified_risk", "radiation_oncology"
    )
    assert question != SETTING_QUESTIONS["prostate_nonmetastatic_unspecified_risk"]
    assert "curiethérapie" in question
    assert "hypofractionnement" in question


def test_a_patient_with_no_derived_setting_still_gets_three_different_questions() -> None:
    """The measured hole in the three-level design.

    Every stored bladder record derives a setting, but **38 of 40 prostate records derive none**
    — the risk group needs Gleason, PSA and stage together, and those are the fields extraction
    most often leaves incomplete. Those patients fell straight past the role layer to one generic
    organ question shared by every specialist, which is the identical-passages defect the layer
    exists to remove, reappearing on the majority of one organ's dossiers.
    """
    asked = {
        role: question_for("prostate", None, role)
        for role in ("urology_oncology", "medical_oncology", "radiation_oncology")
    }
    assert len(set(asked.values())) == 3
    assert "prostatectomie" in asked["urology_oncology"].lower()
    assert "docétaxel" in asked["medical_oncology"].lower()
    assert "curiethérapie" in asked["radiation_oncology"].lower()
    # And none of them is the role-blind fallback that used to be returned here.
    assert GENERIC_QUESTIONS["prostate"] not in set(asked.values())


def test_a_derived_setting_still_beats_the_speciality_generic() -> None:
    """Level 3 sits below level 2 on purpose.

    A question naming the patient's actual clinical situation beats one naming only the
    speciality, because the guidelines are organised by situation. An unmapped role on a mapped
    setting must therefore keep getting the setting question.
    """
    assert question_for("bladder", "bladder_metastatic", "radiation_oncology") == (
        SETTING_QUESTIONS["bladder_metastatic"]
    )


def test_an_unmapped_role_keeps_the_role_blind_fallback() -> None:
    """Roles with no entry are the designed state, not a defect, and must not return nothing."""
    assert question_for("prostate", None, "pathology") == GENERIC_QUESTIONS["prostate"]
    assert question_for("prostate", None) == GENERIC_QUESTIONS["prostate"]


def test_every_mapped_role_is_a_real_agent_role() -> None:
    """A typo in a role key would fall back silently and look like a coverage gap.

    Checked against the corpus vocabulary rather than a list restated here, so a role renamed
    in one place cannot stay valid in the other.
    """
    unknown = {role for role, _ in ROLE_SETTING_QUESTIONS} - set(AGENT_ROLES)
    assert not unknown, f"questions mapped to roles the corpus does not define: {sorted(unknown)}"


def test_every_mapped_setting_is_a_real_setting() -> None:
    """Same argument for the other half of the key."""
    unknown = {setting for _, setting in ROLE_SETTING_QUESTIONS} - set(SETTING_QUESTIONS)
    assert not unknown, f"questions mapped to unknown settings: {sorted(unknown)}"


def test_every_agent_role_either_maps_or_falls_back() -> None:
    """The coverage contract: no role can produce an empty or missing question.

    Most roles have no role-specific question yet, and that is the designed state — they fall
    back to the per-setting question. What must never happen is a role that returns nothing, or
    that returns something different from the documented fallback.
    """
    for role in sorted(AGENT_ROLES):
        for setting in sorted(SETTING_QUESTIONS):
            question = question_for("bladder", setting, role)
            assert question.strip(), f"{role} + {setting} produced an empty question"
            if (role, setting) in ROLE_SETTING_QUESTIONS:
                assert question == ROLE_SETTING_QUESTIONS[(role, setting)]
            else:
                assert question == SETTING_QUESTIONS[setting], (
                    f"{role} + {setting} did not fall back to the per-setting question"
                )


def test_the_two_bladder_specialists_ask_different_questions() -> None:
    """The defect this layer exists to fix: identical evidence for two specialists.

    Measured on dossiers 14 and 2 — ten of ten identical passages on one, nine of ten on the
    other — because both roles asked the same string.
    """
    for setting in ("bladder_mibc_non_metastatic", "bladder_nmibc_unspecified_risk"):
        urology = question_for("bladder", setting, "urology_oncology")
        medical = question_for("bladder", setting, "medical_oncology")
        assert urology != medical
        assert urology != SETTING_QUESTIONS[setting]
        assert medical != SETTING_QUESTIONS[setting]


def test_the_surgical_and_systemic_questions_name_their_own_subject() -> None:
    """A role-specific question that did not name the specialty's own decisions would retrieve
    the same passages as the generic one and quietly undo the fix."""
    urology = question_for("bladder", "bladder_mibc_non_metastatic", "urology_oncology")
    medical = question_for("bladder", "bladder_mibc_non_metastatic", "medical_oncology")

    assert "ystectomie" in urology and "érivation urinaire" in urology
    assert "carboplatine" not in urology

    assert "cisplatine" in medical and "carboplatine" in medical
    assert "dérivation urinaire" not in medical


def test_a_role_without_a_setting_is_specialised_where_the_table_can_and_broad_where_it_cannot(
) -> None:
    """This assertion used to read the other way, and the measurement is why it changed.

    The reasoning was that without a setting there is nothing to specialise, so every role
    should get the same broad question — the honest answer for a record that determined nothing.
    It was honest about the record and wrong about the consequence: **38 of 40 stored prostate
    records derive no setting**, so "every role gets the same question" was not a rare fallback,
    it was the normal case for that organ. Three specialists reading the same ten passages are
    not three opinions.

    A speciality is still not invented where none is mapped: a role with no entry gets the broad
    question exactly as before.
    """
    mapped = {
        role for role, organ in ROLE_GENERIC_QUESTIONS if organ == "prostate"
    }
    assert mapped, "the prostate fallback is what this test exists to protect"
    for role in sorted(AGENT_ROLES):
        question = question_for("prostate", None, role)
        if role in mapped:
            assert question != GENERIC_QUESTIONS["prostate"]
        else:
            assert question == GENERIC_QUESTIONS["prostate"]


def test_an_unknown_role_falls_back_to_the_setting_question() -> None:
    """A role the table does not know is a coverage gap, not an error."""
    assert question_for("bladder", "bladder_mibc_non_metastatic", "psycho_oncology") == (
        SETTING_QUESTIONS["bladder_mibc_non_metastatic"]
    )
