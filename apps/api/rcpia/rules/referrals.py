"""Which referrals to raise, and on what recorded basis.

Section 2 of a real PPS records what the RCP decided about oncogeriatric assessment,
genetic referral, and fertility preservation. RCPia has no decision to record, so it
proposes — and states the recorded value each proposal came from.

**The thresholds here are project choices, not clinician sign-off.** They are conventional
and two are published, but nobody has put their name to them for this deployment. So every
row carries `approved=False` and a caveat saying so, and the PPS renders the caveat next to
the answer. When a clinician signs off, set `provisional=False` in `approval.py` and record
their name; nothing else changes.

Two asymmetries shape the rules, and both come from what happens when they are wrong:

- **Fertility preservation never answers *Non*.** Wrongly declining to raise it is
  irreversible, while wrongly raising it costs a conversation. So the rule answers *Oui*
  below the age reference and *à déterminer* above it — never *Non*. The age limits are
  the least defensible numbers in this file and they are used only to decide whether to
  assert *Oui*, never to close the question.
- **A contested value is no basis for anything.** Where the dossier contradicts itself, the
  rule says so and answers *à déterminer* rather than picking a reading.
"""

import re
from dataclasses import dataclass

from rcpia.cases.fact_schema import REPORTED_ABSENT
from rcpia.pps.schemas import Answer, Referral
from rcpia.rules.approval import POC_PROJECT_CHOICE, Approval

# G8 ≤ 14 is the published screening cutoff for oncogeriatric assessment; the age is the
# usual convention for offering the screen at all.
GERIATRIC_AGE = 75
G8_THRESHOLD = 14.0

# Age references for raising fertility preservation. **Weakly grounded** — used only to
# decide when to assert *Oui*. Above them the rule says *à déterminer*, never *Non*.
FERTILITY_AGE_MALE = 60
FERTILITY_AGE_FEMALE = 45

# Project choices for the proof of concept, not clinician sign-off. See `approval.py`.
GERIATRIC_APPROVAL: Approval = POC_PROJECT_CHOICE
GENETIC_APPROVAL: Approval = POC_PROJECT_CHOICE
FERTILITY_APPROVAL: Approval = POC_PROJECT_CHOICE

# Family-history terms that bear on an oncogenetic referral. Without this the rule proposed
# a genetics referral for `Père : HTA` — a family history of hypertension.
CANCER_TERMS = (
    "cancer", "carcinom", "adenocarcinom", "adénocarcinom", "neoplas", "néoplas",
    "tumeur", "tumor", "sarcom", "lymphom", "leucem", "leucém", "melanom", "mélanom",
    "brca", "lynch", "sein", "ovaire", "pancreas", "pancréas", "prostate", "colon",
    "colorectal", "rein", "vessie", "uterus", "utérus", "metasta", "métasta",
)

# A number in its own right, not a digit inside an identifier. Without the lookbehind,
# `G8 : 5/17` reads as 8 and `p T2` as 2 — a stage silently becoming a score.
NUMBER = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Observation:
    """What a rule saw, and what it concludes from it."""

    text: str
    answer: Answer = "a_determiner"
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


def mentions_cancer(history: str) -> bool:
    """Whether a recorded family history names an oncological condition."""
    lowered = history.lower()
    return any(term in lowered for term in CANCER_TERMS)


def _referral(label: str, approval: Approval, observation: Observation) -> Referral:
    """Turn an observation into the row the document shows.

    An unapproved rule may not answer at all. A *provisionally* approved one may, because
    otherwise the proof of concept has nothing to put in front of a clinician — but the
    caveat travels with it and `approved` stays False, so `assert_provisional` still treats
    the answer as unsigned.
    """
    answer = observation.answer if approval.approved else "a_determiner"
    return Referral(
        label=label,
        answer=answer,
        justification=f"{observation.text} {approval.caveat()}",
        basis=observation.basis,
        # Only a clinician's sign-off makes this true. A project choice does not.
        approved=approval.approved and not approval.provisional,
        provisional=approval.approved and approval.provisional,
    )


def geriatric_referral(
    *, age: str | None, g8: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Propose an oncogeriatric assessment on a low G8, or on age when G8 is absent."""
    years = number_in(age)
    score = number_in(g8)

    if "g8_score" in contested or "age" in contested:
        observation = Observation(
            "L'âge ou le score G8 est rapporté de façon contradictoire dans le dossier ; "
            "aucune conclusion fiable ne peut en être tirée."
        )
    elif score is not None:
        indicated = score <= G8_THRESHOLD
        observation = Observation(
            f"Score G8 rapporté à {g8} "
            f"({'≤' if indicated else '>'} {G8_THRESHOLD:g}) : évaluation oncogériatrique "
            f"{'indiquée' if indicated else 'non requise sur ce seul critère'}.",
            answer="oui" if indicated else "non",
            basis=f"G8 : {g8}",
        )
    elif years is not None:
        old_enough = years >= GERIATRIC_AGE
        observation = Observation(
            f"Patient·e de {years:g} ans, score G8 non renseigné "
            f"(critère habituel : évaluation à partir de {GERIATRIC_AGE} ans).",
            answer="oui" if old_enough else "non",
            basis=f"Âge : {age}",
        )
    else:
        observation = Observation("Ni l'âge ni le score G8 ne sont renseignés.")

    return _referral("Avis oncogériatrique", GERIATRIC_APPROVAL, observation)


def genetic_referral(
    *, family_history: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Propose an oncogenetic referral when the family history names a cancer.

    Any recorded history used to trigger this, which proposed genetics for `Père : HTA` —
    a family history of hypertension. The rule now looks for an oncological term, and shows
    the recorded text either way so the RCP can overrule it.
    """
    if "family_history" in contested:
        observation = Observation(
            "Les antécédents familiaux sont rapportés de façon contradictoire dans le "
            "dossier."
        )
    elif family_history is None:
        observation = Observation("Antécédents familiaux non renseignés dans le dossier.")
    elif _reported_absent(family_history):
        observation = Observation(
            "Antécédents familiaux rapportés sans particularité (RAS).",
            answer="non",
            basis="Antécédents familiaux : RAS",
        )
    elif mentions_cancer(family_history):
        observation = Observation(
            f"Antécédents familiaux évoquant une pathologie tumorale : "
            f"« {family_history} ».",
            answer="oui",
            basis=f"Antécédents familiaux : {family_history}",
        )
    else:
        observation = Observation(
            f"Antécédents familiaux rapportés, sans pathologie tumorale identifiée : "
            f"« {family_history} ». À réexaminer si un antécédent oncologique existe.",
            answer="non",
            basis=f"Antécédents familiaux : {family_history}",
        )

    return _referral("Avis oncogénétique", GENETIC_APPROVAL, observation)


def fertility_referral(
    *, age: str | None, sex: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Raise fertility preservation where it may still apply — and never close it.

    This rule answers *Oui* or *à déterminer*, never *Non*. Wrongly declining to raise
    fertility preservation is permanent; wrongly raising it costs a conversation. The age
    references behind it are the weakest numbers in this module, so they are allowed to
    open the question and never to close it.
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
                answer="oui",
                basis=f"Âge : {age}",
            )
        else:
            # Deliberately not "non". See the docstring.
            observation = Observation(
                f"Patient·e de {years:g} ans, au-delà du repère indicatif de {limit} ans. "
                "RCPia ne conclut pas : la question reste à poser si un projet parental "
                "existe.",
                basis=f"Âge : {age}",
            )

    return _referral("Préservation fertilité", FERTILITY_APPROVAL, observation)
