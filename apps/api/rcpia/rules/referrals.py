"""Which referrals to raise, and on what recorded basis.

Section 2 of a real PPS records what the RCP decided about oncogeriatric assessment,
genetic referral, and fertility preservation. **RCPia never ticks those boxes.** No
threshold here has a clinician's name against it, so every checkbox stays `à déterminer`
and RCPia's own reading is carried separately as a labelled suggestion — see
`approval.py` for why that separation is in the type system rather than in a caption.

Three asymmetries shape the rules, and each comes from what happens when they are wrong.

- **Absence never closes a question.** A missing G8 is not evidence that assessment is
  unnecessary; a family history nobody recorded is not evidence there is none. Where the
  input is missing, the rule declines to suggest rather than suggesting *non*.
- **Fertility preservation is never closed.** Wrongly declining to raise it is permanent;
  wrongly raising it costs a conversation. It suggests *oui* or nothing.
- **A contested value is no basis for anything.** Where the dossier contradicts itself, the
  rule says so and suggests nothing.

The genetics rule has **no authority at all**. Deciding whether a family history warrants a
referral needs the relationship, the tumour type, the age at diagnosis and the family
pattern — none of which the ledger holds as structure. Keyword matching on free text
produced referrals for *colonne vertébrale*, *dessein*, *frein lingual*, a benign breast
tumour, and an isolated prostate cancer at 88. Word boundaries fix the first class only, so
the rule reports what the dossier says and suggests nothing.
"""

import re
from dataclasses import dataclass

from rcpia.cases.fact_schema import REPORTED_ABSENT
from rcpia.pps.schemas import Answer, Referral
from rcpia.rules.approval import NO_RULE, PROJECT_SUGGESTION, Approval, Authority

# G8 ≤ 14 is the published screening cutoff for oncogeriatric assessment; the age is the
# usual convention for offering the screen at all. Both may suggest, never decide.
GERIATRIC_AGE = 75
G8_THRESHOLD = 14.0

# Age references for raising fertility preservation. Weakly grounded, and used only to
# decide when to suggest *oui* — never to suggest *non*.
FERTILITY_AGE_MALE = 60
FERTILITY_AGE_FEMALE = 45

GERIATRIC_APPROVAL: Approval = PROJECT_SUGGESTION
FERTILITY_APPROVAL: Approval = PROJECT_SUGGESTION
# No usable rule exists — see the module docstring.
GENETIC_APPROVAL: Approval = NO_RULE

# A number in its own right, not a digit inside an identifier. Without the lookbehind,
# `G8 : 5/17` reads as 8 and `p T2` as 2 — a stage silently becoming a score.
NUMBER = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Observation:
    """What a rule saw, and what it would suggest from it.

    `suggestion` is deliberately `None` by default. A rule says nothing unless it has
    something to say.
    """

    text: str
    suggestion: Answer | None = None
    basis: str | None = None


def number_in(value: str | None) -> float | None:
    """First standalone number in a free-text value.

    `85 ans` -> 85.0, `G8 : 5/17` -> 5.0, `17,33` -> 17.33, `p T2` -> None.
    """
    if not value:
        return None
    found = NUMBER.search(value)
    if found is None:
        return None
    try:
        return float(found.group().replace(",", "."))
    except ValueError:  # pragma: no cover - the pattern only matches parseable numbers
        return None


def is_female(sex: str | None) -> bool | None:
    if not sex:
        return None
    lowered = sex.strip().lower()
    if lowered.startswith(("f", "femme")):
        return True
    if lowered.startswith(("h", "m")):  # homme / masculin / male
        return False
    return None


def _reported_absent(value: str | None) -> bool:
    return (value or "").strip().upper() == REPORTED_ABSENT


def _referral(label: str, approval: Approval, observation: Observation) -> Referral:
    """Build the row the document shows.

    The checkbox stays `a_determiner` unless a clinician approved the rule. Everything else
    travels as a suggestion, which the renderer prints apart from the box and labels.
    """
    answer: Answer = (
        observation.suggestion
        if approval.may_answer and observation.suggestion is not None
        else "a_determiner"
    )
    suggestion = (
        observation.suggestion
        if approval.may_suggest and not approval.may_answer
        else None
    )
    return Referral(
        label=label,
        answer=answer,
        justification=observation.text,
        basis=observation.basis,
        suggestion=suggestion,
        authority=approval.authority,
    )


