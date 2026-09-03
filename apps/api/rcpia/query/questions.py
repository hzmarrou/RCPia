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

# The second question every agent asks, alongside its own.
#
# **Why a second question rather than a wider first one.** The nutrition, cachexia and geriatric
# guidance is in the corpus, it is retrieval-eligible, and the filter admits it: those passages
# carry `disease_setting = not_specified` with reason `general_principle`. Measured, an explicit
# supportive-care question against exactly the `medical_oncology` + MIBC filter returns ESMO
# cachexia five times, ESPEN three times and ASPEN twice. Nothing blocks them.
#
# What kept them out of every run so far is ranking, and ranking is the retriever working
# correctly: a question about cisplatin eligibility *should* rank a cisplatin decision table
# above cachexia guidance. No wording reaches both subjects, because they are two subjects. The
# fix is a second retrieval with its own slots, not a longer first one.
#
# **The same question for every role, and a different answer for each.** The filter still
# carries `agent_role`, so the supportive search returns what that specialist is permitted to
# read: medical oncology reaches SIOG screening, ESMO-SIOG and ESMO cachexia; urology, which is
# not admitted to those, reaches SIOG bladder and ESPEN's *surgical* nutrition guidance instead.
# One question, specialised by the filter rather than by its wording.
#
# The phrasing names the instruments the dossiers actually record — MNA and G8 — because those
# are the values an agent is asked to interpret, and a passage that defines the instrument is
# what grounds the interpretation.
SUPPORTIVE_QUESTION = (
    "Dénutrition, fragilité et vulnérabilité gériatrique avant traitement anticancéreux : "
    "dépistage nutritionnel et MNA, dépistage G8 et évaluation gériatrique approfondie, "
    "support nutritionnel et adaptation du traitement chez le sujet âgé."
)

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
    # Trimodal bladder preservation is the option this speciality owns and the other two do not:
    # urology asks about cystectomy, medical oncology about cisplatin eligibility, and the
    # per-setting question names `préservation vésicale trimodale` in one clause among four. The
    # selection criteria, the dose and the concomitant radiosensitiser are what decide whether
    # the option is real for a given patient, and none of them is reachable from the others'
    # wording.
    ("radiation_oncology", "bladder_mibc_non_metastatic"): (
        "Préservation vésicale trimodale pour TVIM non métastatique : critères de sélection des "
        "candidats, résection transuréthrale maximale, radiothérapie et radiosensibilisation "
        "concomitante, dose et fractionnement, et surveillance après conservation"
    ),
    # **This pair does not currently fire, and the reasoning that chose it was wrong.** It was
    # mapped on the unspecified-risk setting on the grounds that a risk band would rarely be
    # derived — which is true — but measurement then showed that 38 of 40 stored prostate records
    # derive *no setting at all*, not the unspecified-risk one. Kept because it is correct for a
    # record that does derive it, and extraction is expected to improve; the patients it was
    # meant to serve are reached by `ROLE_GENERIC_QUESTIONS` below instead.
    # The two stored records that *do* derive a prostate setting land here, and all three
    # specialities were sharing the per-setting question on them. Only radiotherapy is mapped:
    # urology and medical oncology sharing a question on this setting predates this agent and
    # is a gap in the table, not something wiring a third speciality should decide.
    ("radiation_oncology", "prostate_locally_advanced_non_metastatic"): (
        "Radiothérapie du cancer de la prostate localement avancé : radiothérapie externe avec "
        "suppression androgénique longue, irradiation ganglionnaire pelvienne, volumes cibles et "
        "dose, et alternatives à la chirurgie"
    ),
    ("radiation_oncology", "prostate_nonmetastatic_unspecified_risk"): (
        "Radiothérapie du cancer de la prostate non métastatique : radiothérapie externe et "
        "curiethérapie, hypofractionnement, volumes cibles et irradiation ganglionnaire "
        "pelvienne, durée de la suppression androgénique associée selon le groupe de risque"
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


# The organ asked by a specialist, for the patients whose stage could not be derived.
#
# **This layer exists because the setting is missing far more often than the design assumed.**
# Measured across the stored dossiers: every bladder record derives a setting — 36 muscle-
# invasive, 18 non-muscle-invasive — but **38 of 40 prostate records derive none**, because the
# risk group needs Gleason, PSA and stage together and those are the fields the extraction most
# often leaves incomplete. Without this level all three specialists fell through to one identical
# generic question on 38 of 40 prostate patients, which is precisely the defect the role layer
# was built to remove: two specialists reading the same ten passages are not two opinions.
#
# The role-and-setting table above could not fix this on its own. A pair is keyed by a setting,
# and there is no setting here to key it by.
#
# Only prostate is mapped, and only for the wired specialities, following the same discipline as
# the table above: bladder derives a setting on every record measured, so a bladder entry would
# be an untested string that never runs.
ROLE_GENERIC_QUESTIONS: dict[tuple[str, str], str] = {
    ("urology_oncology", "prostate"): (
        "Traitement local du cancer de la prostate : prostatectomie totale et curage "
        "ganglionnaire, surveillance active, thérapie focale, indications et contre-indications "
        "chirurgicales selon le groupe de risque"
    ),
    ("medical_oncology", "prostate"): (
        "Traitement systémique du cancer de la prostate : suppression androgénique, "
        "intensification par inhibiteur du récepteur aux androgènes ou docétaxel, éligibilité et "
        "contre-indications, santé osseuse sous hormonothérapie"
    ),
    ("radiation_oncology", "prostate"): (
        "Radiothérapie du cancer de la prostate : radiothérapie externe et curiethérapie, "
        "hypofractionnement, irradiation ganglionnaire pelvienne, radiothérapie adjuvante ou de "
        "rattrapage après prostatectomie, durée de la suppression androgénique associée"
    ),
}


def question_for(
    cancer_type: str, setting: str | None, agent_role: str | None = None
) -> str:
    """The search question for this patient, as asked by this specialist.

    Four levels, most specific first:

    1. **Setting and role** — what this specialist evaluates in this clinical situation.
    2. **Setting alone** — the situation, asked role-blind.
    3. **Organ and role** — the specialist's own subject, when no setting could be derived.
    4. **The organ's generic question** — when nothing else applies.

    Each fallback is a real search, not a failure: level 2 is what every agent asked before
    roles were mapped, and level 4 is what every agent asked before settings were. An unmapped
    pair is therefore a gap in coverage rather than a defect, which is why nothing here raises.

    **Level 3 sits below level 2 deliberately.** A question that names the patient's actual
    clinical situation beats one that names only the speciality, even asked role-blind — the
    setting is what the guidelines are organised by. Level 3 is for the patients who have no
    derived setting at all, and on the stored prostate records that is almost all of them.
    """
    if setting is not None:
        if agent_role is not None:
            by_role = ROLE_SETTING_QUESTIONS.get((agent_role, setting))
            if by_role is not None:
                return by_role
        mapped = SETTING_QUESTIONS.get(setting)
        if mapped is not None:
            return mapped
    if agent_role is not None:
        by_organ = ROLE_GENERIC_QUESTIONS.get((agent_role, cancer_type))
        if by_organ is not None:
            return by_organ
    return GENERIC_QUESTIONS.get(
        cancer_type,
        f"Stratégie thérapeutique pour un cancer de type {cancer_type} : options de traitement, "
        "indications et contre-indications.",
    )
