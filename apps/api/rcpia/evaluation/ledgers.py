"""The 15 dossiers as ledgers, so the safety engine can be measured rather than described.

**Transcribed, not inferred.** Every value here comes from the patient summary tables in
`docs/implementation/safety-rules-from-dossiers.md`. A field the dossier does not record is
`None`, and that is load-bearing: three of the seven bladder patients have no eGFR, and the
engine must report the platinum check as *unevaluated* for them rather than as *no concern*.

**Nothing infers a missing value from a present one.** No performance status is recorded for
any of the fifteen, so `performance_status` is `None` throughout and every rule needing it is
unevaluable. Filling it with a plausible default would manufacture a safety verdict out of an
assumption, which is the failure this whole layer exists to prevent.
"""

from rcpia.evaluation.goldset import Question
from rcpia.query.builder import Fact
from rcpia.safety_rules.catalogue import Ledger

DOSSIER_LEDGERS: dict[str, Ledger] = {
    # --- bladder ---
    "dossier-1": Ledger(
        dossier_id="dossier-1", cancer_type="bladder",
        disease_setting="bladder_mibc_non_metastatic",
        age=63, egfr=28, mna=11.5, adl=3, haemoglobin=7.8,
    ),
    "dossier-2": Ledger(
        dossier_id="dossier-2", cancer_type="bladder",
        disease_setting="bladder_mibc_non_metastatic",
        age=85, egfr=30, mna=7, g8=5, adl=4, haemoglobin=11.6,
    ),
    "dossier-10": Ledger(
        dossier_id="dossier-10", cancer_type="bladder",
        disease_setting="bladder_nmibc_unspecified_risk", age=56, prior_resections=0,
    ),
    "dossier-11": Ledger(
        dossier_id="dossier-11", cancer_type="bladder",
        disease_setting="bladder_nmibc_unspecified_risk", age=64, prior_resections=0,
    ),
    "dossier-13": Ledger(
        dossier_id="dossier-13", cancer_type="bladder",
        disease_setting="bladder_metastatic", age=79, egfr=85, haemoglobin=16.2,
    ),
    "dossier-14": Ledger(
        dossier_id="dossier-14", cancer_type="bladder",
        disease_setting="bladder_mibc_non_metastatic", age=79, haemoglobin=12.2,
    ),
    "dossier-15": Ledger(
        dossier_id="dossier-15", cancer_type="bladder",
        disease_setting="bladder_nmibc_unspecified_risk",
        age=71, egfr=65, mna=23.5, adl=6, haemoglobin=9.0, prior_resections=18,
    ),
    # --- prostate ---
    "dossier-3": Ledger(
        dossier_id="dossier-3", cancer_type="prostate",
        disease_setting="prostate_localized_intermediate_risk",
        age=70, mna=23.5, gleason_sum=7, isup=2, psa=7.5,
    ),
    "dossier-4": Ledger(
        dossier_id="dossier-4", cancer_type="prostate",
        disease_setting="prostate_localized_intermediate_risk",
        age=72, mna=23.5, gleason_sum=7, isup=2, psa=5.1,
    ),
    "dossier-5": Ledger(
        dossier_id="dossier-5", cancer_type="prostate",
        disease_setting="prostate_nonmetastatic_unspecified_risk",
        age=70, mna=23.5, gleason_sum=7, isup=2, psa=8.9, haemoglobin=9.2,
    ),
    "dossier-6": Ledger(
        dossier_id="dossier-6", cancer_type="prostate",
        disease_setting="prostate_metastatic_hormone_sensitive",
        age=59, egfr=114, mna=23.5, gleason_sum=9, isup=5, psa=116,
    ),
    "dossier-7": Ledger(
        dossier_id="dossier-7", cancer_type="prostate",
        disease_setting="prostate_localized_intermediate_risk",
        age=72, mna=23.5, gleason_sum=7, isup=3, psa=34,
    ),
    "dossier-8": Ledger(
        dossier_id="dossier-8", cancer_type="prostate",
        disease_setting="prostate_metastatic_castration_resistant",
        age=61, mna=15.5, gleason_sum=9, isup=5, psa=73,
        on_adt=True, psa_rising=True,
    ),
    "dossier-9": Ledger(
        dossier_id="dossier-9", cancer_type="prostate",
        disease_setting="prostate_localized_intermediate_risk",
        age=70, gleason_sum=7, isup=3, psa=34, clinical_t_stage="T3a",
    ),
    "dossier-12": Ledger(
        dossier_id="dossier-12", cancer_type="prostate",
        disease_setting="prostate_metastatic_hormone_sensitive",
        age=70, gleason_sum=8, isup=4, psa=12,
    ),
}


