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

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# One A4 page, the limit the design fixes for each agent's rationale.
MAX_RATIONALE_CHARACTERS = 3000
MAX_TEXT = 4000

# The shape of an id RCPia hands out. Used to catch citations written into prose, where
# no allowlist would otherwise look. Case-insensitive, because a model writing "SNIP-999"
# has made exactly the same claim as one writing "snip-999".
CITATION_IN_PROSE = re.compile(r"\b(?:snip|ev)-\d{1,6}\b", re.IGNORECASE)


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
    # A snippet with no document behind it is not traceable, which is the only reason
    # snippets exist.
    source_document_id: str = Field(min_length=1, max_length=64)
    source_chunk_id: str | None = None
    page: int | None = None
    section: str | None = None


class GuidelinePassage(Strict):
    """A passage of published guidance, with enough provenance to be checked and weighed.

    **`clinical_status` has no default on purpose.** Every passage in the corpus is
    `poc_unreviewed` — no clinician has read a word of it — and a passage nobody has reviewed
    must not reach a page looking like one that has been. Requiring it means a caller cannot
    construct a passage without stating where it stands, rather than being trusted to
    remember.

    `version` matters for the same reason `page` does: an opinion cited against AFU 2024-2026
    has to stay checkable after the 2027 edition lands.
    """

    # **128, not 64, and the reason is traceability.** A corpus chunk id runs to 120
    # characters, so a 64-character cap forced a short `gl-001` label and a lookup table
    # mapping it back. A citation an agent emits should resolve straight to the corpus, the
    # gold set and the safety rules with no table in between that can drift.
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source: str
    # The document the passage came from, kept apart from `source`, which carries the full
    # citation down to the section and page. Section 6 of the fiche answers "which guidelines
    # were consulted", and a board reading a concatenation of every passage label cannot see
    # that. Deduplicating needs the document, not the citation.
    document_id: str | None = None
    clinical_status: str = Field(min_length=1, max_length=50)
    section: str | None = None
    version: str | None = None
    published_at: str | None = None
    # Where to look in the source. `recommendation_number` is what a clinician quotes to a
    # colleague; a page alone is harder to argue with.
    page: int | None = None
    recommendation_number: int | None = None
    # How the page was derived: `source_html_block` is exact, `pdf_text_alignment` inferred.
    locator_method: str | None = None


class ConflictingField(Strict):
    """A field the dossier contradicts itself about, with each reading's source."""

    field: str
    values: list[str] = Field(min_length=2)
    source_ids: list[str] = Field(default_factory=list)


class LedgerField(Strict):
    """One field of the case ledger as the agent sees it.

    `origin` distinguishes a value a regex scraped off a form from one a clinician
    deliberately entered, and `correction_note` carries the reason they gave. A human
    correction has no source excerpt to cite, so without these two fields it would arrive
    looking like an unsourced machine reading — which is the opposite of what it is.

    Being clinician-entered is *provenance*, not proof: the agent is told who recorded a
    value, and is told in the constraints not to treat that as stronger evidence than the
    dossier itself.
    """

    key: str
    label: str
    value: str | None = None
    unit: str | None = None
    status: str
    origin: Literal["machine", "human"] = "machine"
    correction_note: str | None = Field(default=None, max_length=MAX_TEXT)
    source_ids: list[str] = Field(default_factory=list)


