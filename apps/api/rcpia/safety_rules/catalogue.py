"""The safety rules, as data a person can audit rather than logic buried in a function.

**Every rule is `poc_unreviewed`.** No clinician has approved any of them. The engine's output
carries that status so a reader is never shown a safety annotation that looks validated.

**Every action is `flag` in v1, and that is a deliberate posture rather than a placeholder.**
The corpus already argues the case for the passages themselves: *"Excluding is not deleting… a
passage removed silently and a passage removed for a stated reason look identical from the
outside otherwise."* A runtime `exclude` is that silent removal — the agent never sees the
passage, cannot cite it, and no reviewer downstream can notice the absence. It is also worse
reasoning: the board wants *"cisplatin is standard for MIBC; this patient's clearance of 28
contraindicates it; here is what to do instead"*, and excluding the standard-of-care passage
makes the first clause unsayable. `exclude` exists in the vocabulary, is reserved for passages
that are affirmatively dangerous rather than merely inapplicable, and is used by nothing.

**Absent measurements are not zero, and this is the rule most likely to matter.** A condition
that needs an eGFR the ledger does not have does not fire and does not pass — it is
*unevaluable*, and the engine says so by name. The dossier annotation found three of seven
bladder patients with no eGFR recorded at all, including a pT2 high-grade candidate for
cystectomy and neoadjuvant chemotherapy. Silently treating those as "rule did not fire" would
report safety where none was checked.

**Two kinds of passage match, and the difference is honest rather than incidental.** A keyword
pattern is used only where hand-checking showed it works — the platinum rules, where the
guidelines' own phrasing is stable. Rules whose match is a clinical judgement spread across
sentences (*"recommends radical cystectomy without addressing nutritional optimisation"*)
declare `PRECOMPUTED` and match nothing until a reviewed table exists. They are wired and inert,
and the engine reports them as awaiting precompute rather than as silent non-matches.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

CLINICAL_STATUS = "poc_unreviewed"


class SafetyAction(Enum):
    """What the engine does with a passage a rule fired on."""

    FLAG = "flag"
    # Reserved. Nothing uses it in v1 -- see the module docstring for why.
    EXCLUDE = "exclude"


class Severity(Enum):
    CONTRAINDICATION = "contraindication"
    WARNING = "warning"
    ANNOTATION = "annotation"


@dataclass(frozen=True)
class Ledger:
    """The patient facts the rules read.

    Every field is optional, and `None` means *not recorded* rather than *absent or normal*.
    That distinction is the whole reason this is a dataclass of optionals rather than a dict
    with defaults: a missing G8 must never read as a G8 of zero.
    """

    cancer_type: str | None = None
    disease_setting: str | None = None
    age: int | None = None
    egfr: float | None = None
    performance_status: int | None = None
    significant_comorbidity: bool | None = None
    mna: float | None = None
    g8: int | None = None
    adl: int | None = None
    haemoglobin: float | None = None
    gleason_sum: int | None = None
    isup: int | None = None
    psa: float | None = None
    clinical_t_stage: str | None = None
    prior_resections: int | None = None
    on_adt: bool | None = None
    psa_rising: bool | None = None
    dossier_id: str = ""

    def missing(self, fields: Sequence[str]) -> tuple[str, ...]:
        """Which of the named fields this ledger does not record."""
        return tuple(name for name in fields if getattr(self, name, None) is None)


@dataclass(frozen=True)
class Condition:
    """When a rule applies to a patient.

    `requires` is separate from `test` on purpose: it is the auditable half. It drives the
    unevaluable path, and it lets a reader see which measurements a rule depends on without
    reading its logic.
    """

    requires: tuple[str, ...]
    test: Callable[[Ledger], bool]
    description: str


class MatchKind(Enum):
    KEYWORD = "keyword"
    # A clinical judgement spread across sentences. Matches nothing until a reviewed
    # (rule_id, chunk_id) table exists -- see the module docstring.
    PRECOMPUTED = "precomputed"
    # The rule is about the patient, not about any passage.
    PATIENT_ONLY = "patient_only"


@dataclass(frozen=True)
class PassagePattern:
    """What in a passage makes it the thing the rule is about."""

    kind: MatchKind
    # KEYWORD: the passage must match one of these...
    requires: tuple[re.Pattern[str], ...] = ()
    # ...and none of these, which are the qualifiers that make the passage safe to show.
    excused_by: tuple[re.Pattern[str], ...] = ()
    description: str = ""

    def matches(self, text: str) -> bool:
        if self.kind is not MatchKind.KEYWORD:
            return False
        if not any(pattern.search(text) for pattern in self.requires):
            return False
        return not any(pattern.search(text) for pattern in self.excused_by)


@dataclass(frozen=True)
class SafetyRule:
    rule_id: str
    title: str
    condition: Condition
    passage_pattern: PassagePattern
    reason_template: str
    source_chunk_ids: tuple[str, ...]
    action: SafetyAction = SafetyAction.FLAG
    severity: Severity = Severity.WARNING
    dossier_coverage: tuple[str, ...] = ()
    # Recorded where two guidelines say different things, so the annotation can carry both.
    divergence: str = ""
    clinical_status: str = CLINICAL_STATUS
    notes: str = ""


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# The qualifiers that make a platinum recommendation safe to put in front of a clinician: the
# guideline has already said who may receive it. Measured rather than guessed -- of 99 passages
# mentioning cisplatin in the admitted bladder set, most carry one of these, which is why the
# forbidden set the gold standard was labelled from is small.
_RENAL_QUALIFIER_ONLY = (
    _p(r"renal function|fonction r[ée]nale|kidney function"),
    _p(r"\bGFR\b|\bDFG\b|creatinine cl|clairance|glomerular"),
    _p(r"eligib|[ée]ligib|unfit|fit enough|not fit"),
    _p(r"nephrotox|n[ée]phrotox"),
    _p(r"contraindicat|contre-?indic"),
)
# A passage that recommends *against* the drug is the correct answer for this patient, not a
# hazard. Flagging EAU's "Do not offer chemotherapy alone as primary therapy" would teach a
# reader that the flags are noise -- which is how a safety layer stops being read. Found by a
# test rather than by reasoning, which is why the test is there.
_RECOMMENDS_AGAINST = (
    _p(r"do not (offer|give|use|administer|prescribe|recommend)"),
    _p(r"should not (be )?(offer|give|receive|used|administered|prescribed)"),
    _p(r"is not recommended|are not recommended|no proven role|insufficient (data|evidence)"),
    _p(r"ne pas (offrir|administrer|proposer)|n'est pas recommand|non recommand"),
)
_CISPLATIN = (_p(r"cisplatin|cisplatine"),)
_ANY_PLATINUM = (_p(r"cisplatin|cisplatine|carboplatin|carboplatine|platinum|platine"),)
_RENAL_QUALIFIER = _RENAL_QUALIFIER_ONLY + _RECOMMENDS_AGAINST
_ACTIVE_SURVEILLANCE = (_p(r"active surveillance|surveillance active"),)
_AS_QUALIFIER = (
    _p(r"high[- ]risk|haut risque|very high|tr[èe]s haut"),
    _p(r"contraindicat|contre-?indic|not suitable"),
    _p(r"ISUP (grade )?(group )?[45]|Gleason [89]|grade group [45]"),
) + _RECOMMENDS_AGAINST
_HORMONE_NAIVE = (
    _p(r"initiat\w* (of )?ADT|start\w* ADT|androgen deprivation therapy.{0,40}(initiat|start)"),
    _p(r"hormone[- ]na[ïi]ve|hormone[- ]sensitive|castration[- ]sensitive|mHSPC|mCSPC"),
)
_CRPC_QUALIFIER = (
    _p(r"castration[- ]resistant|CRPC|castrate[- ]resistant"),
) + _RECOMMENDS_AGAINST


def _band(low: float | None, value: float | None, high: float | None) -> bool:
    if value is None:
        return False
    if low is not None and value < low:
        return False
    return not (high is not None and value > high)


def _rule_1a(ledger: Ledger) -> bool:
    """Cisplatin contraindicated: the band where all four guidelines agree."""
    if ledger.egfr is None:
        return False
    if _band(30, ledger.egfr, 49.999):
        return True
    # 50-60 is contested unless performance status or comorbidity settles it.
    if _band(50, ledger.egfr, 60):
        return bool(
            (ledger.performance_status is not None and ledger.performance_status >= 2)
            or ledger.significant_comorbidity
        )
    return False


def _rule_1a_contested(ledger: Ledger) -> bool:
    return (
        _band(50, ledger.egfr, 60)
        and ledger.performance_status is not None
        and ledger.performance_status <= 1
        and ledger.significant_comorbidity is False
    )


def _rule_1b(ledger: Ledger) -> bool:
    if ledger.egfr is not None and ledger.egfr < 30:
        return True
    return ledger.performance_status is not None and ledger.performance_status > 2


RULES: tuple[SafetyRule, ...] = (
    SafetyRule(
        rule_id="1a-cisplatin-contraindicated",
        title="Cisplatin contraindicated by renal function",
        condition=Condition(
            requires=("egfr",),
            test=_rule_1a,
            description="30 <= eGFR < 50, or 50-60 with PS >= 2 or significant comorbidity",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.KEYWORD,
            requires=_CISPLATIN,
            excused_by=_RENAL_QUALIFIER,
            description="recommends cisplatin without naming a renal or eligibility qualifier",
        ),
        reason_template=(
            "Cisplatin-based chemotherapy is contraindicated at this renal function "
            "(eGFR {egfr}). In metastatic disease, carboplatin-based regimens are an "
            "alternative. In non-metastatic MIBC, there is insufficient evidence for "
            "carboplatin as neoadjuvant therapy — consider trimodality therapy or cystectomy "
            "without neoadjuvant chemotherapy."
        ),
        source_chunk_ids=(
            "eau.bladder-mibc.2026.9-3-first-line-systemic-therapy-for-metastatic-disease-patie.01.1229",
            "aua.bladder-mibc.2024.neoadjuvant-adjuvant-chemotherapy.03.0434",
            "aua.bladder-mibc.2024.neoadjuvant-adjuvant-chemotherapy.04.0435",
        ),
        severity=Severity.CONTRAINDICATION,
        dossier_coverage=("dossier-2",),
        notes=(
            "Carboplatin advice is conditional on setting -- AUA finds no evidence "
            "for it as neoadjuvant therapy in MIBC."
        ),
    ),
    SafetyRule(
        rule_id="1a-contested-cisplatin-50-60",
        title="Cisplatin at eGFR 50-60: the guidelines disagree",
        condition=Condition(
            requires=("egfr", "performance_status", "significant_comorbidity"),
            test=_rule_1a_contested,
            description="50 <= eGFR <= 60 with PS 0-1 and no significant comorbidity",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.KEYWORD,
            requires=_CISPLATIN,
            description="any passage recommending cisplatin -- both positions are surfaced",
        ),
        reason_template=(
            "Patient eGFR is {egfr}, in the band where the guidelines disagree. EAU classifies "
            "eGFR at or below 60 as unfit for cisplatin. ESMO states gemcitabine-cisplatin may "
            "be considered at a clearance of 50-60 in an otherwise fit patient with PS 0-1 and "
            "no comorbidity, and AFU sets its threshold at DFG of 50-60 or above. This is a "
            "genuine divergence between guidelines, not an error."
        ),
        source_chunk_ids=(
            "eau.bladder-mibc.2026.9-3-first-line-systemic-therapy-for-metastatic-disease-patie.01.1229",
            "afu.bladder-tvim.2024-2026.chimiotherapie-neoadjuvante-a-base-de-cisplatine.01.0108",
        ),
        severity=Severity.WARNING,
        divergence="EAU says unfit; ESMO and AFU say cisplatin may be considered.",
        notes="Fires on none of the 15 dossiers. Kept because it fires on patient 16.",
    ),
    SafetyRule(
        rule_id="1b-unfit-any-platinum",
        title="Unfit for any platinum-based chemotherapy",
        condition=Condition(
            requires=("egfr",),
            test=_rule_1b,
            description="eGFR < 30, or performance status > 2",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.KEYWORD,
            requires=_ANY_PLATINUM,
            excused_by=_RENAL_QUALIFIER,
            description="recommends any platinum agent without a renal or eligibility qualifier",
        ),
        reason_template=(
            "Patient is unfit for any platinum-based chemotherapy (eGFR {egfr}). Consider "
            "non-platinum alternatives — pembrolizumab, enfortumab vedotin, radiotherapy alone, "
            "or best supportive care per geriatric assessment."
        ),
        source_chunk_ids=(
            "eau.bladder-mibc.2026.9-5-first-line-therapy-in-patients-not-eligible-for-combinat.01.1235",
        ),
        severity=Severity.CONTRAINDICATION,
        dossier_coverage=("dossier-1",),
    ),
    SafetyRule(
        rule_id="1c-confirm-borderline-gfr",
        title="Confirm a borderline GFR before deciding platinum eligibility",
        condition=Condition(
            requires=("egfr",),
            test=lambda ledger: _band(30, ledger.egfr, 60),
            description="30 <= eGFR <= 60",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.PATIENT_ONLY,
            description="about the patient, not any passage",
        ),
        reason_template=(
            "Patient eGFR is {egfr}, close to the platinum eligibility boundary. NCCN "
            "recommends timed urine collection to confirm GFR before deciding cisplatin "
            "eligibility, as an estimate may be inaccurate at this range."
        ),
        source_chunk_ids=(
            "nccn.bladder.3.2026.2026.adjuvant-treatment.01.2468",
            "nccn.bladder.3.2026.2026.muscle-invasive-bladder-cancer.01.2467",
        ),
        severity=Severity.ANNOTATION,
        dossier_coverage=("dossier-2",),
    ),
    SafetyRule(
        rule_id="2-nutrition-before-major-surgery",
        title="Nutritional status gates major surgery",
        condition=Condition(
            requires=("mna",),
            test=lambda ledger: ledger.mna is not None and ledger.mna < 17,
            description="MNA < 17 (severe malnutrition)",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.PRECOMPUTED,
            description="recommends radical cystectomy without addressing preoperative "
            "nutritional optimisation -- a judgement spread across sentences",
        ),
        reason_template=(
            "Patient MNA is {mna} (severe malnutrition below 17). Preoperative nutritional "
            "optimisation is indicated per ESPEN/ASPEN guidance before radical cystectomy."
        ),
        source_chunk_ids=(),
        dossier_coverage=("dossier-1", "dossier-2"),
    ),
    SafetyRule(
        rule_id="3-geriatric-assessment-before-radical-treatment",
        title="Geriatric assessment gates major surgery",
        condition=Condition(
            requires=("age",),
            test=lambda ledger: (
                ledger.age is not None
                and ledger.age >= 75
                and ((ledger.g8 is not None and ledger.g8 <= 14)
                     or (ledger.adl is not None and ledger.adl < 6))
            ),
            description="age >= 75 and (G8 <= 14 or ADL < 6)",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.PRECOMPUTED,
            description="recommends radical treatment without addressing geriatric assessment",
        ),
        reason_template=(
            "Patient is {age} with G8 {g8} and ADL {adl}. Comprehensive geriatric assessment is "
            "indicated before radical treatment per SIOG and EAU."
        ),
        source_chunk_ids=(
            "eau.bladder-mibc.2026.6-4-1-summary-of-evidence-and-recommendations-for-comorbidit.01.1148",
        ),
        dossier_coverage=("dossier-2",),
    ),
    SafetyRule(
        rule_id="4-active-surveillance-in-high-risk-prostate",
        title="Active surveillance contraindicated in high-risk prostate cancer",
        condition=Condition(
            requires=(),
            test=lambda ledger: ledger.cancer_type == "prostate" and bool(
                (ledger.gleason_sum is not None and ledger.gleason_sum >= 8)
                or (ledger.isup is not None and ledger.isup >= 4)
                or (ledger.psa is not None and ledger.psa > 20)
                or (ledger.clinical_t_stage or "").upper().startswith(("T3", "T4"))
            ),
            description="Gleason >= 8, or ISUP >= 4, or PSA > 20, or stage T3/T4",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.KEYWORD,
            requires=_ACTIVE_SURVEILLANCE,
            excused_by=_AS_QUALIFIER,
            description="offers active surveillance without stating the high-risk exclusion",
        ),
        reason_template=(
            "Active surveillance is contraindicated in high-risk prostate cancer. Patient: "
            "Gleason {gleason_sum}, ISUP {isup}, PSA {psa}, stage {clinical_t_stage}."
        ),
        source_chunk_ids=(),
        severity=Severity.CONTRAINDICATION,
        notes=(
            "Interacts with SETTING_PARENTS: AUA's principles-of-active-surveillance is routed "
            "prostate_nonmetastatic_unspecified_risk and is reachable for a high-risk question "
            "through parent admission. Not labelled on pr-001 -- the gold set lists those "
            "passages as relevant, and a passage cannot be both."
        ),
    ),
    SafetyRule(
        rule_id="5-hormone-naive-passage-for-crpc-patient",
        title="Hormone-naive guidance for a castration-resistant patient",
        condition=Condition(
            requires=("on_adt", "psa_rising"),
            test=lambda ledger: bool(
                ledger.cancer_type == "prostate" and ledger.on_adt and ledger.psa_rising
            ),
            description="prostate, on ADT, with rising PSA",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.KEYWORD,
            requires=_HORMONE_NAIVE,
            excused_by=_CRPC_QUALIFIER,
            description="addresses the hormone-naive pathway without naming castration resistance",
        ),
        reason_template=(
            "Patient is on ADT with rising PSA — castration-resistant. This passage addresses "
            "hormone-naive disease. EAU requires testosterone below 50 ng/dL to be confirmed "
            "before the castration-resistant diagnosis is made."
        ),
        source_chunk_ids=(
            "eau.prostate.2026.6-7-13-summary-of-evidence-and-recommendations-for.01.1725",
        ),
        dossier_coverage=("dossier-8",),
    ),
    SafetyRule(
        rule_id="6-recurrent-nmibc-prior-therapy",
        title="Recurrent NMIBC after multiple resections",
        condition=Condition(
            requires=("prior_resections",),
            test=lambda ledger: (
                ledger.prior_resections is not None and ledger.prior_resections > 2
            ),
            description="more than two prior resections",
        ),
        passage_pattern=PassagePattern(
            kind=MatchKind.PRECOMPUTED,
            description="first-line NMIBC management or BCG-naive pathways",
        ),
        reason_template=(
            "Patient has had {prior_resections} prior resections. Recurrent NMIBC after "
            "multiple treatments may require the BCG-unresponsive pathway — radical "
            "cystectomy, clinical trial, or alternative intravesical agents."
        ),
        source_chunk_ids=(),
        dossier_coverage=("dossier-15",),
    ),
)

RULES_BY_ID: dict[str, SafetyRule] = {rule.rule_id: rule for rule in RULES}

# The (rule_id, chunk_id) pairs a reviewer has confirmed for PRECOMPUTED rules. Empty until
# that pass is done, which is why those rules are wired and inert rather than silently absent.
PRECOMPUTED_MATCHES: dict[str, frozenset[str]] = {}
