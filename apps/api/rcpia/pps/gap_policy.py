"""Which silences reach the board, and which are the division of labour working.

**Why this exists.** When one specialist raises a point and another never mentions it, the
synthesis records a *coverage gap*. That category exists because silence was previously read as
agreement — a fiche once reported that both specialists rejected carboplatin when only one had
mentioned it. But most silences are not gaps: a medical oncologist saying nothing about the type
of urinary diversion is staying in its own domain, and printing that costs the board attention
and buries the silences that matter. Measured across fifteen dossiers, roughly three quarters of
the 34 gaps were of that second kind.

**Why it is a declaration and not a computation.** The obvious mechanical rule — *an agent may
address any point it can ground in evidence it is allowed to read* — was implemented and measured
before being adopted, and it fails. Guideline documents are not scoped by speciality: the AUA MIBC
guideline covers surgery and chemotherapy together, so the medical oncology agent's own bundle
contains passages naming `bricker` and `entérocystoplastie`. It suppressed 35% of gaps and kept the
wrong ones. The corpus cannot settle this, because the guidelines do not divide the way a tumour
board divides. So the boundary is a clinical judgement, recorded here.

**The judgement behind each remit** was given by a clinician — in the agent's own brain, whose
*"Vous évaluez"* and *"Vous ne décidez pas"* sections are exactly this statement — and checked
against the measured inventory in `docs/reviews/coverage-gap-decision.md`. The governing question
was always the same one: *is an agent of the silent speciality expected to hold a view on this
subject?* Asked of the speciality once, rather than of every pair it can appear in.

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

# **Split from one `staging` subject, because two specialities disown each other's half of it.**
# The radiology brain says *"vous ne décidez pas de l'interprétation des lames
# anatomopathologiques"*; the pathology brain says *"vous ne décidez pas de la stadification
# clinique (cTNM) à partir de l'imagerie"*. Their remits are stated as disjoint, and a single
# `staging` subject merges exactly the two things they are disjoint about — so it could not say
# that the pathologist's silence on an MRI finding is the division of labour while its silence
# on a Gleason score is a hole.
IMAGING = Subject(
    key="imaging",
    label="Imagerie et stadification clinique",
    terms=frozenset({
        "ctnm", "irm", "tdm", "scanner", "uroscanner", "scintigraphie", "psma", "tep",
        "imagerie", "radiologique", "pirads", "resecabilite", "adenopathie",
        "adenopathies", "tnm", "stade",
    }),
)

HISTOPATHOLOGY = Subject(
    key="histopathology",
    label="Anatomopathologie",
    terms=frozenset({
        "ptnm", "gleason", "isup", "histologique", "histologie", "anatomopathologique",
        "anatomopathologie", "biopsie", "biopsies", "carottes", "relecture",
        "immunohistochimie", "emboles", "variante", "variantes",
    }),
)

# **Split from one `supportive` subject, because three specialities would otherwise be
# indistinguishable.** Nutrition, oncogeriatrics and palliative care all hold supportive-care
# remits, and merged they produce three identical lines on a fiche saying a point was unaddressed
# by three agents, none of which distinguishes *"the nutritionist said nothing about cachexia"*
# from *"the palliative agent said nothing about pain"*.
#
# Nutrition is tested first of the three deliberately. The oncogeriatric brain evaluates
# nutritional state as part of its own assessment, while the nutrition brain says it does *not*
# perform the global geriatric assessment — so the overlap runs one way, and classifying an
# ambiguous point as `nutrition` keeps both specialities answerable for it, where `frailty`
# would excuse the nutritionist. The more conservative reading is the one that stays visible.
NUTRITION = Subject(
    key="nutrition",
    label="Nutrition clinique",
    terms=frozenset({
        "nutritionnel", "nutritionnelle", "nutrition", "denutrition", "denutri", "cachexie",
        "sarcopenie", "albuminemie", "prealbumine", "mna", "amaigrissement", "dietetique",
    }),
)

FRAILTY = Subject(
    key="frailty",
    label="Oncogériatrie et fragilité",
    terms=frozenset({
        "geriatrique", "oncogeriatrique", "geriatrie", "fragilite", "fragile", "g8", "adl",
        "iadl", "autonomie", "cognitive", "cognitif", "chute", "chutes", "comorbidites",
    }),
)

PALLIATIVE = Subject(
    key="palliative",
    label="Soins de support et palliatifs",
    terms=frozenset({
        "palliatif", "palliative", "palliatifs", "tabagique", "sevrage", "support",
        "douleur", "douloureuse", "antalgique", "antalgie", "symptomes", "sociale",
        "psychologique", "pallia",
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
    # The three supportive subjects keep the precedence the single `supportive` subject had.
    NUTRITION,
    FRAILTY,
    PALLIATIVE,
    RADIOTHERAPY,
    # Named investigations precede the treatment pathways that share their anatomy words: a
    # bone scan is an investigation, and `osseuse` in the hormonal set would otherwise read
    # "scintigraphie osseuse" as a statement about bone health under androgen deprivation.
    # Radiotherapy still wins, so "volumes cibles définis sur la TDM de simulation" stays a
    # radiotherapy decision.
    IMAGING,
    HISTOPATHOLOGY,
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
)

# --- the policy -------------------------------------------------------------------------------

REPORT = True
SUPPRESS = False

# What each speciality is expected to hold a view on.
#
# **One declaration per speciality, not one ruling per pair.** The first version of this policy
# was a grid of `(subject, silent speciality)` verdicts. Measured against itself, that grid
# turned out to carry no cell-specific information at all: every one of its eighteen rulings was
# reproducible from a single set per speciality, because the question being answered was never
# about the pair. It was always *is this subject inside that speciality's remit* — a property of
# the speciality alone. The grid was the expensive way of writing it down, it grows as
# specialities × subjects, and its omissions are invisible: a missing cell looks exactly like a
# cell nobody has reached yet.
#
# **And the remit is already written, by a clinician, in the agent's own brain.** Every brain in
# `docs/implementation/` has the same two sections — *"Vous évaluez … Cela inclut :"* and *"Vous
# ne décidez pas :"*. That is the remit. The sets below are transcriptions of those sections into
# subject keys, and they belong beside the brain they came from; keeping them here is what lets
# the two drift, and reviewing them together is what stops it.
#
# A speciality with no entry is not ruled on: its silences reach the board, which is what the
# five unwired specialities need until their remit is reviewed.
REMIT: dict[str, frozenset[str]] = {
    # Proposes the surgical pathway and initiates androgen deprivation, so the drug decision
    # inside a pathway it is proposing — *which* drug, and whether the patient can take it — is
    # part of its own plan. Bladder preservation is the alternative to the cystectomy it offers,
    # so radiotherapy is shared ground. How a drug is administered, and what comes second in
    # advanced disease, are not: its plan does not turn on them.
    "urology_oncology": frozenset({
        "surgery", "systemic_choice", "hormonal", "radiotherapy", "imaging",
        "histopathology", "nutrition", "frailty", "palliative",
    }),
    # Owns systemic therapy in all three of its senses. Surgical technique, target volumes and
    # the reading of a slide belong to others.
    "medical_oncology": frozenset({
        "systemic_choice", "systemic_technique", "systemic_other", "hormonal",
        "nutrition", "frailty", "palliative",
    }),
    # *"Vous évaluez la place de la radiothérapie … l'association radiothérapie-hormonothérapie:
    # durée de l'ADT selon le risque"*, and staging because target volumes depend on it.
    # *"Vous ne décidez pas de la technique chirurgicale … du choix des protocoles de
    # chimiothérapie ou d'immunothérapie systémique."*
    "radiation_oncology": frozenset({
        "radiotherapy", "hormonal", "imaging", "histopathology",
        "nutrition", "frailty", "palliative",
    }),
    # --- Declared from the written brains, not yet exercised by a run. ------------------------
    #
    # These five are transcribed from brains a clinician wrote and have not been measured
    # against a batch, because their agents are not wired. They are here rather than absent so
    # that wiring an agent is one reviewed declaration instead of eight rulings asked afterwards
    # — and so the transcription can be checked against the brain before it decides anything.
    #
    # *"Vous ne décidez pas de l'interprétation des lames anatomopathologiques."*
    "radiology": frozenset({"imaging"}),
    # *"Vous ne décidez pas de la stadification clinique (cTNM) à partir de l'imagerie."*
    "pathology": frozenset({"histopathology"}),
    # Evaluates nutritional state as part of its own assessment — MNA, albuminaemia, weight
    # loss — so its remit covers nutrition as well as frailty.
    "geriatric_frailty": frozenset({"frailty", "nutrition"}),
    # *"Vous ne décidez pas … de l'évaluation gériatrique globale (G8, ADL — mais vous en tenez
    # compte)."* Taking something into account is not holding a view on it, so the overlap with
    # oncogeriatrics runs one way only.
    "nutrition": frozenset({"nutrition"}),
    # Pain, symptoms, Pallia-10, social assessment. *"Vous ne décidez pas … de l'évaluation
    # gériatrique standardisée."*
    "palliative_supportive": frozenset({"palliative"}),
}

# What to do with a silence nobody has ruled on.
#
# **Reported, not suppressed.** Hiding a gap because no one has decided it would be a silent
# choice made by an omission, and this project's rule throughout is that absence is visible.
# A wrongly reported gap costs a line on a page; a wrongly hidden one costs the thing the
# category was built to catch.
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
    return explain(point, silent_role)[1]


def explain(point: str, silent_role: str) -> tuple[str, bool, bool]:
    """Subject label, whether it is reported, and whether a remit actually decided it.

    The third value separates *decided* from *defaulted*, so a run can report how much of its
    output rests on judgements nobody has made yet.

    **An unclassified point is never suppressed by a remit.** A remit says which subjects a
    speciality owns, and it cannot speak to a subject the taxonomy has not named — so a point
    landing in `other` is reported whoever stayed silent, and counts as undecided. Suppressing
    it instead would let the vocabulary quietly rule on clinical content by failing to
    recognise it, which is the one way this policy could hide a gap without anyone choosing to.
    """
    subject = subject_of(point)
    if subject is OTHER:
        return subject.label, UNRULED, False

    remit = REMIT.get(silent_role)
    if remit is None:
        return subject.label, UNRULED, False
    return subject.label, subject.key in remit, True
