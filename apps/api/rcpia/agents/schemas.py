"""Task bundles and agent outputs.

A task bundle is everything a specialist agent is allowed to know. An agent output is
everything RCPia will accept back. Both are strict: unknown fields are rejected rather
than ignored, because a bundle that silently carried an extra key would be the easiest
way to break the property the design depends on.

**Independence is structural.** During the first pass an agent must not see another
agent's recommendation, and that is enforced here — `BundleInputs` forbids extra fields,
so there is no key through which another agent's output could travel. It is not left to
an instruction in `brain.md` asking the model to ignore something it can see.

Clinical text is French; JSON keys are English, because they are code.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# One A4 page, the limit the design fixes for each agent's rationale.
MAX_RATIONALE_CHARACTERS = 3000
MAX_TEXT = 4000


class Strict(BaseModel):
    """Reject unknown fields everywhere in this module."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# What an agent is given
# --------------------------------------------------------------------------------------


class SourceSnippet(Strict):
    """A quotation from the dossier, with where it came from.

    Agents cite these by `id`; the adapter checks afterwards that every citation matches
    one, so a fabricated reference cannot reach the PPS.
    """

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source_document_id: str
    source_chunk_id: str | None = None
    page: int | None = None
    section: str | None = None


class GuidelinePassage(Strict):
    """A passage of published guidance. Empty until evidence ingestion exists."""

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source: str
    section: str | None = None
    version: str | None = None
    published_at: str | None = None


class ConflictingField(Strict):
    """A field the dossier contradicts itself about, with each reading's source."""

    field: str
    values: list[str] = Field(min_length=2)
    source_ids: list[str] = Field(default_factory=list)


class LedgerField(Strict):
    """One field of the case ledger as the agent sees it."""

    key: str
    label: str
    value: str | None = None
    unit: str | None = None
    status: str
    source_ids: list[str] = Field(default_factory=list)


class BundleInputs(Strict):
    """Everything the agent may know about the case.

    Adding a field here is a deliberate act. In particular there is no field for another
    agent's recommendation: the first pass is independent by construction.
    """

    case_id: str
    agent_role: str
    cancer_type: str
    clinical_question: str | None = None
    ledger_version: int
    case_ledger: list[LedgerField] = Field(default_factory=list)
    source_snippets: list[SourceSnippet] = Field(default_factory=list)
    guideline_passages: list[GuidelinePassage] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[ConflictingField] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def snippet_ids_are_unique(self) -> "BundleInputs":
        for label, items in (
            ("source_snippets", [item.id for item in self.source_snippets]),
            ("guideline_passages", [item.id for item in self.guideline_passages]),
        ):
            if len(items) != len(set(items)):
                raise ValueError(f"{label} contains duplicate ids")
        return self


class OutputFormat(Strict):
    type: Literal["json"] = "json"
    schema_: dict[str, Any] = Field(alias="schema")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TaskBundle(Strict):
    """The file handed to Pi via `-p "@path"`.

    Goal / Audience / Inputs / Output format / Constraints, the structure proven in the
    reference fleet.
    """

    goal: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    inputs: BundleInputs
    output_format: OutputFormat
    constraints: list[str] = Field(min_length=1)

    def citable_ids(self) -> set[str]:
        """Every id an agent is allowed to cite."""
        return {snippet.id for snippet in self.inputs.source_snippets} | {
            passage.id for passage in self.inputs.guideline_passages
        }


# --------------------------------------------------------------------------------------
# What an agent returns
# --------------------------------------------------------------------------------------


class EvidenceReference(Strict):
    """A citation, which must point at something the agent was actually given."""

    id: str = Field(min_length=1, max_length=64)
    supports: str = Field(min_length=1, max_length=MAX_TEXT)


class TreatmentOption(Strict):
    option: str = Field(min_length=1, max_length=MAX_TEXT)
    rationale: str | None = Field(default=None, max_length=MAX_TEXT)
    evidence_ids: list[str] = Field(default_factory=list)


class AgentOutput(Strict):
    """A specialist's structured opinion.

    `rejected_options` and `uncertainty_notes` are required rather than optional: an
    opinion that lists only what to do, without what was considered and discarded or what
    remains unknown, is not reviewable by an RCP.
    """

    agent_role: str = Field(min_length=1, max_length=64)
    case_id: str
    key_facts_used: list[str] = Field(default_factory=list)
    recommendation: str = Field(min_length=1, max_length=MAX_TEXT)
    alternatives: list[TreatmentOption] = Field(default_factory=list)
    rejected_options: list[TreatmentOption] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    # True when the agent judges that no safe recommendation is possible yet. That is a
    # legitimate and useful answer, so it is a first-class field rather than prose.
    recommendation_withheld: bool = False
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARACTERS)

    @field_validator("rationale")
    @classmethod
    def rationale_within_one_page(cls, value: str) -> str:
        if len(value) > MAX_RATIONALE_CHARACTERS:  # pragma: no cover - Field enforces it
            raise ValueError(
                f"Rationale exceeds {MAX_RATIONALE_CHARACTERS} characters (one A4 page)"
            )
        return value

    @model_validator(mode="after")
    def withheld_recommendations_say_why(self) -> "AgentOutput":
        if self.recommendation_withheld and not (
            self.missing_information or self.uncertainty_notes
        ):
            raise ValueError(
                "recommendation_withheld requires missing_information or uncertainty_notes "
                "explaining what prevents a recommendation"
            )
        return self


class UncitedEvidence(ValueError):
    """The agent cited an id it was never given."""


def check_citations(output: AgentOutput, bundle: TaskBundle) -> None:
    """Reject citations the agent invented.

    A fabricated reference is worse than no reference: it looks like grounding while
    being none, and a reviewer would have to check every id by hand to notice.
    """
    allowed = bundle.citable_ids()
    cited = {reference.id for reference in output.evidence_references}
    cited |= {
        evidence_id
        for option in (*output.alternatives, *output.rejected_options)
        for evidence_id in option.evidence_ids
    }

    invented = sorted(cited - allowed)
    if invented:
        raise UncitedEvidence(
            "Agent cited evidence it was not given: " + ", ".join(invented)
        )


def agent_output_json_schema() -> dict[str, Any]:
    """The schema embedded in the bundle, so the agent is told exactly what to return."""
    return AgentOutput.model_json_schema()
