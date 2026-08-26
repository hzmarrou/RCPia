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