def geriatric_referral(
    *, age: str | None, g8: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Raise an oncogeriatric assessment on a low G8, or on age when G8 is absent.

    **Never suggests *non*.** A missing G8 says nothing about whether an assessment is
    needed, and a G8 above the cutoff is a screening result rather than a decision — the
    screen exists to raise the question, not to settle it.
    """
    years = number_in(age)
    score = number_in(g8)

    if "g8_score" in contested or "age" in contested:
        observation = Observation(
            "L'âge ou le score G8 est rapporté de façon contradictoire dans le dossier ; "
            "aucune conclusion fiable ne peut en être tirée."
        )
    elif score is not None and score <= G8_THRESHOLD:
        observation = Observation(
            f"Score G8 rapporté à {g8} (≤ {G8_THRESHOLD:g}) : une évaluation "
            "oncogériatrique est habituellement indiquée.",
            suggestion="oui",
            basis=f"G8 : {g8}",
        )
    elif score is not None:
        # Above the cutoff. A screening result does not close the question.
        observation = Observation(
            f"Score G8 rapporté à {g8} (> {G8_THRESHOLD:g}). Le dépistage ne conclut pas "
            "à lui seul à l'inutilité d'une évaluation.",
            basis=f"G8 : {g8}",
        )
    elif years is not None and years >= GERIATRIC_AGE:
        observation = Observation(
            f"Patient·e de {years:g} ans, score G8 non renseigné (dépistage habituellement "
            f"proposé à partir de {GERIATRIC_AGE} ans).",
            suggestion="oui",
            basis=f"Âge : {age}",
        )
    elif years is not None:
        # Below the age reference and no G8. Absence of screening is not a negative result.
        observation = Observation(
            f"Patient·e de {years:g} ans, score G8 non renseigné. L'absence de dépistage "
            "ne permet pas de conclure.",
            basis=f"Âge : {age}",
        )
    else:
        observation = Observation("Ni l'âge ni le score G8 ne sont renseignés.")

    return _referral("Avis oncogériatrique", GERIATRIC_APPROVAL, observation)


def genetic_referral(
    *, family_history: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Report the recorded family history. Suggest nothing.

    See the module docstring: a correct rule needs the relationship, the tumour type, the
    age at diagnosis and the family pattern, and the ledger holds a free-text line. The
    text is shown so the RCP can judge it.
    """
    if "family_history" in contested:
        text = (
            "Les antécédents familiaux sont rapportés de façon contradictoire dans le "
            "dossier."
        )
        basis = None
    elif family_history is None:
        text = "Antécédents familiaux non renseignés dans le dossier."
        basis = None
    elif _reported_absent(family_history):
        text = "Antécédents familiaux rapportés sans particularité (RAS)."
        basis = "Antécédents familiaux : RAS"
    else:
        text = f"Antécédents familiaux rapportés : « {family_history} »."
        basis = f"Antécédents familiaux : {family_history}"

    return _referral(
        "Avis oncogénétique",
        GENETIC_APPROVAL,
        Observation(
            f"{text} L'indication d'une consultation d'oncogénétique dépend du lien de "
            "parenté, du type tumoral, de l'âge au diagnostic et du profil familial : "
            "RCPia ne se prononce pas.",
            basis=basis,
        ),
    )


def fertility_referral(
    *, age: str | None, sex: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Raise fertility preservation where it may still apply — and never close it.

    Suggests *oui* or nothing. Wrongly declining to raise fertility preservation is
    permanent; wrongly raising it costs a conversation.
    """
    years = number_in(age)
    female = is_female(sex)

    if "age" in contested or "sex" in contested:
        observation = Observation(
            "L'âge ou le sexe est rapporté de façon contradictoire dans le dossier ; la "
            "question de la préservation de la fertilité doit être posée explicitement."
        )
    elif years is None or female is None:
        observation = Observation(
            "Âge ou sexe non renseigné : la question de la préservation de la fertilité "
            "doit être posée explicitement."
        )
    else:
        limit = FERTILITY_AGE_FEMALE if female else FERTILITY_AGE_MALE
        if years <= limit:
            observation = Observation(
                f"Patient·e de {years:g} ans : la préservation de la fertilité doit être "
                "abordée avant tout traitement gonadotoxique.",
                suggestion="oui",
                basis=f"Âge : {age}",
            )
        else:
            observation = Observation(
                f"Patient·e de {years:g} ans, au-delà du repère indicatif de {limit} ans. "
                "La question reste à poser si un projet parental existe.",
                basis=f"Âge : {age}",
            )

    return _referral("Préservation fertilité", FERTILITY_APPROVAL, observation)


__all__ = [
    "Authority",
    "FERTILITY_AGE_FEMALE",
    "FERTILITY_AGE_MALE",
    "G8_THRESHOLD",
    "GERIATRIC_AGE",
    "fertility_referral",
    "genetic_referral",
    "geriatric_referral",
    "is_female",
    "number_in",
]
