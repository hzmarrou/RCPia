"""Who is entitled to answer a clinical question, and how far.

A threshold in this codebase is a clinical claim. `FERTILITY_AGE_FEMALE = 45` says a
46-year-old woman need not be offered fertility preservation, and section 2 of a fiche
renders that as a ticked box — the same mark, in the same form, that a room of oncologists
uses to record a decision.

**Only a clinician's sign-off may tick a box.** An unsigned threshold may at most *suggest*,
and a suggestion is rendered somewhere a reader cannot mistake for the decision field.

An earlier version of this module tried a middle state that ticked the box and printed a
caveat beneath it. A reviewer rejected it in one line: the rendered document said
*"RCPia ne se prononce pas"* and then ticked Oui. The caveat was true and the tick was what
a reader saw. The lesson is encoded here as three levels rather than a boolean, so the
difference is in the type system instead of in a comment.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Authority(StrEnum):
    """How far a rule may go in answering."""

    # A clinician signed the threshold off. The rule may set the checkbox.
    clinician_approved = "clinician_approved"
    # A project choice. The rule may offer a suggestion, shown apart from the checkbox and
    # labelled as unvalidated. It may never set the checkbox.
    project_suggestion = "project_suggestion"
    # No usable rule exists. The rule may not even suggest.
    none = "none"


# The one wording used wherever an unvalidated suggestion is shown. Canonical, so no code
# path can weaken it and no string check has to look for it.
SUGGESTION_CAVEAT = (
    "Suggestion RCPia — non validée cliniquement, à confirmer ou infirmer en RCP."
)


@dataclass(frozen=True)
class Approval:
    """The standing of one clinical threshold."""

    authority: Authority
    source: str | None = None
    approved_by: str | None = None
    approved_on: date | None = None

    @property
    def may_answer(self) -> bool:
        """Whether this rule may set the checkbox itself."""
        return self.authority is Authority.clinician_approved

    @property
    def may_suggest(self) -> bool:
        return self.authority in (
            Authority.clinician_approved,
            Authority.project_suggestion,
        )


# No usable rule. Used where a correct rule needs structure RCPia does not have.
NO_RULE = Approval(authority=Authority.none)

# Conventional thresholds chosen by the project so RCPia can show its reasoning. They may
# suggest and they may not decide.
#
# `approved_by` deliberately does not name a person. Writing one in would produce a record
# indistinguishable from a real sign-off, and the purpose of this module is that a
# threshold carries the name of whoever accepted responsibility for it. When a clinician
# does, set `authority=Authority.clinician_approved` and record their name and the date.
PROJECT_SUGGESTION = Approval(
    authority=Authority.project_suggestion,
    source="seuils conventionnels de la littérature",
    approved_by="Équipe RCPia — décision projet, en attente de validation clinicienne",
    approved_on=date(2026, 8, 19),
)
