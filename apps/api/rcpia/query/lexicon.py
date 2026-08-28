"""The vocabulary a question is searched with, keyed by what the question is about.

**This is where the hand-writing moves, not where it disappears.** The pilot's concepts were
written per question — thirteen bespoke term lists, which cannot scale past thirteen and which
make every score a ceiling, because a person had already solved the hardest part of the search.
The lexicon below is written per *disease setting* and per *clinical purpose* instead: a fixed
asset reused across every patient in that situation, rather than a fresh act of expertise for
each one. That is the difference between a system and a demonstration.

**Three vocabularies, because the corpus is 89% English and every question is French.** The
keyword leg runs French concepts against French passages and English against English; `neutral`
runs against both and is the only bridge. So the neutral list carries what survives either
stemmer unchanged — TNM codes, drug names, scores, grades — and it is built from the patient's
own record rather than from the lexicon, because that is where those tokens are.

**Nothing here is clinician-validated.** The terms are taken from the section headings and
recommendation text of the corpus itself, which is what the retrieval has to match against.
"""

import re
from collections.abc import Sequence

from rcpia.evidence.retrieval import Concepts

# Terms that name the clinical situation. French first, then English -- the two are not
# translations of each other but the vocabulary each corpus actually uses.
_SETTING_TERMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bladder_nmibc_unspecified_risk": (
        ("TVNIM", "tumeur de vessie n'infiltrant pas le muscle", "résection transurétrale",
         "instillation endovésicale"),
        ("non-muscle-invasive bladder cancer", "NMIBC", "TURBT", "intravesical therapy"),
    ),
    "bladder_nmibc_cis": (
        ("carcinome in situ", "CIS", "BCG"),
        ("carcinoma in situ", "CIS", "BCG"),
    ),
    "bladder_mibc_non_metastatic": (
        ("TVIM", "tumeur de vessie infiltrant le muscle", "cystectomie totale",
         "chimiothérapie néoadjuvante", "préservation vésicale"),
        ("muscle-invasive bladder cancer", "MIBC", "radical cystectomy",
         "neoadjuvant chemotherapy", "trimodality therapy", "bladder preservation"),
    ),
    "bladder_locally_advanced_non_metastatic": (
        ("tumeur localement avancée", "cystectomie", "traitement multimodal"),
        ("locally advanced", "radical cystectomy", "multimodal treatment"),
    ),
    "bladder_node_positive_non_metastatic": (
        ("atteinte ganglionnaire", "curage ganglionnaire", "chimiothérapie"),
        ("node-positive", "lymph node dissection", "chemotherapy"),
    ),
    "bladder_metastatic": (
        ("maladie métastatique", "chimiothérapie de première ligne", "immunothérapie"),
        ("metastatic urothelial carcinoma", "first-line systemic therapy", "immunotherapy",
         "checkpoint inhibitor"),
    ),
    "prostate_nonmetastatic_unspecified_risk": (
        ("cancer de la prostate localisé", "prostatectomie totale", "radiothérapie",
         "surveillance active"),
        ("localized prostate cancer", "radical prostatectomy", "radiotherapy",
         "active surveillance"),
    ),
    "prostate_localized_low_risk": (
        ("risque faible", "surveillance active"),
        ("low-risk", "active surveillance"),
    ),
    "prostate_localized_intermediate_risk": (
        ("risque intermédiaire", "prostatectomie totale", "radiothérapie",
         "hormonothérapie courte"),
        ("intermediate-risk", "radical prostatectomy", "radiotherapy",
         "short-term androgen deprivation"),
    ),
    "prostate_localized_high_risk": (
        ("haut risque", "prostatectomie élargie", "curage ganglionnaire étendu",
         "radiothérapie et hormonothérapie prolongée"),
        ("high-risk", "radical prostatectomy", "extended pelvic lymph node dissection",
         "long-term androgen deprivation"),
    ),
    "prostate_locally_advanced_non_metastatic": (
        ("localement avancé", "traitement multimodal", "hormonothérapie prolongée"),
        ("locally advanced", "multimodal therapy", "long-term androgen deprivation"),
    ),
    "prostate_node_positive_non_metastatic": (
        ("envahissement ganglionnaire", "hormonothérapie", "radiothérapie pelvienne"),
        ("node-positive", "androgen deprivation therapy", "pelvic radiotherapy"),
    ),
    "prostate_metastatic_hormone_sensitive": (
        ("métastatique hormonosensible", "suppression androgénique", "docétaxel"),
        ("metastatic hormone-sensitive prostate cancer", "mHSPC",
         "androgen deprivation therapy", "docetaxel", "androgen receptor pathway inhibitor"),
    ),
    "prostate_metastatic_castration_resistant": (
        ("résistant à la castration", "abiratérone", "enzalutamide", "cabazitaxel"),
        ("castration-resistant prostate cancer", "mCRPC", "abiraterone", "enzalutamide",
         "cabazitaxel"),
    ),
    "prostate_nonmetastatic_castration_resistant": (
        ("résistant à la castration non métastatique", "temps de doublement du PSA"),
        ("non-metastatic castration-resistant", "nmCRPC", "PSA doubling time"),
    ),
    "prostate_biochemical_recurrence": (
        ("récidive biologique", "radiothérapie de rattrapage"),
        ("biochemical recurrence", "salvage radiotherapy"),
    ),
}

