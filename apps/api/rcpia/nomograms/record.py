"""The typed, normalised patient a nomogram is allowed to see.

**Built before any model is selected, and never from raw text.** A calculator that reaches
into the ledger itself would have to re-parse `"72 ans"`, `"7 (4+3)"` and `"RAS"` in every
model, and each would get it slightly differently. So parsing happens once, here, and every
value arrives typed or absent.

**`None` means unknown, and unknown is never `False`.** MSKCC disqualifies a patient who has
had hormone or radiation therapy, so its own form asks both questions explicitly. A dossier
that records neither cannot be shown to be eligible, and reading that silence as "no prior
treatment" would be the "never use default values for missing clinical data" rule failing
without a sound. `Tristate` exists to make that impossible to do by accident.

**`RAS` is not a value.** It is the clinician writing *rien à signaler* -- for a measurement it
means the test was not done, not that the result was zero.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class Tristate(StrEnum):
    """Yes, no, and the answer nobody wrote down."""

    yes = "yes"
    no = "no"
    unknown = "unknown"

    @property
    def is_unknown(self) -> bool:
        return self is Tristate.unknown


# `RAS`, an empty box, a dash. None of these is a measurement.
NOT_A_VALUE = frozenset(
    {"ras", "r.a.s", "r.a.s.", "néant", "neant", "non précisé", "non precise", "-", "", "?"}
)

# `7 (3+4)`, `7(4+3)`, `9 (5+4)`, `Gleason 4 + 3`. The sum is not enough: 3+4 and 4+3 total
# the same and carry different weights in every model that takes them.
GLEASON_PATTERNS = re.compile(r"(\d)\s*\+\s*(\d)")

# `T2a`, `cT2b`, `T3`. Sub-stages are kept: MSKCC distinguishes 2A from 2B from 2C, and only
# Briganti collapses them into bands, which it does for itself.
CLINICAL_T = re.compile(r"\b(c?T[1-4][a-c]?)\b", re.IGNORECASE)

# `72 ans`, `72`, `72 ans révolus`.
LEADING_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def usable(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower().rstrip(".") not in NOT_A_VALUE


def as_number(value: str | None) -> float | None:
    """The first number in a value, or nothing. Never a default."""
    if not usable(value):
        return None
    match = LEADING_NUMBER.search(str(value).replace(",", "."))
    return float(match.group(0)) if match else None


def as_gleason(value: str | None) -> tuple[int, int] | None:
    if not usable(value):
        return None
    match = GLEASON_PATTERNS.search(str(value))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def as_clinical_t(value: str | None) -> str | None:
    """A stage token in canonical form -- `T2b`, never `t2B` or `cT2b`. Never inferred.

    Canonicalised here rather than by each model, so a comparison against a coefficient table
    cannot fail on casing. The sub-letter is kept: MSKCC distinguishes 2A from 2B from 2C, and
    only Briganti bands them, which it does for itself.
    """
    if not usable(value):
        return None
    match = CLINICAL_T.search(str(value))
    if match is None:
        return None
    token = match.group(1).upper().lstrip("C")
    return token[:2] + token[2:].lower()


def as_tristate(value: str | None, *, positive: tuple[str, ...] = ()) -> Tristate:
    """Yes when the dossier says so, no when it says so, unknown otherwise.

    `RAS` on a treatment field is deliberately **not** read as "no". The clinician may equally
    have meant "nothing to report here because I did not ask", and a disqualifying question
    cannot be answered by an empty box.
    """
    if not usable(value):
        return Tristate.unknown
    text = str(value).strip().lower()
    if any(token in text for token in ("non", "no", "aucun", "absence")):
        return Tristate.no
    if any(token in text for token in ("oui", "yes", *positive)):
        return Tristate.yes
    return Tristate.yes if positive and any(t in text for t in positive) else Tristate.unknown


# Tokens that mean nodal or distant disease is documented, wherever a stage string is written.
NODE_POSITIVE = ("n1", "n2", "n3")
METASTATIC = ("m1",)

# Histologies of the prostate that are not adenocarcinoma. Named rather than matched by
# negation: `as_tristate`'s "non/no/aucun/absence" scan would false-positive on ordinary
# pathology prose ("pas de composante sarcomatoïde") and is not used here for that reason.
# Every published model these coefficients back was validated on adenocarcinoma; a report
# naming one of these instead is a different disease for the model's purposes, not a variant
# of the one it was fitted on.
_NON_ADENOCARCINOMA_HISTOLOGY = (
    "neuroendocrine",
    "petites cellules",
    "small cell",
    "épidermoïde",
    "epidermoide",
    "squamous",
    "sarcome",
    "sarcomatoïde",
    "sarcomatoide",
    "sarcomatous",
    "urothélial",
    "urothelial",
)

_ADENOCARCINOMA_TERMS = ("adénocarcinome", "adenocarcinome", "adenocarcinoma")

# **There is no negation list here, and that is the point.** Four rounds of review found
# four different ways to deny a diagnosis -- "absence de X", "X non retrouvé", "X absent",
# "X non objectivé", "recherche de X : négative", "absence formelle de tout X" -- and each
# fix caught the reported phrasings and missed the next ones. French builds negation from an
# open set of constructions; a blacklist of them cannot be finished, and every gap in it
# failed in the dangerous direction, reading a denial as a confirmation.
#
# So the polarity is inverted. Rather than confirming a diagnosis whenever no *recognised*
# denial is nearby, this confirms one only where the whole field reads as a plain statement
# of that diagnosis and nothing else: every word in it is either the diagnosis itself or
# ordinary descriptive vocabulary. Anything unrecognised -- a negation, a hedge, a search
# result, a construction nobody anticipated -- leaves the field unconfirmed rather than
# confirmed, because an unfamiliar word is evidence that something is being said that this
# code does not understand.
#
# **The failure mode moves to the safe side, deliberately, and it is not free.** A real
# positive report phrased in vocabulary missing from this set now reads as `unknown`, the
# nomograms return `NOT_POSSIBLE` naming `is_prostate_adenocarcinoma`, and a clinician
# confirms it in the ledger -- the same path every other unrecorded field already takes. That
# costs a correction. The alternative cost, paid four times over already, is a calculated
# risk score for a patient whose report denies the disease the model was fitted on.
#
# Grounded in the phrasings this project has actually seen, not invented: "Adénocarcinome
# acinaire", "Adénocarcinome acinaire, Gleason 3+4", "Carcinome urothélial de haut grade",
# "Carcinome neuroendocrine à petites cellules". Extending it is a deliberate act with a test,
# not something to do reflexively when a dossier reads `unknown`.
_PLAIN_DESCRIPTION = frozenset(
    {
        # Subtype and site.
        "acinaire", "acinaires", "ductal", "ductale", "intracanalaire",
        "prostatique", "prostatiques", "prostate", "vessie", "vesicale", "vésicale",
        # The genus term the specific histologies hang off.
        "carcinome", "carcinomes", "tumeur",
        # Grading vocabulary.
        "gleason", "isup", "score", "grade", "groupe", "haut", "bas",
        "intermediaire", "intermédiaire",
        "differencie", "différencié", "differenciee", "différenciée",
        "moderement", "modérément", "bien", "peu",
        # Parts of the multi-word histology names themselves.
        "petites", "cellules", "composante", "contingent",
        # Ordinary connective tissue of a French noun phrase.
        "a", "à", "au", "aux", "de", "du", "d", "des", "la", "le", "les",
        "et", "avec", "type", "sur", "en",
        # An explicit affirmation is evidence *for*, never against.
        "confirme", "confirmé", "confirmee", "confirmée",
    }
)

_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)

# **The quantitative forms a diagnosis may legitimately carry, and nothing else.** Numbers
# and symbols were once simply not looked at -- the word scan skipped them -- so
# "Adénocarcinome : 0 %", "Adénocarcinome : -", "Adénocarcinome ?" and "Adénocarcinome 0/10"
# all read as plain statements of adenocarcinoma. Each is a denial or a query written in
# figures rather than words, and each produced a calculated risk score.
#
# So grading is parsed explicitly and consumed; whatever numeral or symbol is left after that
# has no recognised meaning here, and an unrecognised one is refused for the same reason an
# unrecognised word is. The keyword form declines to match a number that opens a Gleason pair,
# so "Gleason 3+4" is read as the pair it is rather than leaving a stray "+4" behind.
# **Recognising the shape of a grade is not the same as it being a grade.** Consuming any
# digits that looked like grading meant "ISUP 0", "Gleason 0", "Gleason 99", "0+0" and
# "Gleason 8 (3+4)" -- a sum that contradicts its own pair -- were all read as ordinary
# grading and vanished, leaving a field that looked like a plain diagnosis. None of them is a
# grade a pathologist can write. A number outside its scale is either a different measurement
# or an error, and neither should quietly establish that a patient is eligible for a model.
#
# Invalid grading is left in the text rather than consumed, so the token check that follows
# refuses it exactly as it refuses any other stray numeral.
_GRADING_FORMS = re.compile(
    r"\b(?P<sum>\d{1,2})\s*\(\s*(?P<sa>\d)\s*\+\s*(?P<sb>\d)\s*\)"
    r"|\(\s*(?P<pa>\d)\s*\+\s*(?P<pb>\d)\s*\)"
    r"|\b(?P<ba>\d)\s*\+\s*(?P<bb>\d)\b"
    # Declines a number that opens a pair ("Gleason 3+4") or is followed by one
    # ("Gleason 8 (3+4)"), so the fuller form matches instead and its sum is checked against
    # its own pair. Matching "gleason 8" here first would consume the sum and the pair as two
    # separately valid grades and never notice they contradict each other.
    r"|\b(?P<keyword>gleason|isup|score|grade|groupe)\b\s*:?\s*(?P<value>\d{1,2})(?!\s*[+(])",
    re.IGNORECASE,
)

# Gleason grades each pattern 1 to 5 and sums them, so a score runs 2 to 10. ISUP and the
# grade group it names run 1 to 5.
_PATTERN_SCALE = range(1, 6)
_SCORE_SCALE = range(2, 11)
_GROUP_SCALE = range(1, 6)
_KEYWORD_SCALE = {
    "gleason": _SCORE_SCALE,
    "score": _SCORE_SCALE,
    "isup": _GROUP_SCALE,
    "grade": _GROUP_SCALE,
    "groupe": _GROUP_SCALE,
}


def _consume_valid_grading(match: "re.Match[str]") -> str:
    """Erase a grade that a pathologist could have written; leave anything else in place."""
    parts = match.groupdict()
    if parts["sum"] is not None:
        first, second = int(parts["sa"]), int(parts["sb"])
        total = int(parts["sum"])
        # The sum has to be the sum. "Gleason 8 (3+4)" contradicts itself, and a field that
        # disagrees with itself about the grade is not a plain statement of anything.
        valid = (
            first in _PATTERN_SCALE
            and second in _PATTERN_SCALE
            and total == first + second
        )
    elif parts["pa"] is not None:
        valid = int(parts["pa"]) in _PATTERN_SCALE and int(parts["pb"]) in _PATTERN_SCALE
    elif parts["ba"] is not None:
        valid = int(parts["ba"]) in _PATTERN_SCALE and int(parts["bb"]) in _PATTERN_SCALE
    else:
        valid = int(parts["value"]) in _KEYWORD_SCALE[parts["keyword"].lower()]
    return " " if valid else match.group(0)

# Punctuation that may sit against a word without changing what it says. A token made only of
# these is refused rather than skipped: ":" and "-" standing alone are how a form records that
# a question was asked and not answered.
_STRIPPABLE = ",.:;()'’\"«»"


def _states_plainly(text: str, terms: tuple[str, ...]) -> bool:
    """Whether `text` reads as a plain statement of one of `terms` and nothing else.

    **Every token is accounted for -- words, numerals and symbols alike.** The whole field is
    read, not a window around the match: histology arrives as one short field, so there is no
    long report to scope a window within, and window sizing was itself one of the things that
    kept going wrong. A token survives only by being recognised, never by being skipped.
    """
    if not any(term in text for term in terms):
        return False
    named = {word for term in terms for word in _WORDS.findall(term)}
    for token in _GRADING_FORMS.sub(_consume_valid_grading, text).split():
        word = token.strip(_STRIPPABLE)
        # Punctuation alone, or a token carrying a digit or symbol that grading did not claim.
        if not word or not word.isalpha():
            return False
        if word not in named and word not in _PLAIN_DESCRIPTION:
            return False
    return True


def classify_prostate_histology(value: str | None) -> Tristate:
    """Adenocarcinoma, a named other histology, or genuinely unrecorded.

    **Unmatched text stays `unknown`, never `no`.** A pathology report using vocabulary this
    list does not recognise is a gap in the classifier, not evidence the tumour is something
    other than adenocarcinoma -- and the cost of guessing wrong here is a nomogram computing a
    number for a cancer it was never validated on, which is the one failure this whole gate
    exists to prevent.

    **A diagnosis is confirmed by positive evidence, never by the absence of a recognised
    denial.** Both directions go through the same check, and both require the field to read
    as a plain statement of the diagnosis. A denial does not need to be recognised as a
    denial to be refused -- it only needs to contain a word that is not part of plainly
    stating the finding, which every denial does.
    """
    if not usable(value):
        return Tristate.unknown
    text = str(value).strip().lower().replace("e'", "é")
    if _states_plainly(text, _ADENOCARCINOMA_TERMS):
        return Tristate.yes
    if _states_plainly(text, _NON_ADENOCARCINOMA_HISTOLOGY):
        return Tristate.no
    return Tristate.unknown


@dataclass(frozen=True)
class ClinicalRecord:
    """Everything the implemented models may read, typed and explicit.

    Fields the ledger cannot yet supply are present and `None` rather than absent: a model
    asking for the mpMRI index-lesion diameter should refuse naming that field, not raise a
    `KeyError` that a caller has to interpret.
    """

    # -- identity and general ------------------------------------------------
    age_years: float | None = None
    psa_ng_ml: float | None = None

    # -- biopsy --------------------------------------------------------------
    gleason_primary: int | None = None
    gleason_secondary: int | None = None
    systematic_positive_cores: int | None = None
    systematic_total_cores: int | None = None

    # -- staging -------------------------------------------------------------
    # From the digital rectal examination only. EAU §4.5: clinical stage is the DRE; imaging
    # and pathology staging are reported separately and must not stand in for it.
    clinical_t_stage_dre: str | None = None
    known_nodal_metastasis: Tristate = Tristate.unknown
    known_distant_metastasis: Tristate = Tristate.unknown

    # -- eligibility ---------------------------------------------------------
    hormone_therapy: Tristate = Tristate.unknown
    radiation_therapy: Tristate = Tristate.unknown
    radical_prostatectomy_performed: Tristate = Tristate.unknown

    # -- pathway inputs no dossier yet records -------------------------------
    mri_index_lesion_max_diameter_mm: float | None = None
    targeted_biopsy_grade_group: int | None = None
    mri_targeted_biopsy_performed: Tristate = Tristate.unknown
    psma_pet_performed: Tristate = Tristate.unknown
    psma_pet_min0m0: Tristate = Tristate.unknown

    is_prostate_adenocarcinoma: Tristate = Tristate.unknown

    @property
    def systematic_negative_cores(self) -> int | None:
        if self.systematic_positive_cores is None or self.systematic_total_cores is None:
            return None
        return self.systematic_total_cores - self.systematic_positive_cores

    @property
    def percent_positive_cores(self) -> float | None:
        """Only where numerator and denominator describe the same biopsy."""
        if self.systematic_positive_cores is None or not self.systematic_total_cores:
            return None
        return 100.0 * self.systematic_positive_cores / self.systematic_total_cores

    @property
    def gleason_grade_group(self) -> int | None:
        """ISUP grade group, derived from the patterns rather than read separately.

        Deriving it here keeps one source of truth: a dossier recording both a Gleason and an
        ISUP that disagree is a conflict for the ledger to surface, not something a calculator
        should silently pick a side in.
        """
        pair = (self.gleason_primary, self.gleason_secondary)
        if None in pair:
            return None
        primary, secondary = pair
        total = primary + secondary  # type: ignore[operator]
        if total <= 6:
            return 1
        if (primary, secondary) == (3, 4):
            return 2
        if (primary, secondary) == (4, 3):
            return 3
        if total == 8:
            return 4
        return 5

    def invalid_ranges(self) -> tuple[str, ...]:
        """Values that are present and cannot be right. Rejected, never clamped."""
        problems: list[str] = []
        if self.age_years is not None and not (20 <= self.age_years <= 99):
            problems.append(f"age {self.age_years:g} outside 20-99")
        if self.psa_ng_ml is not None and self.psa_ng_ml < 0:
            problems.append(f"PSA {self.psa_ng_ml:g} is negative")
        for name, pattern in (
            ("primary", self.gleason_primary), ("secondary", self.gleason_secondary)
        ):
            if pattern is not None and pattern not in (1, 2, 3, 4, 5):
                problems.append(f"{name} Gleason pattern {pattern} outside 1-5")
        positive, total = self.systematic_positive_cores, self.systematic_total_cores
        if positive is not None and positive < 0:
            problems.append("negative positive-core count")
        if total is not None and total <= 0:
            problems.append("total cores is zero or negative")
        if positive is not None and total is not None and positive > total:
            problems.append(f"positive cores {positive} exceeds total {total}")
        return tuple(problems)


def _stage_flag(*values: str | None, tokens: tuple[str, ...]) -> Tristate:
    """Yes where a stage string records the finding; unknown where nothing is recorded."""
    seen = False
    for value in values:
        if not usable(value):
            continue
        seen = True
        if any(token in str(value).lower() for token in tokens):
            return Tristate.yes
    # A stage string that exists and does not mention N1/M1 is evidence of absence; no stage
    # string at all is no evidence either way.
    return Tristate.no if seen else Tristate.unknown


def record_from_facts(facts: dict[str, str]) -> ClinicalRecord:
    """Normalise a ledger reading. Keys are `fact_schema` field names."""
    gleason = as_gleason(facts.get("gleason"))
    positive = as_number(facts.get("cores_positive"))
    total = as_number(facts.get("cores_total"))
    prostate = classify_prostate_histology(facts.get("histology"))
    return ClinicalRecord(
        age_years=as_number(facts.get("age")),
        psa_ng_ml=as_number(facts.get("psa_total")),
        gleason_primary=gleason[0] if gleason else None,
        gleason_secondary=gleason[1] if gleason else None,
        systematic_positive_cores=int(positive) if positive is not None else None,
        systematic_total_cores=int(total) if total is not None else None,
        clinical_t_stage_dre=as_clinical_t(facts.get("ct_dre")),
        known_nodal_metastasis=_stage_flag(
            facts.get("ctnm"), facts.get("ptnm"), tokens=NODE_POSITIVE
        ),
        known_distant_metastasis=_stage_flag(
            facts.get("ctnm"), facts.get("ptnm"), tokens=METASTATIC
        ),
        hormone_therapy=as_tristate(facts.get("hormone_therapy")),
        radiation_therapy=as_tristate(facts.get("radiation_therapy")),
        is_prostate_adenocarcinoma=prostate,
    )
