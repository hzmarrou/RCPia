"""Which silences reach the board, and which are the division of labour working.

**Why this exists.** When one specialist raises a point and another never mentions it, the
synthesis records a *coverage gap*. That category exists because silence was previously read as
agreement — a fiche once reported that both specialists rejected carboplatin when only one had
mentioned it. But most silences are not gaps: a medical oncologist saying nothing about the type
of urinary diversion is staying in its own domain, and printing that costs the board attention
and buries the silences that matter. Measured across fifteen dossiers, roughly three quarters of
the 34 gaps were of that second kind.

**Why it is a table and not a computation.** The obvious mechanical rule — *an agent may address
any point it can ground in evidence it is allowed to read* — was implemented and measured before
being adopted, and it fails. Guideline documents are not scoped by speciality: the AUA MIBC
guideline covers surgery and chemotherapy together, so the medical oncology agent's own bundle
contains passages naming `bricker` and `entérocystoplastie`. It suppressed 35% of gaps and kept
the wrong ones. The corpus cannot settle this, because the guidelines do not divide the way a
tumour board divides. So the boundary is a clinical judgement, recorded here.

**The judgement behind each row** was given by a clinician against the measured inventory in
`docs/reviews/coverage-gap-decision.md`. The governing question was always: *is an agent of the
silent speciality expected to hold a view on this subject?*

**The split that matters.** Systemic therapy divides in two, and the division is the whole
point. When the urology agent proposes a pathway that runs through a drug — neoadjuvant
chemotherapy before cystectomy, hormonal therapy before prostatectomy — then *which* drug, and
whether it is contraindicated, is part of its own plan; its silence there is a hole in that
plan. When the subject is how a drug is administered, or which agent comes second in advanced
disease, the urologist's plan does not turn on it. The first is reported, the second is not.

**Nothing is deleted.** Suppressed gaps stay in the stored synthesis output; this decides only
what the rendered fiche shows. A suppressed gap is still auditable.
"""

import re
import unicodedata
from dataclasses import dataclass

# --- the subjects, and how a point is recognised as belonging to one --------------------------


@dataclass(frozen=True)
class Subject:
    """One subject in the policy table, and the words that place a point inside it."""

    key: str
    label: str
    # Any of these puts a point in this subject, unless an earlier subject claimed it first.
    terms: frozenset[str]


# A point about *administration or sequencing* is 1b even when it names a drug, so this subject
# is tested first and wins ties. Without that precedence, "la mitomycine nécessite une
# alcalinisation urinaire" would be classified by the drug name rather than by what it is about.
SYSTEMIC_TECHNIQUE = Subject(
    key="systemic_technique",
    label="Traitement systémique — technique d'administration et séquence",
    terms=frozenset({
        "alcalinisation", "modalites", "instillation", "instillations", "endovesicale",
        "endovesical", "ablation", "duree", "contact", "sequence", "ulterieure",
        "ulterieures", "entretien", "schema", "fenetre", "reevaluation", "preference",
        "resistance", "intensification", "ligne", "lignes",
    }),
)

# The drug decision inside a pathway the silent agent is itself proposing: *which* drug, and
# *whether the patient can take it*. This is the carboplatin shape.
#
# **Deliberately narrow.** A first version also carried the ordinary vocabulary of systemic
# therapy — `chimiotherapie`, `adjuvante`, `neoadjuvante`, `immunotherapie` — and swept in
# statements like *"absence d'indication à un traitement anticancéreux systémique"*, which name
# no substitution, no contraindication, and change nothing the urologist proposes. It reported
# 13 of 18 where the clinical judgement expected 5 or 6. The subject is now recognised by the
# decision being made, not by the fact that a drug is mentioned.
SYSTEMIC_CHOICE = Subject(
    key="systemic_choice",
    label="Traitement systémique — choix et éligibilité dans un parcours proposé",
    terms=frozenset({
        "substitut", "substitue", "substituee", "substitution", "remplacer", "remplace",
        "eligibilite", "eligible", "ineligible", "ineligibilite", "eligibles",
        "contreindication", "contreindique",
    }),
)

