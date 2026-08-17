"""Which referrals to propose, and on what recorded basis.

Section 2 of a real PPS records what the RCP decided about oncogeriatric assessment,
genetic referral, and fertility preservation. RCPia has no decision to record, so it
proposes — and a proposal is only useful if the reader can see what it was derived from.
Every rule here returns the value that triggered it.

The thresholds are the conventional ones and are stated in one place so a clinician can
argue with them. Where the ledger does not hold the field, the answer is `a_determiner`
rather than a guess: "we do not know" is the honest output, and the missing-information
report already says so in red.
"""

import re

from rcpia.cases.fact_schema import REPORTED_ABSENT
from rcpia.pps.schemas import Referral

# Age from which an oncogeriatric assessment is conventionally offered, and the G8 score
# at or below which it is indicated whatever the age.
GERIATRIC_AGE = 75
G8_THRESHOLD = 14.0

# Ages beyond which fertility preservation is not ordinarily discussed. Deliberately
# generous, because the cost of raising it needlessly is a conversation and the cost of
# missing it is permanent.
FERTILITY_AGE_MALE = 60
FERTILITY_AGE_FEMALE = 45

# A number in its own right, not a digit inside an identifier. Without the lookbehind,
# `G8 : 5/17` reads as 8 and `p T2` as 2 — a stage silently becoming a score.
NUMBER = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:[.,]\d+)?")


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


def geriatric_referral(*, age: str | None, g8: str | None) -> Referral:
    """Propose an oncogeriatric assessment on age or on a low G8."""
    years = number_in(age)
    score = number_in(g8)

    if score is not None and score <= G8_THRESHOLD:
        return Referral(
            label="Avis oncogériatrique",
            answer="oui",
            justification=(
                f"Score G8 à {g8} (seuil ≤ {G8_THRESHOLD:g}) : évaluation "
                "oncogériatrique indiquée avant tout traitement lourd."
            ),
            basis=f"G8 : {g8}",
        )

    if years is not None and years >= GERIATRIC_AGE:
        return Referral(
            label="Avis oncogériatrique",
            answer="oui",
            justification=(
                f"Patient de {years:g} ans (≥ {GERIATRIC_AGE} ans) : évaluation "
                "oncogériatrique à proposer."
            ),
            basis=f"Âge : {age}",
        )

    if years is None:
        return Referral(
            label="Avis oncogériatrique",
            answer="a_determiner",
            justification="Âge non renseigné dans le dossier : indication à trancher en RCP.",
        )

    return Referral(
        label="Avis oncogériatrique",
        answer="non",
        justification=(
            f"Patient de {years:g} ans, en deçà du seuil habituel de {GERIATRIC_AGE} ans."
            + (f" Score G8 à {g8}." if g8 else "")
        ),
        basis=f"Âge : {age}",
    )


def genetic_referral(*, family_history: str | None) -> Referral:
    """Propose a genetic referral when the dossier records a family history.

    `RAS` means the clinician looked and found none, which is an answer. A field nobody
    filled in is not, and must not be read as absence of family history.
    """
    if family_history is None:
        return Referral(
            label="Avis oncogénétique",
            answer="a_determiner",
            justification=(
                "Antécédents familiaux non renseignés : à recueillir avant de conclure."
            ),
        )

    if _reported_absent(family_history):
        return Referral(
            label="Avis oncogénétique",
            answer="non",
            justification="Antécédents familiaux rapportés sans particularité (RAS).",
            basis="Antécédents familiaux : RAS",
        )

    return Referral(
        label="Avis oncogénétique",
        answer="oui",
        justification=(
            "Antécédents familiaux rapportés : à évaluer pour une éventuelle "
            "consultation d'oncogénétique."
        ),
        basis=f"Antécédents familiaux : {family_history}",
    )


def fertility_referral(*, age: str | None, sex: str | None) -> Referral:
    """Propose a fertility-preservation discussion while it can still be offered."""
    years = number_in(age)
    female = is_female(sex)

    if years is None or female is None:
        return Referral(
            label="Préservation fertilité",
            answer="a_determiner",
            justification=(
                "Âge ou sexe non renseigné : la question de la préservation de la "
                "fertilité doit être posée explicitement."
            ),
        )

    limit = FERTILITY_AGE_FEMALE if female else FERTILITY_AGE_MALE
    if years <= limit:
        return Referral(
            label="Préservation fertilité",
            answer="oui",
            justification=(
                f"Patient·e de {years:g} ans : la préservation de la fertilité doit être "
                "abordée avant tout traitement gonadotoxique."
            ),
            basis=f"Âge : {age}",
        )

    return Referral(
        label="Préservation fertilité",
        answer="non",
        justification=(
            f"Patient·e de {years:g} ans, au-delà du seuil habituel de {limit} ans. "
            "À reconsidérer si le ou la patient·e en fait la demande."
        ),
        basis=f"Âge : {age}",
    )