class BundleInputs(Strict):
    """Everything the agent may know about the case.

    Adding a field here is a deliberate act. In particular there is no field for another
    agent's recommendation: the first pass is independent by construction.
    """

    case_id: str
    agent_role: str
    cancer_type: str
    # Free text an operator may supply. It is the one channel through which another
    # agent's opinion could still reach a first pass — nothing can stop someone pasting
    # one in — so a bundle carrying a custom question is not an independent first pass
    # and says so here rather than pretending otherwise.
    clinical_question: str | None = Field(default=None, max_length=MAX_TEXT)
    first_pass: bool = True
    ledger_version: int
    case_ledger: list[LedgerField] = Field(default_factory=list)
    source_snippets: list[SourceSnippet] = Field(default_factory=list)
    guideline_passages: list[GuidelinePassage] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[ConflictingField] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def citable_ids_are_unique(self) -> "BundleInputs":
        """Across both collections, not within each.

        An id shared by a snippet and a guideline passage would make every citation of it
        ambiguous, and a reviewer checking the reference would have no way to tell which
        of the two the agent meant.
        """
        snippets = [item.id for item in self.source_snippets]
        passages = [item.id for item in self.guideline_passages]

        for label, items in (
            ("source_snippets", snippets),
            ("guideline_passages", passages),
        ):
            if len(items) != len(set(items)):
                raise ValueError(f"{label} contains duplicate ids")

        shared = sorted(set(snippets) & set(passages))
        if shared:
            raise ValueError(
                "the same id is used by a snippet and a guideline passage, making a "
                f"citation ambiguous: {', '.join(shared)}"
            )
        return self

    @model_validator(mode="after")
    def a_custom_question_is_not_a_first_pass(self) -> "BundleInputs":
        """`first_pass` states a fact about the bundle rather than accepting a claim.

        It is derived from whether an operator supplied a question, so it cannot drift
        out of step with the thing it describes. Setting it explicitly to the wrong value
        is rejected rather than quietly corrected: silently fixing a safety flag would
        hide a caller that misunderstood what it means.
        """
        derived = not self.clinical_question
        if "first_pass" in self.model_fields_set and self.first_pass != derived:
            raise ValueError(
                "clinical_question is operator-supplied free text, so a bundle carrying "
                f"one cannot declare first_pass={self.first_pass}"
            )
        self.first_pass = derived
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

    # Matches `GuidelinePassage.id`. An agent citing a corpus passage emits its chunk id, and
    # a cap below the length of one would refuse the citation rather than the invention.
    id: str = Field(min_length=1, max_length=128)
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


class UngroundedRecommendation(ValueError):
    """The agent recommended something without citing any of the evidence it was given."""


def _prose_of(output: AgentOutput) -> list[str]:
    """Every free-text field an agent could write a citation into."""
    return [
        output.recommendation,
        output.rationale,
        *output.key_facts_used,
        *output.contraindications,
        *output.uncertainty_notes,
        *output.missing_information,
        *(reference.supports for reference in output.evidence_references),
        *(
            text
            for option in (*output.alternatives, *output.rejected_options)
            for text in (option.option, option.rationale or "")
        ),
    ]


def check_citations(output: AgentOutput, bundle: TaskBundle) -> None:
    """Reject citations the agent invented, and recommendations grounded in nothing.

    A fabricated reference is worse than no reference: it looks like grounding while
    being none, and a reviewer would have to check every id by hand to notice.

    Prose is scanned too. `evidence_references` is the structured channel, but an agent
    that writes "selon snip-999" into its rationale has made a claim of provenance that no
    allowlist would otherwise inspect — and a reviewer reading the text would take it at
    face value.
    """
    allowed = bundle.citable_ids()
    on_recommendation = {reference.id for reference in output.evidence_references}
    on_options = {
        evidence_id
        for option in (*output.alternatives, *output.rejected_options)
        for evidence_id in option.evidence_ids
    }
    # Matched case-insensitively: an id is only miscased or fabricated, and a reviewer
    # reading "SNIP-001" would find snip-001 without difficulty.
    allowed_folded = {identifier.casefold() for identifier in allowed}
    in_prose = {
        found.casefold()
        for text in _prose_of(output)
        for found in CITATION_IN_PROSE.findall(text)
    }

    invented = sorted(
        {
            identifier
            for identifier in (on_recommendation | on_options)
            if identifier.casefold() not in allowed_folded
        }
        | {identifier for identifier in in_prose if identifier not in allowed_folded}
    )
    if invented:
        raise UncitedEvidence(
            "L'agent a cité des preuves qui ne lui ont pas été fournies : "
            + ", ".join(invented)
        )

    # Citing nothing is honest only when there was nothing to cite. With evidence in the
    # bundle, an uncited recommendation is an assertion, and RCPia cannot stand behind it.
    #
    # `evidence_references` specifically, not any citation anywhere in the output: an
    # agent that grounds a rejected alternative and asserts its actual recommendation has
    # produced exactly the document this check exists to refuse.
    if allowed and not on_recommendation and not output.recommendation_withheld:
        raise UngroundedRecommendation(
            f"L'agent a formulé une recommandation sans citer aucune des {len(allowed)} "
            "sources qui lui ont été fournies (les citations portées par les "
            "alternatives ne fondent pas la recommandation elle-même)."
        )


def agent_output_json_schema() -> dict[str, Any]:
    """The schema embedded in the bundle, so the agent is told exactly what to return."""
    return AgentOutput.model_json_schema()