# Everything else about systemic therapy: whether it is indicated at all, how a course is
# reassessed, what comes next in advanced disease. The urologist's plan does not turn on these,
# so its silence is the division of labour rather than a hole.
SYSTEMIC_OTHER = Subject(
    key="systemic_other",
    label="Traitement systémique — indication générale et suites",
    terms=frozenset({
        "chimiotherapie", "immunotherapie", "systemique", "neoadjuvante", "neoadjuvant",
        "adjuvante", "adjuvant", "perioperatoire", "cytotoxique", "carboplatine",
        "cisplatine", "gemcitabine", "docetaxel", "mitomycine", "bcg",
    }),
)

HORMONAL = Subject(
    key="hormonal",
    label="Hormonothérapie et santé osseuse",
    terms=frozenset({
        "hormonotherapie", "castration", "androgenique", "adt", "abiraterone", "enzalutamide",
        "triptoreline", "gnrh", "antagoniste", "testosteronemie", "testosterone",
        "antiresorptif", "osseuse", "osseux", "radiopharmaceutique", "denosumab",
    }),
)

SURGERY = Subject(
    key="surgery",
    label="Chirurgie et dérivation urinaire",
    terms=frozenset({
        "cystectomie", "prostatectomie", "derivation", "bricker", "neovessie",
        "enterocystoplastie", "curage", "rtuv", "resection", "chirurgie", "chirurgical",
        "chirurgicale", "endoscopique", "exerese", "operabilite",
    }),
)

# **The words a radiotherapy decision is actually written in**, which is not the same set as the
# words for the treatment. The first version carried `radiotherapeutique` and `hypofractionnee`
# and missed both `radiothérapique` and `fractionnement` -- so *"choix précis du fractionnement
# radiothérapique et prudence vis-à-vis de l'ultrahypofractionnement"* was classified as `Autre`
# and adjudicated under no rule at all, on a batch where radiotherapy had just become a wired
# speciality. Dose, fractionation and target volumes are the substance of the decision, so the
# terms have to reach them.
#
# `dose` and `volume` are deliberately left out despite belonging to the subject: they appear in
# other specialities' sentences too -- *"un antirésorptif mensuel à dose de prévention"* is a
# hormonal point -- and this subject is tested before `HORMONAL`, so adding them would move
# points out of the pathway they belong to. A missed point defaults to reported; a stolen one is
# silently ruled by the wrong row.
RADIOTHERAPY = Subject(
    key="radiotherapy",
    label="Radiothérapie",
    terms=frozenset({
        "radiotherapie", "curietherapie", "trimodal", "trimodale", "irradiation",
        "radiochimiotherapie", "hypofractionnee", "radiotherapeutique", "radiotherapique",
        "radiotherapiques", "fractionnement", "hypofractionnement", "ultrahypofractionnement",
        "fractionne", "fractionnee", "stereotaxie", "stereotaxique", "ebrt",
    }),
)

STAGING = Subject(
    key="staging",
    label="Stadification, imagerie et anatomopathologie",
    terms=frozenset({
        "ctnm", "ptnm", "tnm", "stade", "gleason", "isup", "histologique",
        "anatomopathologique", "biopsie", "relecture", "irm", "scintigraphie", "psma",
        "imagerie", "resecabilite",
    }),
)

SUPPORTIVE = Subject(
    key="supportive",
    label="Soins de support, nutrition, gériatrie",
    terms=frozenset({
        "nutritionnel", "nutritionnelle", "denutrition", "cachexie", "geriatrique",
        "oncogeriatrique", "fragilite", "palliatif", "palliative", "tabagique", "sevrage",
        "support", "douleur", "sociale",
    }),
)

OTHER = Subject(key="other", label="Autre", terms=frozenset())

