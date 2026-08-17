"""Which support-care items the recorded data calls for.

Section 4 of the reference documents lists transfusion, nutrition, pain, psychological
support, nursing, physiotherapy and social support. A generator that emitted all seven for
every case would be writing a template, not a proposal, and the reader would learn nothing
from it.

So each item here is either **triggered** by a recorded value — and then states that value
— or **standing**, meaning it applies to any patient entering an oncology pathway and the
reference documents carry it unconditionally. Nothing is invented from the diagnosis, and
the section says plainly that the RCP completes it.

One of the reference documents prescribes intravenous albumin as nutritional repletion.
That is a practice error: albumin is not nutrition, and correcting a low albumin by
infusing it treats the number rather than the patient. RCPia proposes the oral-then-enteral
route instead. This is the reason `docs/reference/pps-format.md` says neither example is a
content benchmark.
"""

from collections.abc import Callable
from dataclasses import dataclass

from rcpia.cases.ledger import display_label
from rcpia.pps.schemas import SupportItem
from rcpia.rules.referrals import number_in

# Transfusion threshold used by the reference documents, and the nutritional thresholds
# from the usual scores. Stated here so a clinician can argue with them in one place.
HAEMOGLOBIN_TRANSFUSION = 10.0
ALBUMIN_LOW = 30.0
MNA_MALNOURISHED = 17.0
EGFR_IMPAIRED = 60.0


@dataclass(frozen=True)
class Trigger:
    """A support item conditional on a recorded value."""

    key: str
    title: str
    detail: str
    applies: Callable[[float], bool]
    unit: str = ""

    def evaluate(self, raw: str | None) -> SupportItem | None:
        value = number_in(raw)
        if value is None or not self.applies(value):
            return None
        return SupportItem(
            title=self.title,
            detail=self.detail,
            # Named, not bare: "7" tells a reader nothing, "Score MNA : 7" tells them
            # which value called for this and lets them check it.
            trigger=f"{display_label(self.key)} : {raw}{self.unit}",
        )


TRIGGERED: tuple[Trigger, ...] = (
    Trigger(
        key="haemoglobin",
        title="Transfusion sanguine",
        detail=(
            "Anémie documentée : évaluer l'indication transfusionnelle et corriger avant "
            f"tout traitement myélotoxique (objectif usuel Hb ≥ {HAEMOGLOBIN_TRANSFUSION:g} "
            "g/dL). Surveillance NFS."
        ),
        applies=lambda value: value < HAEMOGLOBIN_TRANSFUSION,
        unit=" g/dL",
    ),
    Trigger(
        key="albumin",
        title="Support nutritionnel",
        detail=(
            "Hypoalbuminémie documentée : évaluation diététique, compléments nutritionnels "
            "oraux hyperprotidiques, et nutrition entérale en cas d'échec. "
            "L'albumine intraveineuse n'est pas un support nutritionnel."
        ),
        applies=lambda value: value < ALBUMIN_LOW,
        unit=" g/L",
    ),
    Trigger(
        key="mna",
        title="Support nutritionnel",
        detail=(
            "Score MNA abaissé : évaluation diététique et compléments nutritionnels oraux, "
            "avec réévaluation du poids et de l'albuminémie."
        ),
        applies=lambda value: value < MNA_MALNOURISHED,
    ),
    Trigger(
        key="egfr",
        title="Surveillance néphrologique",
        detail=(
            "Fonction rénale altérée : adapter les posologies, éviter les néphrotoxiques, "
            "et confirmer la clairance avant toute chimiothérapie à base de sels de platine."
        ),
        applies=lambda value: value < EGFR_IMPAIRED,
        unit=" mL/min",
    ),
)

# Applied to every case: the reference documents carry these unconditionally, and none of
# them depends on a value RCPia would have to guess at.
STANDING: tuple[SupportItem, ...] = (
    SupportItem(
        title="Prise en charge de la douleur",
        detail=(
            "Évaluation systématique de la douleur (EVA) à chaque consultation, "
            "antalgiques adaptés par paliers, avis algologie si douleurs réfractaires ou "
            "neuropathiques."
        ),
    ),
    SupportItem(
        title="Soutien psychologique",
        detail=(
            "Consultation de psycho-oncologie proposée dès l'annonce, au patient et à "
            "son entourage."
        ),
    ),
    SupportItem(
        title="Accompagnement social",
        detail=(
            "Évaluation par l'assistante sociale : couverture, aides à domicile, "
            "transport, droits sociaux."
        ),
    ),
)

COMPLETED_BY_RCP = (
    "Cette liste est dérivée des seules valeurs enregistrées dans le dossier ; elle n'est "
    "pas exhaustive et doit être complétée en RCP."
)


def support_items(values: dict[str, str | None]) -> list[SupportItem]:
    """Support items the recorded values call for, then the standing ones.

    Two triggers can name the same item — a low albumin and a low MNA both call for
    nutritional support — so the first to fire keeps the title and the second is dropped
    rather than repeating the heading with a different reason.
    """
    items: list[SupportItem] = []
    seen: set[str] = set()

    for trigger in TRIGGERED:
        item = trigger.evaluate(values.get(trigger.key))
        if item is None or item.title in seen:
            continue
        seen.add(item.title)
        items.append(item)

    return [*items, *STANDING]
