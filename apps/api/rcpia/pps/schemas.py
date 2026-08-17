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

from pydantic import BaseModel, ConfigDict, Field

# Section 2 records a decision the RCP has not yet taken. RCPia never fills it, and these
# two strings — which the reference documents carry — must never appear in its output.
FORBIDDEN_IN_GENERATED_OUTPUT = ("Statut : Décision prise", "Fiche validée : OUI")

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
    """A referral RCPia *proposes*. The RCP decides.

    `basis` names the recorded value the proposal was derived from. A proposal whose basis
    a reader cannot see is indistinguishable from an assertion, and this document must
    never assert.
    """

    label: str
    answer: Answer
    justification: str
    basis: str | None = None


class TreatmentStep(Strict):
    title: str
    items: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SupportItem(Strict):
    """A support-care item, and the recorded value that called for it."""

    title: str
    detail: str
    trigger: str | None = None


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


class RationaleAppendix(Strict):
    """One agent's reasoning, at most one A4 page — the cap the design fixes."""

    agent_role: str
    model: str | None = None
    rationale: str
    key_facts_used: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    recommendation_withheld: bool = False


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
    steps: list[TreatmentStep] = Field(default_factory=list)
    precisions: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    rejected: list[TreatmentStep] = Field(default_factory=list)
    status: str
    withheld: bool = False


class SupportCare(Strict):
    items: list[SupportItem] = Field(default_factory=list)
    carers: str | None = None
    location: str | None = None
    status: str


class SocialAssessment(Strict):
    rows: list[Field2] = Field(default_factory=list)
    comment: str | None = None


class CareFramework(Strict):
    reference_standard: str
    clinical_trial: str
    validated: str
    sent_to_gp: str
    participants: str
    review: str


class PpsDocument(Strict):
    """A provisional PPS, ready to render.

    `ledger_version` and `agent_run_ids` are what the document was built from. Together
    they make it reproducible: the same inputs assemble the same clinical content.
    """

    case_id: str
    case_reference: str
    cancer_type: str
    generated_on: date
    ledger_version: int
    agent_run_ids: list[str] = Field(default_factory=list)

    banner: str = PROVISIONAL_BANNER
    synthesis: Synthesis
    decision: Decision
    treatments: Treatments
    support: SupportCare
    social: SocialAssessment
    framework: CareFramework

    missing: list[MissingItem] = Field(default_factory=list)
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    appendices: list[RationaleAppendix] = Field(default_factory=list)
