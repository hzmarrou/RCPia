"""Clinical approval of the thresholds RCPia reasons with.

A threshold in this codebase is a clinical claim. `FERTILITY_AGE_FEMALE = 45` says a
46-year-old woman need not be offered fertility preservation, and the PPS renders that as a
ticked box in the same form, with the same weight, as a decision a room full of oncologists
took. Nobody has approved these numbers.

So a rule states whether it has been approved, and an unapproved rule **may not answer**.
It may only observe: it shows the recorded value and the criterion it would apply, marks
the question `à déterminer`, and leaves the answer to the RCP. Adding a "seuil non validé"
caption to a ticked box does not fix this — the box is still ticked, and a reader skimming
the form sees the tick, not the caption.

To approve a rule, set `Approval(approved=True, ...)` naming the clinician and the source.
That is a deliberate act with a name attached to it, which is the point.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Approval:
    """Whether a clinician has signed off a threshold, and on what basis."""

    approved: bool
    source: str | None = None
    approved_by: str | None = None
    approved_on: date | None = None

    # True when the threshold is a project decision rather than a clinician's. Such a
    # rule may answer — otherwise the POC has nothing to show anyone — but the document
    # must never let it read as clinically validated.
    provisional: bool = False

    def caveat(self) -> str:
        """What the document says about this rule's standing."""
        if self.approved and self.provisional:
            return (
                "Seuil conventionnel retenu par l'équipe RCPia pour cette version "
                "d'évaluation, **non validé cliniquement** : à confirmer ou corriger en RCP."
                + (f" Référence : {self.source}." if self.source else "")
            )
        if self.approved:
            return f"Critère validé — {self.source}" if self.source else "Critère validé"
        return (
            "Critère non validé cliniquement : RCPia ne se prononce pas, la question est "
            "laissée à la RCP."
        )


UNAPPROVED = Approval(approved=False)

# Thresholds chosen by the project so the proof of concept can answer at all. They are
# conventional, and two of them are published; none has a clinician's name against it.
#
# `approved_by` deliberately does not name a person. Writing one in would produce a record
# indistinguishable from a real sign-off, and the whole purpose of this module is that a
# threshold carries the name of whoever accepted responsibility for it. When a clinician
# does, replace these with `provisional=False` and their name.
POC_PROJECT_CHOICE = Approval(
    approved=True,
    provisional=True,
    source="seuils conventionnels de la littérature",
    approved_by="Équipe RCPia — décision projet, en attente de validation clinicienne",
    approved_on=date(2026, 8, 19),
)
