"""The provisional PPS document.

**The document is assembled, not written.** Every value in it comes from the case ledger,
which carries provenance, or from a validated agent output, whose citations were checked
against the evidence it was given. No language model composes this document.

That is the whole point. The agent has already done the reasoning and had it validated;
asking a second model to render it into prose would create a place where a fact can change
between the ledger and the page, with nothing to catch it. A `PpsDocument` is therefore a
data structure with a renderer, and a field RCPia cannot fill is left visibly empty rather
than written around.

Clinical text is French, because the readers are the RCP. Keys are English, being code.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rcpia.rules.approval import SUGGESTION_CAVEAT, Authority

# Section 2 records a decision the RCP has not yet taken, and section 6 records validation
# that has not happened. These are the phrases the reference documents carry, and RCPia
# must never *claim* either.
#
# The check applies to the fields RCPia authors, never to the whole rendered document. An
# agent quoting a previous PPS, or a dossier containing one, may legitimately contain these
# words; refusing to render the document because they appear somewhere in it would break a
# valid case to prevent a claim nobody made. Agent prose cannot make the claim structurally
# because the renderer escapes every structural line start it could use.
FORBIDDEN_IN_GENERATED_OUTPUT = ("Statut : Décision prise", "Fiche validée : OUI")


class DocumentAssertsADecision(Exception):
    """The assembled document claims a decision or a validation. It must never."""

PROVISIONAL_BANNER = (
    "DOCUMENT PROVISOIRE — préparé automatiquement par RCPia à partir du dossier déposé. "
    "Aucune décision n'a été prise. Ce document doit être relu, corrigé et validé en RCP "
    "avant tout usage clinique."
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Answer = Literal["oui", "non", "a_determiner"]


class Field2(Strict):
    """A `label : value` row of a section table."""

    label: str
    value: str | None = None
    # Where the value came from, so the renderer can mark what RCPia could not fill.
    missing: bool = False


class Referral(Strict):
    """A referral question, what the dossier says about it, and who may answer.

    `answer` is **the checkbox** — the field a real fiche uses to record what the RCP
    decided. Only a clinician-approved rule may set it to anything but `a_determiner`.

    `suggestion` is RCPia's own non-binding reading, rendered apart from the checkbox and
    always labelled unvalidated. An earlier design let an unsigned threshold tick the box
    and printed a caveat underneath; the document then said "RCPia ne se prononce pas" and
    ticked Oui, and the tick was what a reader saw.

    `basis` names the recorded value behind the observation. An observation whose basis a
    reader cannot see is indistinguishable from an assertion.
    """

    label: str
    answer: Answer
    justification: str
    basis: str | None = None
    # RCPia's unvalidated reading. Never rendered as the checkbox.
    suggestion: Answer | None = None
    authority: Authority = Authority.none

    @model_validator(mode="after")
    def only_a_clinician_may_tick_the_box(self) -> "Referral":
        if (
            self.answer != "a_determiner"
            and self.authority is not Authority.clinician_approved
        ):
            raise ValueError(
                f"{self.label}: only a clinician-approved rule may set the checkbox; "
                f"authority is {self.authority}"
            )
        if self.suggestion is not None and self.authority is Authority.none:
            raise ValueError(
                f"{self.label}: a rule with no authority may not suggest either"
            )
        return self


class TreatmentStep(Strict):
    title: str
    items: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SupportItem(Strict):
    """A support-care item.

    Two kinds, and the difference is who is entitled to say it:

    - **standing** (`authority=none`, no trigger) — applies to any patient entering an
      oncology pathway, and the reference documents carry it unconditionally.
    - **triggered** (`authority=project_suggestion`) — RCPia's own reading of a recorded
      value against a threshold nobody has approved. Hb 9.9 produces an item and Hb 10.0
      does not, which is not a clinical distinction; it is a line the project drew.

    A triggered item must carry the canonical caveat, and the renderer prints the two kinds
    apart. An earlier version placed both in one list with an optional caveat, so an
    unapproved threshold silently became a block in the care plan — the same laundering the
    referral checkbox was fixed for, one section over.
    """

    title: str
    detail: str
    trigger: str | None = None
    # The threshold behind a triggered item and its standing. Required when the item is a
    # project suggestion; absent on standing items, which rest on no threshold.
    caveat: str | None = None
    authority: Authority = Authority.none

    @model_validator(mode="after")
    def a_suggestion_carries_its_caveat(self) -> "SupportItem":
        if self.authority is Authority.project_suggestion:
            if self.caveat is None or SUGGESTION_CAVEAT not in self.caveat:
                raise ValueError(
                    f"{self.title}: a project-threshold item must carry the canonical "
                    "caveat from approval.py"
                )
        elif self.trigger is not None:
            raise ValueError(
                f"{self.title}: an item triggered by a recorded value is a project "
                "reading and must say so"
            )
        return self


class MissingItem(Strict):
    label: str
    severity: Literal["blocking", "required", "uncertain"]
    highlight: Literal["red", "amber"]
    message: str


class EvidenceEntry(Strict):
    """One citation, resolved back to the text and the document it came from.

    This closes the loop a reader needs: recommendation -> citation id -> the sentence in
    the dossier -> the file it was uploaded from.
    """

    id: str
    quote: str
    supports: str | None = None
    source_document_id: str | None = None
    page: int | None = None


class NomogramFigure(Strict):
    """One validated calculator's answer, as the board reads it.

    **A computed figure, never an agent's estimate.** The clinician asked for exactly this
    separation — *nous voulons éviter que le modèle estime lui-même un risque sans utiliser le
    calculateur validé* — and a number printed on a tumour-board document is where that
    distinction either holds or is lost, because a plausible estimate and a nomogram's output
    look identical on the page.

    So the figure never appears without its model, its version and its limitation. Briganti
    2012's coefficients were reconstructed from a published graphic rather than released as an
    equation; a reader who cannot see that cannot weigh the number.

    **A refusal is printed too.** "MSKCC : manque l'antécédent d'hormonothérapie" tells the
    board what to obtain, and is more use to them than an absent row they never think to ask
    about.
    """

    display_name: str
    status: str
    # Endpoint label to percentage, already rounded for display. Several for MSKCC: organ
    # confinement drives nerve-sparing and nodal involvement drives the dissection, and those
    # are separate decisions that a single number would collapse.
    estimates: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    inputs_used: dict[str, str] = Field(default_factory=dict)
    limitation: str = ""
    source_url: str = ""


class RationaleAppendix(Strict):
    """One agent's reasoning, at most one A4 page — the cap the design fixes."""

    agent_role: str
    model: str | None = None
    # Each agent read some version of the ledger, and two agents need not have read the
    # same one. Recorded per appendix so a reader can tell.
    ledger_version: int | None = None
    rationale: str
    key_facts_used: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    recommendation_withheld: bool = False
    # What the agent wrote in place of a recommendation when it withheld one. Kept here,
    # in the reasoning, and never in section 3 — see `Treatments`.
    withheld_statement: str | None = None