# The cytotoxic drug names, which settle one collision the word sets cannot.
#
# `castration` belongs to the hormonal pathway, and `resistance` and `preference` belong to
# sequencing — so *"prefer docetaxel if castration resistance is confirmed"* satisfies both
# subjects at once. It is a chemotherapy sequencing decision in advanced disease, which the
# clinical judgement suppresses, and reading it as the androgen-deprivation pathway would report
# it. Naming a cytotoxic is what separates the two: the ADT pathway is discussed without them.
CYTOTOXICS = frozenset({
    "docetaxel", "cabazitaxel", "cisplatine", "carboplatine", "gemcitabine", "mitomycine",
    "mvac", "paclitaxel", "etoposide",
})

# Order matters: the first subject whose terms appear claims the point. Technique precedes
# choice so that "how the drug is given" is not classified by the drug's name; surgery and
# radiotherapy precede the systemic subjects so that "adjuvant after cystectomy" is read as the
# systemic decision it is, only once the surgical subjects have declined it.
SUBJECTS: tuple[Subject, ...] = (
    SUPPORTIVE,
    RADIOTHERAPY,
    # The explicit drug decision is tested before anything that merely mentions a drug.
    SYSTEMIC_CHOICE,
    # Hormonal therapy precedes the technique subject, because its own vocabulary of sequencing
    # — *preferring a GnRH antagonist to avoid a flare*, *a bone-seeking radiopharmaceutical in a
    # later sequence* — is the androgen-deprivation pathway itself, not the administration of
    # someone else's drug. Left behind `systemic_technique`, both of those were suppressed while
    # the clinical judgement reports every silence on that pathway.
    HORMONAL,
    # Then administration and sequencing, so "how it is given" is not read as "which one".
    SYSTEMIC_TECHNIQUE,
    SYSTEMIC_OTHER,
    SURGERY,
    STAGING,
)

# --- the policy -------------------------------------------------------------------------------

REPORT = True
SUPPRESS = False

# (subject, silent speciality) -> whether the board sees it.
#
# Given by a clinician against the measured inventory. Where a subject is reportable whichever
# speciality stayed silent, both entries say so rather than the table carrying a wildcard: the
# next five specialities will add rows here, and a wildcard would silently answer for them.
POLICY: dict[tuple[str, str], bool] = {
    # 1a — the urologist proposes a pathway that runs through a drug, so the drug decision is
    # part of its own plan. This is the carboplatin case the whole category exists for.
    ("systemic_choice", "urology_oncology"): REPORT,
    # 1b — how a drug is administered, and what comes second in advanced disease. The
    # urologist's plan does not turn on these.
    ("systemic_technique", "urology_oncology"): SUPPRESS,
    ("systemic_other", "urology_oncology"): SUPPRESS,
    # 2 — surgical technique. A chemotherapy specialist having no view on which diversion to
    # build is the division of labour working. Same for the radiation oncologist.
    ("surgery", "medical_oncology"): SUPPRESS,
    ("surgery", "radiation_oncology"): SUPPRESS,
    # 3 — the urologist initiates the androgen-deprivation pathway, so its silence on that
    # pathway is a hole in its own plan. The radiation oncologist delivers ADT alongside
    # EBRT, so its silence there is a hole in its own plan too.
    ("hormonal", "urology_oncology"): REPORT,
    ("hormonal", "radiation_oncology"): REPORT,
    # 4 — staging completeness belongs to radiology and urology. The radiation oncologist
    # needs staging for target volumes.
    ("staging", "medical_oncology"): SUPPRESS,
    ("staging", "radiation_oncology"): REPORT,
    # 5 and 6 — supportive care is shared ground; both original specialities are expected to
    # address it. The radiation oncologist manages pain, nutrition during pelvic RT, and
    # smoking cessation.
    ("supportive", "medical_oncology"): REPORT,
    ("supportive", "urology_oncology"): REPORT,
    ("supportive", "radiation_oncology"): REPORT,
    # 7 — radiotherapy as a shared decision. Urology shares the bladder-preservation choice
    # with radiation oncology, so urology silent on radiotherapy is a gap. But medical
    # oncology silent on fractionation, dose, or target volumes is the division of labour:
    # those are not its domain.
    ("radiotherapy", "urology_oncology"): REPORT,
    ("radiotherapy", "medical_oncology"): SUPPRESS,
    # The radiation oncologist silent on a radiotherapy point raised by someone else. This is
    # the speciality that owns the subject saying nothing about it -- the shape the whole
    # category exists to catch, and the radiotherapy equivalent of the carboplatin case. It was
    # reaching the board on the default rather than on a rule, which is how
    # `("radiotherapy", "medical_oncology")` came to be wrong: a verdict nobody had given,
    # reading as one that had been.
    ("radiotherapy", "radiation_oncology"): REPORT,
    # 8 — systemic therapy. The radiation oncologist's pathway runs through irradiation, not
    # chemotherapy. Its silence on systemic drug choice, administration, or general
    # indication is division of labour.
    ("systemic_choice", "radiation_oncology"): SUPPRESS,
    ("systemic_technique", "radiation_oncology"): SUPPRESS,
    ("systemic_other", "radiation_oncology"): SUPPRESS,
}

