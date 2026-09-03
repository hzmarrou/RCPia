"""Briganti — risk of lymph-node invasion before radical prostatectomy.

**Selection is by diagnostic pathway, never by whichever version's inputs happen to be
present.** The three published versions were fitted on three different work-ups: systematic
biopsy alone (2012), mpMRI with targeted biopsy (2019), and the same after a negative PSMA
PET/CT (2023). A patient who had a PSMA PET is a 2023 patient; if a 2023 input is missing, the
answer is "2023, not possible, obtain the index-lesion diameter" -- not a quietly computed 2012
figure. Falling back would answer a different question and label it with a real model's name,
and nothing downstream could tell.

**`cN` and `cM` are not inputs.** Briganti 2012 takes PSA, the DRE-derived clinical T stage,
the primary and secondary biopsy Gleason patterns, and the percentage of positive systematic
cores. Documented nodal or distant disease is an *applicability* failure -- the risk of nodal
invasion is not the question for a patient already known to have it -- while an unrecorded
nodal status is the ordinary state of the patient this model exists for.
"""

import math

from rcpia.nomograms.contract import (
    NomogramResult,
    Provenance,
    Reason,
    Status,
    calculated,
    not_applicable,
    not_possible,
)
from rcpia.nomograms.record import ClinicalRecord, Tristate

FAMILY = "briganti"

PROVENANCE_2012 = Provenance(
    formula_revision="briganti_2012_v1",
    source_url="https://doi.org/10.1016/j.eururo.2011.10.044",
    limitation=(
        "Coefficients reconstruits à partir du nomogramme graphique publié ; les auteurs "
        "n'ont pas publié d'équation exécutable. Probabilité d'envahissement ganglionnaire, "
        "non une décision : l'indication d'un curage relève du référentiel et de la RCP."
    ),
)

# LP = -5.3269 + 0.0361·PSA + stage + gleason + 0.0269·(% positive cores)
INTERCEPT = -5.3269
PSA_COEFFICIENT = 0.0361
CORES_COEFFICIENT = 0.0269
PRIMARY_GLEASON_4 = 1.2950
SECONDARY_GLEASON_4 = 0.3721
# Three levels only. cT4 has no term, and mapping it onto cT3 would answer a question the
# model was never fitted to.
STAGE_TERM = {"T1": 0.0, "T2": 0.5876, "T3": 1.9950}

DISPLAY = {"2012": "Briganti 2012", "2019": "Briganti 2019", "2023": "Briganti 2023"}


def stage_band(stage: str | None) -> str | None:
    """`T2a` -> `T2`. Briganti bands the sub-stages; MSKCC does not, so this is local."""
    if not stage:
        return None
    band = stage.upper().lstrip("C")[:2]
    return band if band in STAGE_TERM else None


def applicability(record: ClinicalRecord) -> tuple[Reason, ...]:
    """Why Briganti does not apply to this patient at all, if it does not."""
    reasons: list[Reason] = []
    if record.known_nodal_metastasis is Tristate.yes:
        reasons.append(Reason.known_nodal_metastasis)
    if record.known_distant_metastasis is Tristate.yes:
        reasons.append(Reason.known_distant_metastasis)
    if record.radical_prostatectomy_performed is Tristate.yes:
        # A preoperative model has nothing to say after the operation.
        reasons.append(Reason.post_treatment_context)
    if record.is_prostate_adenocarcinoma is Tristate.no:
        # Briganti was validated on adenocarcinoma. A confirmed other histology -- small-cell,
        # neuroendocrine, sarcomatoid -- is not a data gap the RCP can fill by ordering another
        # test: no amount of PSA, stage or Gleason data makes this model apply to this tumour.
        reasons.append(Reason.not_prostate_adenocarcinoma)
    return tuple(reasons)


def select_pathway(record: ClinicalRecord) -> str | None:
    """Which published version this patient's work-up belongs to.

    Ordered by work-up, not by recency: a PSMA PET makes this a 2023 patient whether or not
    the rest of the 2023 inputs were recorded.
    """
    if record.psma_pet_performed is Tristate.yes:
        return "2023"
    if record.mri_targeted_biopsy_performed is Tristate.yes:
        return "2019"
    # **The conventional systematic-biopsy pathway is what remains**, and it is chosen on the
    # absence of an imaging pathway rather than on having its own inputs. An earlier version
    # required a core count or a stage to be present before selecting 2012, which reported a
    # dossier missing both as `BRIGANTI_PATHWAY_UNDETERMINED` -- true in a narrow sense and
    # useless to an RCP, who needs to be told that this is a 2012 patient lacking two named
    # measurements. The assignment's acceptance table says the same: dossier 9, missing both
    # core counts and an explicit DRE stage, is `briganti_2012 / NOT_POSSIBLE`.
    return "2012"