class Synthesis(Strict):
    patient: str
    diagnosis: str | None = None
    comorbidities: str | None = None
    question: str


class Decision(Strict):
    """Section 2. `taken` is always False in a generated document."""

    taken: bool = False
    statement: str
    referrals: list[Referral] = Field(default_factory=list)


class Treatments(Strict):
    """Section 3.

    When the agent withholds a recommendation, `steps` and `rejected` are **empty**. The
    section then carries only what is missing and what is uncertain. Printing the agent's
    text under a heading saying it declined to recommend produces a document that says
    "no proposal" and then reads as a proposal — and a reader acts on the part that looks
    like a plan, not on the disclaimer above it.
    """

    steps: list[TreatmentStep] = Field(default_factory=list)
    precisions: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    rejected: list[TreatmentStep] = Field(default_factory=list)
    status: str
    withheld: bool = False

    @model_validator(mode="after")
    def a_withheld_section_proposes_nothing(self) -> "Treatments":
        if self.withheld and (self.steps or self.rejected):
            raise ValueError(
                "a withheld recommendation must render no treatment steps: the agent "
                "declined to propose, so section 3 has nothing to carry"
            )
        return self


class SupportCare(Strict):
    """Section 4, in two parts.

    `items` is care that applies to any patient entering an oncology pathway. `suggested`
    is RCPia's own reading of recorded values against thresholds nobody approved, and the
    renderer prints it under its own heading — interleaving the two would let an unapproved
    threshold read as part of the care plan.
    """

    items: list[SupportItem] = Field(default_factory=list)
    suggested: list[SupportItem] = Field(default_factory=list)
    carers: str | None = None
    location: str | None = None
    status: str

    @model_validator(mode="after")
    def standing_care_carries_no_project_threshold(self) -> "SupportCare":
        for item in self.items:
            if item.authority is not Authority.none:
                raise ValueError(
                    f"{item.title}: a project reading belongs in `suggested`, not in the "
                    "standing care plan"
                )
        return self


class SocialAssessment(Strict):
    rows: list[Field2] = Field(default_factory=list)
    comment: str | None = None


class CareFramework(Strict):
    """Section 6.

    The three yes/no fields are **typed**, not prose. They used to be display strings, and
    a string check for "[X] Non" passed a value reading `[X] Non … [X] Oui`. State that
    matters is state, and the renderer draws the boxes from it.
    """

    reference_standard: str
    clinical_trial: Answer = "a_determiner"
    clinical_trial_note: str | None = None
    # Always False in a generated document, and `assert_provisional` enforces it.
    validated: bool = False
    validated_note: str
    sent_to_gp: bool = False
    sent_to_gp_note: str
    participants: str
    review: str


