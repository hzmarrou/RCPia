"""MSKCC pre-radical-prostatectomy nomogram.

**Five endpoints, verified against the live calculator.** Organ-confined disease,
extracapsular extension, lymph-node involvement, seminal-vesicle invasion, and progression-free
probability at five years. Two patients entered on mskcc.org reproduce every one of them to
rounding, across both coefficient sets -- which tests the assembly and not merely the
transcription. A perfect coefficient table wired to the wrong link function still returns
confident wrong numbers.

**Two outputs are deliberately missing, because ours were wrong.** MSKCC also reports
cancer-specific survival at fifteen years and progression-free probability at ten. Checked
against the same two patients, the survival figure came out at 0.14% where the site says 2%,
and 4.3% where the site says 42% -- the covariate is the five-year BCR-free probability, which
is right, but the prediction horizon is fifteen years and the implementation evaluated it at
five. Rather than ship a figure wrong by an order of magnitude, the endpoint is absent and says
so. Neither is a number the clinician named as used: they asked for local aggressiveness and
nodal extension, both of which are here.

**Eligibility blocks rather than assumes.** MSKCC's own form asks two yes/no questions before
anything else -- prior hormone therapy, prior radiotherapy -- and a `yes` to either disqualifies
the patient. A dossier silent on both cannot be shown to be eligible. That silence is reported
as `ELIGIBILITY_UNCONFIRMED`, which names two questions an RCP can answer in a sentence, rather
than being read as "no".

Coefficients: https://www.mskcc.org/nomograms/prostate/pre_op/coefficients, revision of
12 December 2024, audited term by term against that page.
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

FAMILY = "mskcc"
MODEL_ID = "mskcc_preoperative"
DISPLAY = "MSKCC pré-prostatectomie radicale"

PROVENANCE = Provenance(
    formula_revision="mskcc_preop_2024-12-12",
    source_url="https://www.mskcc.org/nomograms/prostate/pre_op/coefficients",
    limitation=(
        "Probabilités indépendantes, non une décision. La survie spécifique à 15 ans et la "
        "survie sans progression à 10 ans ne sont pas calculées ici et ne doivent pas être "
        "déduites de ces valeurs. Stadification clinique AJCC version 7 (2010), telle "
        "qu'attendue par le modèle publié."
    ),
)

PSA_KNOTS = (0.2, 4.8, 7.35, 307.0)

# Clinical stage. The published table has four bands -- 2A, 2B, 2C and 3+ -- with T1 as the
# reference category, so T3a and T3b share the 3+ coefficient.
STAGE = {
    "BCR_cores": {"T1": 0.0, "T2a": -0.2653079, "T2b": -0.55098723, "T2c": -0.53710672,
                  "T3": -0.90153},
    "BCR": {"T1": 0.0, "T2a": -0.33123718, "T2b": -0.71511838, "T2c": -0.74038841,
            "T3": -1.17324936},
    "ECE_cores": {"T1": 0.0, "T2a": 0.21016394, "T2b": 0.7631916, "T2c": 0.56884638,
                  "T3": 1.46007476},
    "ECE": {"T1": 0.0, "T2a": 0.29223705, "T2b": 0.8045909, "T2c": 0.85209752,
            "T3": 1.67265063},
    "LNI_cores": {"T1": 0.0, "T2a": 0.17084652, "T2b": 0.49919005, "T2c": 0.46102494,
                  "T3": 0.74403104},
    "LNI": {"T1": 0.0, "T2a": 0.26172062, "T2b": 0.55860494, "T2c": 0.84874365,
            "T3": 1.09527926},
    "OCD_cores": {"T1": 0.0, "T2a": -0.22318145, "T2b": -0.752952, "T2c": -0.51980437,
                  "T3": -1.51243948},
    "OCD": {"T1": 0.0, "T2a": -0.31267024, "T2b": -0.81414158, "T2c": -0.82816419,
            "T3": -1.70223149},
    "SVI_cores": {"T1": 0.0, "T2a": 0.13563953, "T2b": 0.43790414, "T2c": 0.67764059,
                  "T3": 0.80895957},
    "SVI": {"T1": 0.0, "T2a": 0.28661144, "T2b": 0.58408599, "T2c": 1.04372486,
            "T3": 1.23384554},
}

# Gleason grade group; index 0 is GGG1, the reference category.
GRADE = {
    "BCR_cores": (0.0, -1.03528693, -2.01205601, -2.41622722, -2.7369239),
    "BCR": (0.0, -1.20123133, -2.20716939, -2.61752267, -2.98888801),
    "ECE_cores": (0.0, 0.62975509, 1.04483516, 1.11988728, 2.04021401),
    "ECE": (0.0, 0.9456123, 1.38600414, 1.47229518, 2.52672521),
    "LNI_cores": (0.0, 0.99889717, 2.03362879, 2.17702562, 2.87515732),
    "LNI": (0.0, 1.52752948, 2.57873595, 2.75375893, 3.50034615),
    "OCD_cores": (0.0, -0.66445702, -1.14834872, -1.20528662, -2.1840382),
    "OCD": (0.0, -0.97346299, -1.4674392, -1.55598833, -2.62627438),
    "SVI_cores": (0.0, 1.11283762, 1.89864418, 2.06573755, 2.97808178),
    "SVI": (0.0, 1.24764853, 2.04455038, 2.19455366, 3.17089909),
}

# intercept, age, psa, spline 1, spline 2, positive cores, negative cores
TERMS = {
    "ECE_cores": (-4.14615912, 0.03263727, 0.22419499, -0.00151357, 0.0041806,
                  0.08760181, -0.06353104),
    "ECE": (-4.3619472, 0.02789286, 0.25391759, -0.00185119, 0.00512636, 0.0, 0.0),
    "OCD_cores": (4.03916815, -0.03091248, -0.23245617, 0.00152155, -0.00419601,
                  -0.08434317, 0.06529789),
    "OCD": (4.29308859, -0.02662001, -0.26009513, 0.00185438, -0.005129, 0.0, 0.0),
    "LNI_cores": (-5.37408605, 0.01061319, 0.22266602, -0.00159928, 0.00441175,
                  0.05972006, -0.09466818),
    "LNI": (-5.83057151, 0.00521158, 0.18754729, -0.00122617, 0.0033653, 0.0, 0.0),
    "SVI_cores": (-6.11427809, 0.02483733, 0.1475095, -8.5465e-4, 0.00234133,
                  0.07682603, -0.10972019),
    "SVI": (-5.96514874, 0.01303056, 0.16554996, -9.5842e-4, 0.00262538, 0.0, 0.0),
    "BCR_cores": (6.27240656, -0.0055112, -0.44297705, 0.003802, -0.01059234,
                  -0.04892766, 0.04203013),
    "BCR": (6.34845727, -0.00211646, -0.42475212, 0.00353219, -0.0098305, 0.0, 0.0),
}
BCR_SCALE = {"BCR_cores": 1.04739984, "BCR": 1.09196115}

MAX_CORES = 20


def psa_splines(psa: float) -> tuple[float, float]:
    """The two restricted-cubic-spline terms on PSA, knots (0.2, 4.8, 7.35, 307).

        sp1 = max(psa-k1,0)^3 - max(psa-k3,0)^3·(k4-k1)/(k4-k3) + max(psa-k4,0)^3·(k3-k1)/(k4-k3)
        sp2 = max(psa-k2,0)^3 - max(psa-k3,0)^3·(k4-k2)/(k4-k3) + max(psa-k4,0)^3·(k3-k2)/(k4-k3)

    **Unnormalised.** A first version of this divided both terms by `(k4-k1)²`, which is the
    conventional scaling in some spline formulations and is not what these coefficients were
    fitted against: it drove case B's extracapsular-extension estimate to 99.98% where the
    published calculator gives 93%. The reference vectors caught it immediately, which is the
    argument for having them.
    """
    k1, k2, k3, k4 = PSA_KNOTS

    def cube(value: float, knot: float) -> float:
        return max(value - knot, 0.0) ** 3

    first = (
        cube(psa, k1)
        - cube(psa, k3) * (k4 - k1) / (k4 - k3)
        + cube(psa, k4) * (k3 - k1) / (k4 - k3)
    )
    second = (
        cube(psa, k2)
        - cube(psa, k3) * (k4 - k2) / (k4 - k3)
        + cube(psa, k4) * (k3 - k2) / (k4 - k3)
    )
    return first, second


def stage_key(stage: str) -> str | None:
    """The published band for a canonical stage token.

    The table has four bands -- T1 (reference), 2A, 2B, 2C and 3+ -- so `T1a`, `T1b` and `T1c`
    all sit at zero, the T2 sub-stages are kept apart because the model distinguishes them,
    and anything T3 or above takes the single `3+` coefficient.
    """
    token = stage.upper().lstrip("C")
    if token.startswith("T1"):
        return "T1"
    if token[:2] == "T2" and token[2:3].lower() in ("a", "b", "c"):
        return "T2" + token[2:3].lower()
    if token[:2] in ("T3", "T4"):
        return "T3"
    return None


def _linear(
    model: str,
    *,
    age_years: float,
    psa_ng_ml: float,
    grade_group: int,
    band: str,
    positive_cores: int | None,
    negative_cores: int | None,
) -> float:
    intercept, age, psa, sp1, sp2, positive, negative = TERMS[model]
    first, second = psa_splines(psa_ng_ml)
    total = (
        intercept
        + age * age_years
        + psa * psa_ng_ml
        + sp1 * first
        + sp2 * second
        + GRADE[model][grade_group - 1]
        + STAGE[model][band]
    )
    if positive_cores is not None and negative_cores is not None:
        total += positive * min(positive_cores, MAX_CORES)
        total += negative * min(negative_cores, MAX_CORES)
    return total


def _logistic(value: float) -> float:
    return 100.0 * math.exp(value) / (1.0 + math.exp(value))


def eligibility(record: ClinicalRecord) -> tuple[Reason, ...]:
    """Why the published model does not apply to this patient."""
    reasons: list[Reason] = []
    if record.hormone_therapy is Tristate.yes or record.radiation_therapy is Tristate.yes:
        reasons.append(Reason.model_eligibility_not_met)
    if record.radical_prostatectomy_performed is Tristate.yes:
        reasons.append(Reason.post_treatment_context)
    if record.is_prostate_adenocarcinoma is Tristate.no:
        # Validated on adenocarcinoma. A confirmed other histology is not a gap the RCP can
        # close by ordering another test -- no amount of clinical data makes this model apply.
        reasons.append(Reason.not_prostate_adenocarcinoma)
    return tuple(reasons)


def evaluate(record: ClinicalRecord) -> NomogramResult:
    """The five verified preoperative endpoints, or the reason there are none."""
    blocked = eligibility(record)
    if blocked:
        return not_applicable(
            model_family=FAMILY, model_id=MODEL_ID, display_name=DISPLAY, reasons=blocked
        )

    invalid = record.invalid_ranges()
    if invalid:
        return NomogramResult(
            model_family=FAMILY,
            model_id=MODEL_ID,
            display_name=DISPLAY,
            status=Status.not_possible,
            missing_fields=invalid,
            reason_codes=(Reason.invalid_input_range,),
        )

    age_years = record.age_years
    psa_ng_ml = record.psa_ng_ml
    grade_group = record.gleason_grade_group
    band = stage_key(record.clinical_t_stage_dre) if record.clinical_t_stage_dre else None

    missing: list[str] = []
    if age_years is None:
        missing.append("age_years")
    if psa_ng_ml is None:
        missing.append("psa_ng_ml")
    if grade_group is None:
        missing.append("gleason_primary_and_secondary")
    if band is None:
        missing.append("clinical_t_stage_dre")
    if missing or age_years is None or psa_ng_ml is None or grade_group is None or band is None:
        return not_possible(
            model_family=FAMILY, model_id=MODEL_ID, display_name=DISPLAY,
            missing=tuple(missing), provenance=PROVENANCE,
        )

    # Unknown is not "no". Checked after the inputs so the refusal names everything at once.
    # Histology joins the same check as hormone and radiation therapy: all three are
    # eligibility questions the published form asks explicitly, and a dossier silent on any
    # of them cannot be shown eligible.
    if (
        record.hormone_therapy.is_unknown
        or record.radiation_therapy.is_unknown
        or record.is_prostate_adenocarcinoma.is_unknown
    ):
        unconfirmed = tuple(
            name for name, value in (
                ("hormone_therapy", record.hormone_therapy),
                ("radiation_therapy", record.radiation_therapy),
                ("prostate_adenocarcinoma_confirmed", record.is_prostate_adenocarcinoma),
            ) if value.is_unknown
        )
        return not_possible(
            model_family=FAMILY, model_id=MODEL_ID, display_name=DISPLAY,
            missing=unconfirmed, reasons=(Reason.eligibility_unconfirmed,),
            provenance=PROVENANCE,
        )

    positive_cores = record.systematic_positive_cores
    negative_cores = record.systematic_negative_cores
    with_cores = positive_cores is not None and negative_cores is not None
    suffix = "_cores" if with_cores else ""
    def predictor(model: str) -> float:
        return _linear(
            model,
            age_years=age_years,
            psa_ng_ml=psa_ng_ml,
            grade_group=grade_group,
            band=band,
            positive_cores=positive_cores if with_cores else None,
            negative_cores=negative_cores if with_cores else None,
        )

    estimates = {
        f"{label.lower()}_percent": _logistic(predictor(label + suffix))
        for label in ("OCD", "ECE", "LNI", "SVI")
    }
    bcr = predictor("BCR" + suffix)
    scale = BCR_SCALE["BCR" + suffix]
    free_at_5y = 1.0 / (1.0 + (math.exp(-bcr) * 5.0) ** (1.0 / scale))
    estimates["progression_free_5y_percent"] = 100.0 * free_at_5y

    used = {
        "age_years": f"{age_years:g}",
        "psa_ng_ml": f"{psa_ng_ml:g}",
        "gleason_primary": str(record.gleason_primary),
        "gleason_secondary": str(record.gleason_secondary),
        "gleason_grade_group": str(grade_group),
        "clinical_t_stage_dre": str(record.clinical_t_stage_dre),
        "stage_band_used": band,
        "model_variant": "cores" if with_cores else "full",
    }
    if with_cores:
        used["systematic_positive_cores"] = str(record.systematic_positive_cores)
        used["systematic_negative_cores"] = str(record.systematic_negative_cores)

    return calculated(
        model_family=FAMILY, model_id=MODEL_ID, display_name=DISPLAY,
        estimates=estimates, inputs_used=used, provenance=PROVENANCE,
    )