# What to do with a combination nobody has ruled on.
#
# **Reported, not suppressed.** Hiding a gap because no one has decided it would be a silent
# choice made by an omission, and this project's rule throughout is that absence is visible.
# A wrongly reported gap costs a line on a page; a wrongly hidden one costs the thing the
# category was built to catch. The five specialities still to be wired will land here first.
UNRULED = REPORT


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFD", (text or "").lower())
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", stripped)


def subject_of(point: str) -> Subject:
    """Which subject a gap belongs to, from the words it uses.

    Two subjects are recognised by phrase rather than by word, because the word alone means the
    opposite half the time. `contre-indication` folds to two words and the fragment `contre`
    appears in ordinary prose. And `essai` carries both senses at once: *hors essai clinique*
    excludes the patient from a drug — the contraindication shape — while *dans le cadre d'un
    essai* says only where a technique belongs, which is 1b. Only the exclusion is matched here;
    the other reading falls through to the technique subject.
    """
    folded = _fold(point)
    words = set(folded.split())
    if "contre indication" in folded or "contre indique" in folded:
        return SYSTEMIC_CHOICE
    if "hors essai" in folded:
        return SYSTEMIC_CHOICE
    # Single-fraction palliative irradiation of a painful bone metastasis. `fraction` alone
    # cannot be a radiotherapy term -- *fraction d'éjection* is a cardiac measurement and would
    # drag a fitness assessment into this subject -- and without the phrase `osseuse` claims the
    # point for the hormonal pathway, which is tested later but matches first here.
    if "fraction unique" in folded:
        return RADIOTHERAPY
    # `HORMONAL` is tested before `SYSTEMIC_TECHNIQUE` (see `SUBJECTS`), which is right for the
    # androgen-deprivation pathway and wrong the moment a cytotoxic is named.
    order = SUBJECTS
    if words & CYTOTOXICS:
        order = tuple(s for s in SUBJECTS if s is not HORMONAL) + (HORMONAL,)
    for subject in order:
        if words & subject.terms:
            return subject
    return OTHER


def reports(point: str, silent_role: str) -> bool:
    """Whether this silence should reach the board."""
    return POLICY.get((subject_of(point).key, silent_role), UNRULED)


def explain(point: str, silent_role: str) -> tuple[str, bool, bool]:
    """Subject label, whether it is reported, and whether a rule actually covered it.

    The third value separates *decided* from *defaulted*, so a run can report how much of its
    output rests on judgements nobody has made yet.
    """
    subject = subject_of(point)
    key = (subject.key, silent_role)
    return subject.label, POLICY.get(key, UNRULED), key in POLICY