class PanelPoint(Strict):
    """One line of the panel's agreement structure, as the board reads it.

    Flattened from the synthesis output into a single shape, because a reader scanning the
    section cares about *what kind of point this is and who held it* before anything else.
    `kind` drives the marker; `attribution` is the sentence that makes the provenance visible
    without opening a JSON file — which is precisely what a board cannot do.
    """

    kind: Literal["convergence", "divergence", "gap"]
    point: str
    attribution: str
    detail: str = ""


class PanelAgreement(Strict):
    """What the specialists agreed on, disagreed on, and did not both address.

    Empty when a single agent produced the document: there is no panel to report on, and a
    section announcing unanimity among one speciality would be worse than no section.
    """

    points: list[PanelPoint] = Field(default_factory=list)
    # Named so the reader knows whose silence is being reported. A gap is only meaningful
    # against a known set of participants.
    specialities: list[str] = Field(default_factory=list)
    # And the ones that did not sit. Naming only the contributors makes any panel read as a
    # complete one: two specialities of eight look like a board rather than a quarter of one,
    # and the illusion strengthens as agents are added, because seven names look like all of
    # them. A reader cannot weigh a silence without knowing who was never asked to speak.
    absent: list[str] = Field(default_factory=list)
    # Kept apart from `absent`, because they are different facts and they ask different things
    # of the board. A speciality nobody wired has no opinion to recover. One that was asked and
    # whose run failed has an opinion that a re-run would produce, and the decision below was
    # taken without it. Folding the second into the first makes the fiche assert that nobody
    # asked, which is not true and is worse than the silence it replaced.
    failed: list[str] = Field(default_factory=list)


class PpsDocument(Strict):
    """A provisional PPS, ready to render.

    `ledger_version` and `agent_run_ids` are what the document was built from. Together
    they make it reproducible: the same inputs assemble the same clinical content.
    """

    case_id: str
    case_reference: str
    cancer_type: str
    generated_on: date
    # The version the lead agent actually read — not necessarily the newest.
    ledger_version: int
    # Set when the case has moved on since: someone corrected a field after the analysis,
    # so this opinion was formed on data that is no longer current.
    superseded_by: int | None = None
    agent_run_ids: list[str] = Field(default_factory=list)
    # The document is regenerated rather than stored, so what produced it has to travel
    # inside it. Without these two a downloaded copy cannot be matched to the runs and the
    # code that made it, and "regenerate to check" silently compares different things.
    generator_version: str = "0"

    banner: str = PROVISIONAL_BANNER
    synthesis: Synthesis
    decision: Decision
    # Empty unless a synthesis agent reconciled several specialists.
    panel: PanelAgreement = Field(default_factory=PanelAgreement)
    treatments: Treatments
    # Printed inside section 3, before the proposals: the nodal-invasion risk is what decides
    # whether an extended dissection is proposed, and reading the proposal before its input is
    # backwards. Empty for a bladder case, where the prostate models do not apply.
    nomograms: list[NomogramFigure] = Field(default_factory=list)
    support: SupportCare
    social: SocialAssessment
    framework: CareFramework

    missing: list[MissingItem] = Field(default_factory=list)
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    appendices: list[RationaleAppendix] = Field(default_factory=list)

    def assert_provisional(self) -> None:
        """Refuse to leave RCPia claiming a decision or a validation.

        Checks **typed state**, not rendered strings. A previous version searched for
        `"[X] Non"` inside a display string, which a value reading `[X] Non … [X] Oui`
        satisfied while claiming the opposite. Booleans cannot be phrased around.
        """
        if self.decision.taken:
            raise DocumentAssertsADecision("decision.taken is True in a generated PPS")
        if self.framework.validated:
            raise DocumentAssertsADecision("framework.validated is True in a generated PPS")
        if self.framework.sent_to_gp:
            raise DocumentAssertsADecision("a generated PPS was marked as sent to the GP")
        if self.treatments.withheld and (
            self.treatments.steps or self.treatments.rejected
        ):
            raise DocumentAssertsADecision(
                "a withheld recommendation is carrying treatment content"
            )

        # Only a clinician's sign-off may tick a box. Checked on typed state, not by
        # searching the justification for a caveat: a substring test passes on unrelated
        # text that happens to contain the phrase, and the previous two attempts at this
        # guard were both defeated that way.
        for referral in self.decision.referrals:
            if (
                referral.answer != "a_determiner"
                and referral.authority is not Authority.clinician_approved
            ):
                raise DocumentAssertsADecision(
                    f"{referral.label}: {referral.authority} ticked the decision box"
                )

        # Prose RCPia authors must not carry the phrases that mean "decided" and
        # "validated". This is not applied to the whole document: an agent or a dossier
        # may legitimately quote a previous fiche, and the claim is structural.
        for text in (self.decision.statement, self.banner, self.framework.validated_note):
            for forbidden in FORBIDDEN_IN_GENERATED_OUTPUT:
                if forbidden in text:
                    raise DocumentAssertsADecision(
                        f"a generated field claims {forbidden!r}"
                    )