def evaluate(record: ClinicalRecord) -> NomogramResult:
    """One Briganti answer for this patient, computed or explained."""
    blocked = applicability(record)
    if blocked:
        return not_applicable(
            model_family=FAMILY, model_id=None, display_name="Briganti", reasons=blocked
        )

    invalid = record.invalid_ranges()
    if invalid:
        return NomogramResult(
            model_family=FAMILY,
            model_id=None,
            display_name="Briganti",
            status=Status.not_possible,
            missing_fields=invalid,
            reason_codes=(Reason.invalid_input_range,),
        )

    version = select_pathway(record)
    if version is None:
        return not_possible(
            model_family=FAMILY,
            model_id=None,
            display_name="Briganti",
            reasons=(Reason.briganti_pathway_undetermined,),
        )
    if version != "2012":
        # The pathway is identified and its coefficients are not implemented. Reported as
        # itself rather than as a 2012 result, which is the whole point of pathway selection.
        return not_possible(
            model_family=FAMILY,
            model_id=f"briganti_{version}",
            display_name=DISPLAY[version],
            reasons=(Reason.coefficients_not_implemented,),
        )
    return _briganti_2012(record)


def _briganti_2012(record: ClinicalRecord) -> NomogramResult:
    psa = record.psa_ng_ml
    primary, secondary = record.gleason_primary, record.gleason_secondary
    percent = record.percent_positive_cores
    stage = record.clinical_t_stage_dre

    missing: list[str] = []
    if psa is None:
        missing.append("psa_ng_ml")
    if stage is None:
        missing.append("clinical_t_stage_dre")
    if primary is None or secondary is None:
        missing.append("gleason_primary_and_secondary")
    if percent is None:
        missing.append("systematic_positive_and_total_cores")
    if missing or psa is None or primary is None or secondary is None or percent is None:
        return not_possible(
            model_family=FAMILY,
            model_id="briganti_2012",
            display_name=DISPLAY["2012"],
            missing=tuple(missing),
            provenance=PROVENANCE_2012,
        )

    # Unknown is not "no". Checked after the numeric inputs, so a dossier missing both a
    # measurement and a pathology report is told about the measurement first -- the same
    # ordering `mskcc.py` uses for its own unconfirmed eligibility questions.
    if record.is_prostate_adenocarcinoma.is_unknown:
        return not_possible(
            model_family=FAMILY,
            model_id="briganti_2012",
            display_name=DISPLAY["2012"],
            missing=("is_prostate_adenocarcinoma",),
            reasons=(Reason.eligibility_unconfirmed,),
            provenance=PROVENANCE_2012,
        )

    band = stage_band(stage)
    if band is None:
        return not_applicable(
            model_family=FAMILY,
            model_id="briganti_2012",
            display_name=DISPLAY["2012"],
            reasons=(Reason.model_eligibility_not_met,),
        )

    grade = PRIMARY_GLEASON_4 * (primary >= 4) + SECONDARY_GLEASON_4 * (secondary >= 4)
    linear_predictor = (
        INTERCEPT
        + PSA_COEFFICIENT * psa
        + STAGE_TERM[band]
        + grade
        + CORES_COEFFICIENT * percent
    )
    probability = 100.0 / (1.0 + math.exp(-linear_predictor))

    return calculated(
        model_family=FAMILY,
        model_id="briganti_2012",
        display_name=DISPLAY["2012"],
        estimates={"lymph_node_invasion_percent": probability},
        inputs_used={
            "psa_ng_ml": f"{psa:g}",
            "clinical_t_stage_dre": str(stage),
            "stage_band_used": band,
            "gleason_primary": str(primary),
            "gleason_secondary": str(secondary),
            "systematic_positive_cores": str(record.systematic_positive_cores),
            "systematic_total_cores": str(record.systematic_total_cores),
            "positive_systematic_cores_percent": f"{percent:.4f}",
        },
        provenance=PROVENANCE_2012,
    )
