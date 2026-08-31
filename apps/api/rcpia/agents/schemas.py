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
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rcpia.corpus.vocabulary import AGENT_ROLES

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
    agent's recommendation **on a first pass**: independence is the property that makes a panel
    worth convening, and it is enforced here rather than requested in a brain. `agent_opinions`
    is the one exception, populated only for the synthesis role, whose entire job is to read
    what the specialists said; a bundle carrying it sets `first_pass=False` and says so.
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
    # Empty for every first pass. Populated only for `rcp_synthesis`.
    agent_opinions: list["AgentOpinion"] = Field(default_factory=list)
    # Specialities the panel asked for that produced no usable opinion. Also synthesis-only.
    #
    # Stated rather than left to be noticed. The agent sees answers, never the request, so an
    # absent speciality is indistinguishable from one nobody convened — and those two call for
    # opposite things from a tumour board: chase the missing opinion, or do not go looking for
    # an opinion that was never sought.
    missing_opinions: list[str] = Field(default_factory=list)

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
    def independence_is_derived_not_declared(self) -> "BundleInputs":
        """`first_pass` states a fact about the bundle rather than accepting a claim.

        It is derived from what the bundle actually carries, so it cannot drift out of step
        with the thing it describes. Setting it explicitly to the wrong value is rejected
        rather than quietly corrected: silently fixing a safety flag would hide a caller that
        misunderstood what it means.

        **Two things end independence, and the second was added later.** An operator's free-text
        question is one — nothing stops someone pasting another agent's opinion into it.
        `agent_opinions` is the other, and a much plainer one: a bundle holding the specialists'
        conclusions is the opposite of an independent first pass. When only the question was
        checked here, building the synthesis bundle failed against this very validator, which is
        the check doing its job — the premise that a question was the *only* way to lose
        independence had quietly stopped being true.
        """
        derived = not self.clinical_question and not self.agent_opinions
        if "first_pass" in self.model_fields_set and self.first_pass != derived:
            raise ValueError(
                "first_pass is derived from what the bundle carries (a clinical_question or "
                f"another agent's opinions), so it cannot be declared {self.first_pass} here"
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


class AgentOpinion(Strict):
    """One specialist's finished opinion, as the synthesis agent reads it.

    **This is the field `BundleInputs` says is deliberately absent, present for exactly one
    role.** Every other agent's bundle omits it, and `first_pass` records which kind of bundle
    this is. A synthesis that never saw the opinions could not name a divergence; a first pass
    that saw them would not be independent, and the panel's whole value is that the specialists
    did not read each other.

    Carried as structured fields rather than prose so the synthesis cites positions rather than
    paraphrasing them, and so `agent_role` on each opinion is the name it must use when it
    reports who disagreed with whom.
    """

    agent_role: str = Field(min_length=1, max_length=64)
    recommendation: str = Field(min_length=1, max_length=MAX_TEXT)
    alternatives: list[TreatmentOption] = Field(default_factory=list)
    rejected_options: list[TreatmentOption] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    recommendation_withheld: bool = False
    rationale: str = Field(default="", max_length=MAX_RATIONALE_CHARACTERS)


# `BundleInputs.agent_opinions` is annotated as a string above, because the opinion is
# built from output types and this file puts inputs first. Resolve it now that they exist.
BundleInputs.model_rebuild()


SYNTHESIS_ROLE = "rcp_synthesis"


# The names a synthesis may use for a speciality, and the display labels it reaches for instead.
#
# Measured across fifteen dossiers: eleven syntheses named specialities by role key
# (`medical_oncology`) and three by French display label (`Oncologie médicale`). The fiche
# renders both identically, so the inconsistency is invisible to a reader and fatal to any
# programmatic check on attribution — the same shape as the fabricated-convergence defect. The
# cause is that the agent is handed French labels in its constraints while the schema example
# uses keys.
#
# Normalised rather than rejected: a run that named the right speciality in the wrong vocabulary
# got the clinical judgement right, and discarding it would cost an opinion to punish a spelling.
# What must not survive is the ambiguity.
ROLE_ALIASES: dict[str, str] = {
    "oncologie urologique": "urology_oncology",
    "urologie": "urology_oncology",
    "oncologie medicale": "medical_oncology",
    "radiotherapie": "radiation_oncology",
    "oncologie radiotherapie": "radiation_oncology",
    "radiologie": "radiology",
    "anatomopathologie": "pathology",
    "pathologie": "pathology",
    "oncogeriatrie": "geriatric_frailty",
    "geriatrie": "geriatric_frailty",
    "nutrition": "nutrition",
    "soins de support": "palliative_supportive",
    "soins de support et palliatifs": "palliative_supportive",
    "soins palliatifs": "palliative_supportive",
}


def normalise_role(name: str) -> str:
    """A speciality name as the corpus spells it, whichever way the agent wrote it.

    Raises when the name matches nothing. A synthesis attributing a position to a speciality the
    panel does not have has not made a spelling mistake — it has named a participant that never
    sat at the table, and no downstream check could tell the two apart afterwards.
    """
    cleaned = " ".join(str(name).strip().split())
    if cleaned in AGENT_ROLES:
        return cleaned

    folded = unicodedata.normalize("NFD", cleaned.lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    alias = ROLE_ALIASES.get(folded)
    if alias is not None:
        return alias

    raise ValueError(
        f"{name!r} ne designe aucune specialite du panel : utilisez l'identifiant du role."
    )


def _roles(values: list[str]) -> list[str]:
    return [normalise_role(value) for value in values]


class AgentPosition(Strict):
    """One specialist's side of a disagreement, named rather than paraphrased."""

    role: str = Field(min_length=1, max_length=64)
    position: str = Field(min_length=1, max_length=MAX_TEXT)

    @field_validator("role")
    @classmethod
    def role_is_a_speciality(cls, value: str) -> str:
        return normalise_role(value)


class AgentSupport(Strict):
    """Where in one specialist's own opinion the claimed position appears.

    **This is the field that makes an attribution checkable.** A synthesis previously asserted
    that two specialities agreed and nothing could test the assertion: measured across fifteen
    dossiers, one convergence in ninety-two credited an agent with a position it never took, and
    the failure was invisible in the rendered fiche. Requiring a quotation turns the claim into
    something `check_synthesis` can verify against the stored opinion.

    It also enforces one claim per point without trying to parse French. A convergence bundling
    a shared assertion with an unshared one — the carboplatin defect — cannot produce a quotation
    from the silent agent, so the compound point fails where the atomic ones pass. The rule is
    not "reject sentences containing *et*", which would fire on most clinical prose; it is
    "prove it, per agent".
    """

    role: str = Field(min_length=1, max_length=64)
    # Long enough to be locatable. A three-word quotation matches half the document.
    quote: str = Field(min_length=12, max_length=MAX_TEXT)

    @field_validator("role")
    @classmethod
    def role_is_a_speciality(cls, value: str) -> str:
        return normalise_role(value)


class Convergence(Strict):
    """A point the specialists reached independently, and who reached it.

    `agents_agreeing` needs at least two entries: one agent saying something is not a
    convergence, it is that agent's opinion, and recording it as agreement would manufacture
    consensus out of a single voice — the exact failure a panel exists to prevent.
    """

    point: str = Field(min_length=1, max_length=MAX_TEXT)
    agents_agreeing: list[str] = Field(min_length=2)
    evidence_summary: str = Field(default="", max_length=MAX_TEXT)

    # One quotation per agreeing speciality, from that speciality's own opinion.
    support: list[AgentSupport] = Field(default_factory=list)

    @model_validator(mode="after")
    def every_named_agent_is_attested(self) -> "Convergence":
        """Naming an agent and showing where it said so are the same act, done properly.

        Left optional, this field would be skipped exactly when it matters — a fabricated
        agreement is the one a model cannot quote.
        """
        attested = {item.role for item in self.support}
        unattested = [role for role in self.agents_agreeing if role not in attested]
        if unattested:
            raise ValueError(
                "Chaque specialite citee dans une convergence doit etre appuyee par une "
                f"citation de son propre avis ; manquant pour : {', '.join(unattested)}."
            )
        return self

    @field_validator("agents_agreeing")
    @classmethod
    def named_specialities_exist(cls, value: list[str]) -> list[str]:
        """Normalised, then de-duplicated — in that order, and the order is the point.

        `["Oncologie medicale", "medical_oncology"]` is one speciality written twice. Counted
        before normalising it satisfies the two-name rule on its own, which is a single voice
        recorded as a consensus — exactly what that rule exists to prevent.
        """
        seen = list(dict.fromkeys(_roles(value)))
        if len(seen) < 2:
            raise ValueError(
                "Une convergence doit nommer au moins deux specialites distinctes ; "
                f"celle-ci n'en nomme qu'une ({seen[0] if seen else '-'})."
            )
        return seen


class Divergence(Strict):
    """A point the specialists did not reach together.

    **Exposed, never resolved.** RCPia does not arbitrate between two specialists — that is
    what the board is for — so this type has no field for a preferred side. `clinical_impact`
    is what makes the disagreement actionable: a divergence that changes nothing for the patient
    does not need board time, and one that changes the treatment does.
    """

    point: str = Field(min_length=1, max_length=MAX_TEXT)
    agent_a: AgentPosition
    agent_b: AgentPosition
    clinical_impact: str = Field(min_length=1, max_length=MAX_TEXT)
    # Whether the board must settle this before a plan can proceed.
    resolution_required: bool = True

    @model_validator(mode="after")
    def a_speciality_does_not_diverge_from_itself(self) -> "Divergence":
        """Two sides written in two vocabularies can be the same speciality, and a board asked
        to arbitrate between an agent and itself would be arbitrating nothing."""
        if self.agent_a.role == self.agent_b.role:
            raise ValueError(
                "Une divergence oppose deux specialites differentes ; "
                f"les deux cotes sont ici {self.agent_a.role}."
            )
        return self


class SupportiveCarePlan(Strict):
    """The support half of the plan, which no single specialist owns.

    Every field is optional prose: a synthesis with nothing to say about psychological support
    should say nothing rather than invent a line to fill the shape.
    """

    nutrition: str | None = Field(default=None, max_length=MAX_TEXT)
    pain_management: str | None = Field(default=None, max_length=MAX_TEXT)
    psychological_support: str | None = Field(default=None, max_length=MAX_TEXT)
    nursing: str | None = Field(default=None, max_length=MAX_TEXT)
    social: str | None = Field(default=None, max_length=MAX_TEXT)


class CoverageGap(Strict):
    """A point one speciality raised and another never addressed.

    **The category the first synthesis run did not have, and needed.** Asked to reconcile two
    opinions where the medical oncologist ruled out carboplatin and the urologist never mentioned
    it, the synthesis had only two boxes to put that in, and chose `convergence` — recording the
    urologist as agreeing to something it had not said. Silence is not assent, and it is not
    dissent either; it is a third thing, and a board needs to see it as a third thing, because
    the action it calls for is *ask the silent speciality*, not *choose between two positions*.

    `raised_by` and `silent` are separate lists rather than a pair, because the same gap can be
    raised by two specialities and unaddressed by three.
    """

    point: str = Field(min_length=1, max_length=MAX_TEXT)
    raised_by: list[str] = Field(min_length=1)
    silent: list[str] = Field(min_length=1)
    clinical_impact: str = Field(default="", max_length=MAX_TEXT)

    @field_validator("raised_by", "silent")
    @classmethod
    def named_specialities_exist(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(_roles(value)))


class SynthesisOutput(AgentOutput):
    """What the synthesis agent returns: a specialist's opinion plus what the panel disagreed on.

    A subclass rather than optional fields on `AgentOutput`, because the difference is not
    decoration. Every other agent is forbidden to hold another agent's opinion, so no other
    agent *can* report a convergence; a schema that let them would be describing a system where
    the first pass is not independent.
    """

    convergences: list[Convergence] = Field(default_factory=list)
    divergences: list[Divergence] = Field(default_factory=list)
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    supportive_care_plan: SupportiveCarePlan | None = None

    @model_validator(mode="after")
    def a_synthesis_reports_what_it_compared(self) -> "SynthesisOutput":
        """Silence about both agreement and disagreement is not a synthesis.

        An agent that read two opinions and reported neither a convergence nor a divergence has
        either not compared them or has hidden the comparison in prose, and in both cases the
        board is being handed a single plan whose provenance it cannot see. Withholding a
        recommendation is exempt: an agent that cannot conclude is not required to have found
        structure first.
        """
        structure = self.convergences or self.divergences or self.coverage_gaps
        if not self.recommendation_withheld and not structure:
            raise ValueError(
                "Une synthèse doit rapporter au moins une convergence, une divergence ou une "
                "lacune de couverture : sans cela, elle présente un plan unique sans montrer "
                "d'où il vient."
            )
        return self

    @model_validator(mode="after")
    def a_speciality_is_not_both_agreeing_and_silent(self) -> "SynthesisOutput":
        """The same speciality cannot agree to a point and be silent on it.

        Not a hypothetical: the failure this whole category exists for was a speciality being
        listed as agreeing to something it never mentioned. If a later run reports both, the
        two claims cannot both be true and the board would have no way to tell which to believe.
        """
        for gap in self.coverage_gaps:
            overlap = set(gap.raised_by) & set(gap.silent)
            if overlap:
                raise ValueError(
                    "Une spécialité ne peut pas être à la fois à l'origine d'un point et "
                    f"silencieuse sur ce point : {', '.join(sorted(overlap))}"
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


def output_model_for(agent_role: str) -> type[AgentOutput]:
    """Which output schema this role answers under.

    One place decides it, so the schema an agent is *given* and the schema its answer is
    *checked against* cannot drift apart — they are the same call.
    """
    return SynthesisOutput if agent_role == SYNTHESIS_ROLE else AgentOutput


def agent_output_json_schema(agent_role: str = "") -> dict[str, Any]:
    """The schema embedded in the bundle, so the agent is told exactly what to return."""
    return output_model_for(agent_role).model_json_schema()


class UnattestedAgreement(ValueError):
    """A synthesis claimed an agreement it could not quote from the agent's own opinion."""


def check_synthesis(output: "SynthesisOutput", bundle: TaskBundle) -> None:
    """Verify every claimed agreement against the opinion it claims to summarise.

    The quotation must appear in that speciality's own output. Compared on words rather than
    bytes — a model requotes with the spacing and accents rearranged, and demanding an exact
    match would reject honest citations and make the check worthless.

    This is the mechanical half of the fix for fabricated attribution. The brain asks for one
    claim per point; this refuses the claim that cannot be shown.
    """
    opinions = {
        normalise_role(opinion.agent_role): _searchable(opinion)
        for opinion in bundle.inputs.agent_opinions
    }

    for convergence in output.convergences:
        for item in convergence.support:
            haystack = opinions.get(item.role)
            if haystack is None:
                raise UnattestedAgreement(
                    f"La convergence cite {item.role}, dont aucun avis n'a ete fourni."
                )
            if _words(item.quote) not in haystack:
                raise UnattestedAgreement(
                    f"La citation attribuee a {item.role} ne figure pas dans son avis : "
                    f"{item.quote[:80]!r}"
                )


def _words(text: str) -> str:
    folded = unicodedata.normalize("NFD", text.lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def _searchable(opinion: AgentOpinion) -> str:
    """One agent's opinion as continuous text, in the order a reader meets it.

    **Each option is followed by its own rationale**, and that ordering is not cosmetic. Listing
    every option first and every rationale afterwards destroys the adjacency a reader sees, and
    a synthesis quoting an option together with its reason — *"Cystectomie radicale d'emblée.
    Disproportionnée pour une première TVNIM…"*, which is exactly how that opinion reads — was
    rejected as a fabrication on the first real run. A check that refuses a truthful attestation
    is worse than no check: it blocks correct work and teaches everyone to switch it off.
    """
    parts: list[str] = [
        opinion.recommendation,
        opinion.rationale,
        *opinion.contraindications,
        *opinion.uncertainty_notes,
        *opinion.missing_information,
    ]
    for option in (*opinion.alternatives, *opinion.rejected_options):
        parts.append(option.option)
        # An option may carry no rationale; the empty string keeps it adjacent to its own
        # option rather than silently pulling the next option's text alongside it.
        parts.append(option.rationale or "")
    parts.extend(reference.supports for reference in opinion.evidence_references)
    return _words(" ".join(part for part in parts if part))
