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


def test_role_is_ignored_when_no_setting_was_derived() -> None:
    """Without a setting there is nothing to specialise, so every role gets the same broad
    question — the honest answer for a record that determined nothing."""
    for role in sorted(AGENT_ROLES):
        assert question_for("prostate", None, role) == GENERIC_QUESTIONS["prostate"]


def test_an_unknown_role_falls_back_to_the_setting_question() -> None:
    """A role the table does not know is a coverage gap, not an error."""
    assert question_for("bladder", "bladder_mibc_non_metastatic", "psycho_oncology") == (
        SETTING_QUESTIONS["bladder_mibc_non_metastatic"]
    )
