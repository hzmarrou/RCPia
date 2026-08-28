"""The question the corpus is searched with, composed from the patient's derived setting.

**Why this module exists.** Until now every dossier was searched with one generic string per
organ — *"Proposition de stratégie thérapeutique pour un cancer de prostate : options de
traitement, indications et contre-indications."* Measured across fourteen dossiers, that question
returned **ten AFU passages out of ten** for prostate, and nine of the corpus's thirty-seven
documents were cited in total. `eau.prostate.2026` (445 eligible passages),
`nccn.prostate.6.2026` (265) and `esmo.prostate-advanced.2020` were never retrieved once.

**The cause was the question, not the corpus and not the retriever.** Probed directly with a
question that names the clinical situation — *"Quel traitement systémique de première ligne pour
un cancer de la prostate métastatique hormonosensible à forte charge tumorale ?"* — retrieval
returns `eau.prostate.2026` first, `aua.prostate-advanced.2026` second,
`esmo.prostate-advanced.2020` third and `nccn.prostate.6.2026` fourth. The passages were always
reachable. The generic phrasing matched AFU's French section headings (*indications*,
*traitement chirurgical*, *radiothérapie*) as near-literal string matches, and AFU's prostate
guideline is a single 148-passage document spanning every stage at once, so it filled all ten
slots before a stage-specific decision table could compete.

**What changed.** The setting is already derived from the patient's own facts by
`rcpia.query.staging`; it was used to build the retrieval *filter* and then thrown away. It now
also names the question. A metastatic patient asks a metastatic question.

**Where the setting cannot be derived the question stays generic**, which is the same principle
the filter follows when it leaves a dimension unset: a broad question for a record that did not
determine a narrower one. The caller already reports an underivable setting in the agent's
constraints, so the agent is told its evidence is broad rather than being handed a
confident-looking narrow set chosen at random.

**The question also names the specialist.** Setting alone was not enough: with the urology and
the medical oncology agent run on the same two bladder dossiers, the ten passages handed to each
were identical on one and differed by one on the other. `agent_role` was doing its job as a
*permission* — 203 bladder passages admit medical oncology and not urology — but permission is
inert while both roles ask the same question. `ROLE_SETTING_QUESTIONS` closes that gap for the
pairs the current dossiers reach, and everything else falls through to the per-setting question.

**These strings are search queries, not clinical claims.** They are read by a retriever and by
nothing else; no part of a question reaches the fiche. A question naming a setting the patient
does not have would retrieve the wrong guidance, which is why each is derived rather than
chosen — but it cannot by itself put a wrong word in front of a clinician.
"""

# Setting *and* role. Measured before this table existed: running the urology and the medical
# oncology agent on the same two bladder dossiers, the passages handed to each were **identical
# 10 of 10** on one dossier and 9 of 10 on the other. `agent_role` gates what an agent *may*
# read — of the eligible bladder passages, 1,151 admit urology and 1,336 admit medical oncology,
# with 203 admitting only medical oncology — but the question decided what each *did* read, and
# the question was the same for both. A filter that permits more than the question asks for
# permits nothing in practice.
#
# The 203 medical-oncology-only passages are the nutrition, cachexia, geriatric-screening and
# palliative guidance — ESMO cachexia, SIOG, ESPEN, ASPEN, Pallia10. All of them carry
# `disease_setting = not_specified` with reason `general_principle`, which
# `ADMISSIBLE_UNSPECIFIED_REASONS` admits, so the filter has never been what kept them out.
# Only the ranking has, and ranking answers to the question.
#
# **Started where it fires.** Only the pairs the current dossiers actually reach are mapped;
# an unmapped pair falls through to the per-setting question below, which is a narrower search
# than the organ generic and was itself the previous improvement. Prostate follows the same
# pattern once its dossiers record a stage to derive from.
ROLE_SETTING_QUESTIONS: dict[tuple[str, str], str] = {
    ("urology_oncology", "bladder_mibc_non_metastatic"): (
        "Cystectomie radicale et dérivation urinaire pour TVIM non métastatique : indications, "
        "contre-indications chirurgicales, critères de résécabilité, et alternatives à la "
        "chirurgie"
    ),
    ("medical_oncology", "bladder_mibc_non_metastatic"): (
        "Chimiothérapie néoadjuvante pour TVIM non métastatique : éligibilité au cisplatine, "
        "alternatives au carboplatine, et optimisation pré-thérapeutique"
    ),
    ("urology_oncology", "bladder_nmibc_unspecified_risk"): (
        "Résection transuréthrale et seconde résection pour TVNIM : qualité et complétude de la "
        "résection, indications de cystectomie précoce, critères de résécabilité et surveillance "
        "endoscopique"
    ),
    ("medical_oncology", "bladder_nmibc_unspecified_risk"): (
        "Traitement endovésical de la TVNIM : instillations de BCG et de mitomycine, schéma "
        "d'entretien, conduite en cas d'échec du BCG, et optimisation pré-thérapeutique"
    ),
}