# What the question is *for*. Added to the setting terms rather than replacing them.
_PURPOSE_TERMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "therapy_indication": (("indication thérapeutique", "traitement recommandé"),
                           ("treatment recommendation", "indication")),
    "modality_selection": (("choix du traitement", "alternatives thérapeutiques"),
                           ("treatment options", "choice of modality")),
    "diagnosis": (("diagnostic", "biopsie"), ("diagnosis", "biopsy")),
    "staging": (("bilan d'extension", "stadification"), ("staging", "extent of disease")),
    "risk_stratification": (("stratification du risque", "groupes de risque"),
                            ("risk stratification", "risk groups")),
    "pathology": (("anatomopathologie", "histologie"), ("pathology", "histology")),
    "follow_up": (("surveillance", "suivi"), ("follow-up", "surveillance")),
    "safety": (("contre-indication", "toxicité", "effets indésirables"),
               ("contraindication", "toxicity", "adverse effects", "eligibility")),
    "supportive_care": (("soins de support", "prise en charge nutritionnelle"),
                        ("supportive care", "nutritional support")),
    "treatment_delivery": (("modalités de traitement",), ("treatment delivery", "technique")),
    "treatment_response": (("réponse au traitement",), ("treatment response",)),
    "screening": (("dépistage",), ("screening", "early detection")),
    "genetics": (("oncogénétique",), ("germline testing", "genetic testing")),
}

# What a specialist brings to the question, beyond the setting. Deliberately short: the role is
# already a filter dimension, and repeating it as vocabulary would narrow twice.
_ROLE_TERMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "renal_safety": (("fonction rénale", "clairance de la créatinine", "éligibilité au cisplatine"),
                     ("renal function", "creatinine clearance", "cisplatin eligibility")),
    "geriatric_frailty": (("évaluation oncogériatrique", "fragilité", "G8"),
                          ("geriatric assessment", "frailty", "comorbidity")),
    "nutrition": (("dénutrition", "MNA", "albumine"),
                  ("malnutrition", "nutritional status", "albumin")),
    "palliative_supportive": (("soins palliatifs", "qualité de vie"),
                              ("palliative care", "quality of life")),
    "pathology": (("compte rendu anatomopathologique",), ("pathology report", "specimen")),
    "radiology": (("imagerie", "IRM", "scanner"), ("imaging", "MRI", "CT")),
    "radiation_oncology": (("radiothérapie", "fractionnement"), ("radiotherapy", "fractionation")),
}

# Tokens that mean the same thing in both corpora and therefore bridge the language split.
_TNM = re.compile(r"\b[cp]?T(?:is|[0-4a])[a-d]?\b|\bN[0-3]\b|\bM[01][a-c]?\b", re.IGNORECASE)
_GLEASON = re.compile(r"\b\d\s*\+\s*\d\b")
_DRUGS = (
    "cisplatine", "cisplatin", "carboplatine", "carboplatin", "gemcitabine", "BCG",
    "mitomycine", "mitomycin", "docétaxel", "docetaxel", "abiratérone", "abiraterone",
    "enzalutamide", "cabazitaxel", "pembrolizumab", "nivolumab", "avelumab",
    "enfortumab vedotin", "erdafitinib", "apalutamide", "darolutamide", "triptoréline",
)


def neutral_terms(values: Sequence[str | None]) -> tuple[str, ...]:
    """The language-neutral tokens present in this patient's own record.

    Built from the record rather than from the lexicon, because TNM codes, grades and drug
    names are patient facts. They are also the only route a French question has to an English
    passage through the keyword leg, so an empty neutral list is a real loss of reach.
    """
    found: list[str] = []
    haystack = " ".join(v for v in values if v)
    for match in _TNM.finditer(haystack):
        found.append(match.group(0))
    for match in _GLEASON.finditer(haystack):
        found.append(match.group(0).replace(" ", ""))
    lowered = haystack.lower()
    found.extend(drug for drug in _DRUGS if drug.lower() in lowered)
    # Order-preserving dedupe, case-insensitively, so `cT2` and `ct2` do not both survive.
    seen: set[str] = set()
    unique: list[str] = []
    for term in found:
        if term.lower() not in seen:
            seen.add(term.lower())
            unique.append(term)
    return tuple(unique)


def build_concepts(
    *,
    disease_setting: str | None,
    clinical_purpose: Sequence[str] = (),
    agent_role: str | None = None,
    record_values: Sequence[str | None] = (),
) -> Concepts:
    """The concepts to search with, assembled from the situation rather than the question.

    An unknown setting yields no setting terms rather than a guess. The neutral list still
    carries the patient's own codes, so such a query is weakened rather than empty.
    """
    french: list[str] = []
    english: list[str] = []

    def add(table: dict[str, tuple[tuple[str, ...], tuple[str, ...]]], key: str | None) -> None:
        if key and key in table:
            fr, en = table[key]
            french.extend(fr)
            english.extend(en)

    add(_SETTING_TERMS, disease_setting)
    for purpose in clinical_purpose:
        add(_PURPOSE_TERMS, purpose)
    add(_ROLE_TERMS, agent_role)

    return Concepts(
        french=tuple(dict.fromkeys(french)),
        english=tuple(dict.fromkeys(english)),
        neutral=neutral_terms(record_values),
    )