def ledger_for(question: Question) -> Ledger | None:
    """The ledger for a gold-set question, from the dossier its `source` names.

    A question drawn from two dossiers names both — `"dossier-15 (et dossier-2)"`. The first is
    taken as the patient, because that is the one the question's clinical facts were written
    from. Returns None rather than an empty ledger when no dossier can be identified: an empty
    ledger would make every rule unevaluable and read as if the checks had been attempted.
    """
    source = question.source or ""
    for dossier_id in sorted(DOSSIER_LEDGERS, key=len, reverse=True):
        if source.startswith(dossier_id):
            return DOSSIER_LEDGERS[dossier_id]
    return None


# The staging fields the query builder reads, transcribed from the same patient summaries.
#
# **Only what the record states.** Where a dossier's summary gives no TNM, none is invented --
# "organ-confined" and a PI-RADS score are not a stage, and turning them into one would be the
# builder guessing under a different name. Four of the eight prostate dossiers are in that
# position, which is a finding about the intake form rather than about the derivation.
DOSSIER_FACTS: dict[str, tuple[Fact, ...]] = {
    "dossier-1": (
        Fact("ctnm", "cT4a N1 M1a (à confirmer)"), Fact("histology", "carcinome épidermoïde"),
        Fact("egfr", "28"), Fact("mna", "11.5"), Fact("haemoglobin", "7.8"),
    ),
    "dossier-2": (
        Fact("ptnm", "pT2"), Fact("histology", "carcinome urothélial de haut grade"),
        Fact("egfr", "30"), Fact("mna", "7"), Fact("g8_score", "5"), Fact("adl", "4"),
    ),
    "dossier-10": (Fact("ptnm", "pTa"), Fact("grade", "bas grade"),),
    "dossier-11": (Fact("ptnm", "pT1"), Fact("grade", "bas grade"),),
    "dossier-13": (Fact("ptnm", "pT4"), Fact("ctnm", "N1 M1"), Fact("egfr", "85"),),
    "dossier-14": (Fact("ptnm", "pT2"), Fact("grade", "haut grade"),),
    "dossier-15": (
        Fact("ptnm", "pT1"), Fact("grade", "bas grade"), Fact("egfr", "65"),
        Fact("illness_history", "18 résections antérieures"),
    ),
    # No TNM is recorded for these four. The summaries give PI-RADS and "organ-confined", which
    # are not a stage.
    "dossier-3": (Fact("gleason", "3+4"), Fact("isup", "2"), Fact("psa_total", "7.5"),),
    "dossier-4": (
        Fact("gleason", "3+4"), Fact("isup", "2"), Fact("psa_total", "5.1"),
        Fact("medical_history", "GIST sous imatinib"),
    ),
    "dossier-5": (
        Fact("gleason", "3+4"), Fact("isup", "2"), Fact("psa_total", "8.9"),
        Fact("haemoglobin", "9.2"),
    ),
    "dossier-7": (Fact("gleason", "4+3"), Fact("isup", "3"), Fact("psa_total", "34"),),
    "dossier-6": (
        Fact("ctnm", "M1b"), Fact("gleason", "5+4"), Fact("isup", "5"),
        Fact("psa_total", "116"), Fact("egfr", "114"),
    ),
    "dossier-8": (
        Fact("gleason", "5+4"), Fact("isup", "5"), Fact("psa_total", "73"), Fact("mna", "15.5"),
        Fact("previous_rcp_decisions", "ADT par triptoréline, abiratérone puis docétaxel"),
        Fact("disease_phase", "progression"),
    ),
    "dossier-9": (
        Fact("ctnm", "T3a N0 M0"), Fact("gleason", "4+3"), Fact("isup", "3"),
        Fact("psa_total", "34"),
    ),
    "dossier-12": (
        Fact("ctnm", "M1b"), Fact("gleason", "4+4"), Fact("isup", "4"), Fact("psa_total", "12"),
    ),
}


def facts_for(question: Question) -> tuple[Fact, ...]:
    """The staging facts for a gold-set question, by the dossier its `source` names."""
    source = question.source or ""
    for dossier_id in sorted(DOSSIER_FACTS, key=len, reverse=True):
        if source.startswith(dossier_id):
            return DOSSIER_FACTS[dossier_id]
    return ()