# One question per disease setting in the staging vocabulary. Each names the situation in the
# words the guidelines use for it, so the retriever has something to match beyond the organ.
#
# The four NMIBC risk bands are included although `staging.py` does not currently emit them: the
# corpus vocabulary and `SETTING_PARENTS` both carry them, and a mapping that silently lacked an
# entry would fall back to the generic question without anyone noticing.
SETTING_QUESTIONS: dict[str, str] = {
    # -- Prostate ---------------------------------------------------------------------------
    "prostate_metastatic_hormone_sensitive": (
        "Cancer de la prostate métastatique hormonosensible : traitement systémique de première "
        "intention, suppression androgénique, intensification par inhibiteur du récepteur aux "
        "androgènes ou docétaxel, et contre-indications."
    ),
    "prostate_metastatic_castration_resistant": (
        "Cancer de la prostate métastatique résistant à la castration : options de traitement "
        "systémique, séquence thérapeutique, indications et contre-indications."
    ),
    "prostate_nonmetastatic_castration_resistant": (
        "Cancer de la prostate non métastatique résistant à la castration : indications de "
        "traitement, temps de doublement du PSA, options systémiques et contre-indications."
    ),
    "prostate_node_positive_non_metastatic": (
        "Cancer de la prostate avec atteinte ganglionnaire pelvienne sans métastase à distance : "
        "traitement locorégional, hormonothérapie associée et contre-indications."
    ),
    "prostate_locally_advanced_non_metastatic": (
        "Cancer de la prostate localement avancé non métastatique : radiohormonothérapie, "
        "traitement chirurgical, indications et contre-indications."
    ),
    "prostate_localized_high_risk": (
        "Cancer de la prostate localisé à haut risque : prostatectomie, radiothérapie et "
        "hormonothérapie longue, curage ganglionnaire, indications et contre-indications."
    ),
    "prostate_localized_intermediate_risk": (
        "Cancer de la prostate localisé de risque intermédiaire : prostatectomie, radiothérapie, "
        "curiethérapie, surveillance active, indications et contre-indications."
    ),
    "prostate_localized_low_risk": (
        "Cancer de la prostate localisé de faible risque : surveillance active, thérapie focale, "
        "prostatectomie, radiothérapie, indications et contre-indications."
    ),
    "prostate_nonmetastatic_unspecified_risk": (
        "Cancer de la prostate non métastatique : options de traitement local, surveillance "
        "active, indications et contre-indications selon le groupe de risque."
    ),
    # -- Bladder ----------------------------------------------------------------------------
    "bladder_metastatic": (
        "Cancer de la vessie métastatique : chimiothérapie de première ligne à base de sels de "
        "platine, éligibilité au cisplatine, immunothérapie, entretien et contre-indications."
    ),
    "bladder_mibc_non_metastatic": (
        "Tumeur de vessie infiltrant le muscle non métastatique : cystectomie radicale, "
        "chimiothérapie néoadjuvante à base de cisplatine, préservation vésicale trimodale, "
        "indications et contre-indications."
    ),
    "bladder_node_positive_non_metastatic": (
        "Cancer de la vessie avec atteinte ganglionnaire sans métastase à distance : traitement "
        "systémique, curage et cystectomie, indications et contre-indications."
    ),
    "bladder_locally_advanced_non_metastatic": (
        "Cancer de la vessie localement avancé envahissant la paroi pelvienne : options de "
        "traitement, résécabilité, traitement systémique et contre-indications."
    ),
    "bladder_nmibc_unspecified_risk": (
        "Tumeur de vessie n'infiltrant pas le muscle : résection transuréthrale, instillations "
        "endovésicales de BCG ou de mitomycine, indications et contre-indications selon le "
        "groupe de risque."
    ),
    "bladder_nmibc_cis": (
        "Carcinome in situ de la vessie : instillations de BCG, schéma d'entretien, échec du "
        "BCG, cystectomie, indications et contre-indications."
    ),
    "bladder_nmibc_low_risk": (
        "Tumeur de vessie n'infiltrant pas le muscle de faible risque : instillation "
        "postopératoire précoce, surveillance, indications et contre-indications."
    ),
    "bladder_nmibc_intermediate_risk": (
        "Tumeur de vessie n'infiltrant pas le muscle de risque intermédiaire : instillations de "
        "mitomycine ou de BCG, seconde résection, indications et contre-indications."
    ),
    "bladder_nmibc_high_risk": (
        "Tumeur de vessie n'infiltrant pas le muscle à haut risque : BCG avec entretien, seconde "
        "résection, cystectomie précoce, indications et contre-indications."
    ),
    "bladder_nmibc_very_high_risk": (
        "Tumeur de vessie n'infiltrant pas le muscle de très haut risque : cystectomie précoce "
        "contre BCG, échec du BCG, indications et contre-indications."
    ),
}

# What to ask when the record did not determine a setting. Deliberately the phrasing the fiche
# uses for its own section 1, so a broad search still bears on the question the document poses.
GENERIC_QUESTIONS: dict[str, str] = {
    "bladder": (
        "Proposition de stratégie thérapeutique pour un cancer de vessie : options de "
        "traitement, indications et contre-indications."
    ),
    "prostate": (
        "Proposition de stratégie thérapeutique pour un cancer de prostate : options de "
        "traitement, indications et contre-indications."
    ),
}


def question_for(
    cancer_type: str, setting: str | None, agent_role: str | None = None
) -> str:
    """The search question for this patient, as asked by this specialist.

    Three levels, most specific first:

    1. **Setting and role** — what this specialist evaluates in this clinical situation.
    2. **Setting alone** — the situation, asked role-blind.
    3. **The organ's generic question** — when no setting could be derived.

    Each fallback is a real search, not a failure: level 2 is what every agent asked before
    roles were mapped, and level 3 is what every agent asked before settings were. An unmapped
    pair is therefore a gap in coverage rather than a defect, which is why nothing here raises.
    """
    if setting is not None:
        if agent_role is not None:
            by_role = ROLE_SETTING_QUESTIONS.get((agent_role, setting))
            if by_role is not None:
                return by_role
        mapped = SETTING_QUESTIONS.get(setting)
        if mapped is not None:
            return mapped
    return GENERIC_QUESTIONS.get(
        cancer_type,
        f"Stratégie thérapeutique pour un cancer de type {cancer_type} : options de traitement, "
        "indications et contre-indications.",
    )
