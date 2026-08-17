"""Which referrals to raise, and on what recorded basis.

Section 2 of a real PPS records what the RCP decided about oncogeriatric assessment,
genetic referral, and fertility preservation. RCPia has no decision to record.

**It does not propose one either.** The thresholds below are conventional but unapproved
(see `approval.py`), so every rule here returns `à déterminer` and states the observation
it would have reasoned from. An unapproved rule that ticked *Non* for fertility
preservation would put a decision no one made into a form where a tick means a decision.

What the rules still do, and what makes them worth having: they surface the recorded value
next to the criterion that bears on it, so the RCP is asked the question with the evidence
already in front of it.

A value the dossier contradicts itself about is not a basis for anything. Where a field is
contested, the observation says so instead of picking a reading.
"""

import re
from dataclasses import dataclass

from rcpia.cases.fact_schema import REPORTED_ABSENT
from rcpia.pps.schemas import Referral
from rcpia.rules.approval import UNAPPROVED, Approval

# Age from which an oncogeriatric assessment is conventionally offered, and the G8 score
# at or below which it is indicated whatever the age. G8 ≤ 14 is the published cutoff; the
# age is convention. Neither is approved for use here — see `approval.py`.
GERIATRIC_AGE = 75
G8_THRESHOLD = 14.0

# Ages beyond which fertility preservation is not ordinarily discussed. **These two are
# invented.** They come from no guideline, which is exactly why nothing may be decided
# from them.
FERTILITY_AGE_MALE = 60
FERTILITY_AGE_FEMALE = 45

GERIATRIC_APPROVAL: Approval = UNAPPROVED
GENETIC_APPROVAL: Approval = UNAPPROVED
FERTILITY_APPROVAL: Approval = UNAPPROVED

# A number in its own right, not a digit inside an identifier. Without the lookbehind,
# `G8 : 5/17` reads as 8 and `p T2` as 2 — a stage silently becoming a score.
NUMBER = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Observation:
    """What a rule saw, and what it would have concluded had it been approved."""

    text: str
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
    """Turn an observation into the row the document shows.

    While the rule is unapproved the answer is always `à déterminer`. When one is approved,
    this is the single place that has to learn to answer.
    """
    if not approval.approved:  # pragma: no branch - nothing is approved yet
        return Referral(
            label=label,
            answer="a_determiner",
            justification=f"{observation.text} {approval.caveat()}",
            basis=observation.basis,
            approved=False,
        )
    raise NotImplementedError(  # pragma: no cover - reached only once a rule is approved
        f"{label}: approved rules must define how they answer"
    )


def geriatric_referral(
    *, age: str | None, g8: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Observe what bears on an oncogeriatric assessment."""
    years = number_in(age)
    score = number_in(g8)

    if "g8_score" in contested or "age" in contested:
        observation = Observation(
            "L'âge ou le score G8 est rapporté de façon contradictoire dans le dossier ; "
            "aucune observation fiable ne peut en être tirée.",
        )
    elif score is not None:
        observation = Observation(
            f"Score G8 rapporté à {g8} (critère habituel : évaluation indiquée si "
            f"≤ {G8_THRESHOLD:g}).",
            basis=f"G8 : {g8}",
        )
    elif years is not None:
        observation = Observation(
            f"Patient·e de {years:g} ans (critère habituel : évaluation proposée à partir "
            f"de {GERIATRIC_AGE} ans). Score G8 non renseigné.",
            basis=f"Âge : {age}",
        )
    else:
        observation = Observation("Ni l'âge ni le score G8 ne sont renseignés.")

    return _referral("Avis oncogériatrique", GERIATRIC_APPROVAL, observation)


def genetic_referral(
    *, family_history: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Observe the recorded family history.

    RCPia does not judge whether a family history is *oncologically* relevant. A rule that
    treated any recorded history as relevant proposed a genetics referral for `Père : HTA`,
    which is a family history of hypertension. Deciding what counts needs a clinician, so
    the recorded text is shown and the question is put to the RCP.
    """
    if "family_history" in contested:
        observation = Observation(
            "Les antécédents familiaux sont rapportés de façon contradictoire dans le "
            "dossier.",
        )
    elif family_history is None:
        observation = Observation("Antécédents familiaux non renseignés dans le dossier.")
    elif _reported_absent(family_history):
        observation = Observation(
            "Antécédents familiaux rapportés sans particularité (RAS).",
            basis="Antécédents familiaux : RAS",
        )
    else:
        observation = Observation(
            f"Antécédents familiaux rapportés : « {family_history} ». Leur pertinence "
            "oncologique est à apprécier.",
            basis=f"Antécédents familiaux : {family_history}",
        )

    return _referral("Avis oncogénétique", GENETIC_APPROVAL, observation)


def fertility_referral(
    *, age: str | None, sex: str | None, contested: frozenset[str] = frozenset()
) -> Referral:
    """Observe what bears on fertility preservation.

    The most dangerous rule in this file if it were allowed to answer: a wrongly ticked
    *Non* is permanent, and the age limits behind it were invented rather than sourced.
    """
    years = number_in(age)
    female = is_female(sex)

    if "age" in contested or "sex" in contested:
        observation = Observation(
            "L'âge ou le sexe est rapporté de façon contradictoire dans le dossier.",
        )
    elif years is None or female is None:
        observation = Observation(
            "Âge ou sexe non renseigné : la question de la préservation de la fertilité "
            "doit être posée explicitement.",
        )
    else:
        limit = FERTILITY_AGE_FEMALE if female else FERTILITY_AGE_MALE
        observation = Observation(
            f"Patient·e de {years:g} ans (repère indicatif, non validé : {limit} ans). "
            "La préservation de la fertilité doit être abordée avant tout traitement "
            "gonadotoxique lorsqu'elle est envisageable.",
            basis=f"Âge : {age}",
        )

    return _referral("Préservation fertilité", FERTILITY_APPROVAL, observation)
