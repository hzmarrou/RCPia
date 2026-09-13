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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from rcpia.agents.pps_fiche import ConciseFiche, InstitutionalFiche
from rcpia.agents.pps_proposal import PpsProposal
from rcpia.corpus.vocabulary import AGENT_ROLES
from rcpia.evidence.payload import MAX_EVIDENCE_CHARACTERS
from rcpia.query.decision import QueryProvenance

# One A4 page, the limit the design fixes for each agent's rationale.
MAX_RATIONALE_CHARACTERS = 3000
MAX_TEXT = 4000

# The shape of an id RCPia hands out. Used to catch citations written into prose, where
# no allowlist would otherwise look. Case-insensitive, because a model writing "SNIP-999"
# has made exactly the same claim as one writing "snip-999".
CITATION_IN_PROSE = re.compile(r"\b(?:snip|ev)-\d{1,6}\b", re.IGNORECASE)

# The other id RCPia hands out, and the one the prose scan could not see: a corpus passage,
# named after where it came from --
# `eau.bladder-mibc.2026.6-8-1-b-radiation-therapy.01.1198`.
#
# Measured: a radiation oncology run failed three times as an ungrounded recommendation
# while its rationale carried five of these, every one of them a real id from its own
# bundle. The recommendation genuinely was ungrounded -- grounding is the structured
# channel -- but the diagnosis was wrong, and a wrong diagnosis sends the next person
# looking at retrieval, which was working.
#
# Anchored on an alphabetic source prefix (`eau`, `aua`, `nccn`, `afu`) and a numeric tail,
# with at least four dotted segments between them, so that a decimal, a section number and
# an ordinary French sentence cannot be read as a citation.
CORPUS_ID_IN_PROSE = re.compile(
    r"\b[a-z]{2,}[a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*){2,}\.\d{1,6}\b", re.IGNORECASE
)


def citations_in_prose(text: str) -> set[str]:
    """Every id-shaped string in one piece of free text, folded for comparison."""
    return {found.casefold() for found in CITATION_IN_PROSE.findall(text)} | {
        found.casefold() for found in CORPUS_ID_IN_PROSE.findall(text)
    }


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
    # Set only when the excerpt was assembled from chunks landing on more than one page --
    # a multi-paragraph imaging report, typically. `None` means the whole excerpt is on
    # `page`, exactly as before this field existed.
    page_end: int | None = None
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

    New deliveries preserve the stored contextual unit under the dedicated evidence bound.
    Older stored bundles remain readable as written; no missing text or context is repaired.
    """

    # **128, not 64, and the reason is traceability.** A corpus chunk id runs to 120
    # characters, so a 64-character cap forced a short `gl-001` label and a lookup table
    # mapping it back. A citation an agent emits should resolve straight to the corpus, the
    # gold set and the safety rules with no table in between that can drift.
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=MAX_EVIDENCE_CHARACTERS)
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
    page_end: int | None = None
    recommendation_number: int | None = None
    # How the page was derived: `source_html_block` is exact, `pdf_text_alignment` inferred.
    locator_method: str | None = None


class ConflictingField(Strict):
    """A field the dossier contradicts itself about, with each reading's source."""

    field: str
    values: list[str] = Field(min_length=2)
    source_ids: list[str] = Field(default_factory=list)


class NomogramReading(Strict):
    """What a validated calculator said about this patient, or why it said nothing.

    **The clinician's requirement, verbatim:**

        Nous voulons éviter que le modèle estime lui-même un risque sans utiliser le
        calculateur validé.

    A language model asked for a lymph-node invasion probability will produce a number:
    plausible, confident, and not the nomogram's. Nothing downstream could tell, because it
    arrives in the same shape as a real one. So the figure is computed deterministically and
    handed to the agent, and the constraints forbid it producing one of its own.

    **A refusal is carried too, not filtered out.** "Briganti 2023 needs the mpMRI index-lesion
    diameter" tells a tumour board what to obtain; showing only the models that produced a
    number would silently drop that sentence, and would also let an agent assume no calculator
    applied when in fact one was two fields away.

    These ids are not citable evidence and deliberately do not appear in `citable_ids()`: a
    nomogram is a computation over this patient's own ledger, not a source to quote.
    """

    model_id: str | None = None
    display_name: str
    status: str
    # Endpoint name to percentage, display-rounded. Several for MSKCC, one for Briganti:
    # collapsing them would lose the one the surgeon needs, since organ confinement drives
    # nerve-sparing and nodal involvement drives the lymph-node dissection.
    estimates: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    inputs_used: dict[str, str] = Field(default_factory=dict)
    # What the number does not mean, and where its arithmetic came from. Briganti 2012's
    # coefficients were reconstructed from a published graphic rather than released as an
    # equation, and a reader who cannot see that cannot weigh the figure.
    limitation: str = ""
    source_url: str = ""


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
    retrieval_query: QueryProvenance | None = None
    first_pass: bool = True
    ledger_version: int
    case_ledger: list[LedgerField] = Field(default_factory=list)
    source_snippets: list[SourceSnippet] = Field(default_factory=list)
    guideline_passages: list[GuidelinePassage] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[ConflictingField] = Field(default_factory=list)
    # Computed from this patient's own ledger by a published model, never estimated. Carries
    # refusals as well as figures: a model that names the one measurement it lacks tells the
    # board what to obtain.
    nomograms: list[NomogramReading] = Field(default_factory=list)
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

    @model_serializer(mode="wrap")
    def omit_unused_retrieval_query(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.retrieval_query is None:
            data.pop("retrieval_query", None)
        return data

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
        derived = (
            not self.clinical_question and not self.agent_opinions
            and (self.retrieval_query is None or self.retrieval_query.request is None)
        )
        if "first_pass" in self.model_fields_set and self.first_pass != derived:
            raise ValueError(
                "first_pass is derived from what the bundle carries (a clinical_question, "
                "a recorded retrieval decision, or another agent's opinions), so it cannot "
                f"be declared {self.first_pass} here"
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
    """One specialist's side of a disagreement, named rather than paraphrased.

    **`quote` makes the attribution checkable, the same way `AgentSupport` does for a
    convergence.** A divergence naming two specialities and their positions was, until this
    field existed, unverifiable in exactly the way a convergence used to be: nothing stopped
    a synthesis from putting words in a specialist's mouth to manufacture a disagreement, or
    to make a real one sound more extreme than either side actually wrote. `check_synthesis`
    requires the quotation to appear in that speciality's own opinion.
    """

    role: str = Field(min_length=1, max_length=64)
    position: str = Field(min_length=1, max_length=MAX_TEXT)
    # Long enough to be locatable. A three-word quotation matches half the document.
    quote: str = Field(min_length=12, max_length=MAX_TEXT)

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

    **`raised_by` carries a quotation per speciality; `silent` does not, and cannot.**
    A speciality that raised a point can be quoted raising it — the same mechanism a
    convergence uses. A speciality that said nothing has, by definition, no sentence to quote:
    demanding one would not verify the silence, it would force a citation of something else
    entirely and call it proof. `silent` stays a plain role list; `check_synthesis` only
    confirms each name there is a real speciality that was actually convened, which
    `named_specialities_exist` already guarantees.
    """

    point: str = Field(min_length=1, max_length=MAX_TEXT)
    raised_by: list[AgentSupport] = Field(min_length=1)
    silent: list[str] = Field(min_length=1)
    clinical_impact: str = Field(default="", max_length=MAX_TEXT)

    @field_validator("silent")
    @classmethod
    def named_specialities_exist(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(_roles(value)))

    @field_validator("raised_by")
    @classmethod
    def raised_by_names_distinct_specialities(
        cls, value: list[AgentSupport]
    ) -> list[AgentSupport]:
        seen: dict[str, AgentSupport] = {}
        for item in value:
            seen[item.role] = item
        return list(seen.values())


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
            overlap = {item.role for item in gap.raised_by} & set(gap.silent)
            if overlap:
                raise ValueError(
                    "Une spécialité ne peut pas être à la fois à l'origine d'un point et "
                    f"silencieuse sur ce point : {', '.join(sorted(overlap))}"
                )
        return self


class DecisionLedSynthesisOutput(SynthesisOutput):
    """Required for newly dispatched syntheses; historical outputs remain readable."""

    pps_proposal: PpsProposal
    recommendation_withheld: Literal[False] = False


class ConciseSynthesisOutput(DecisionLedSynthesisOutput):
    pps_fiche: ConciseFiche

    @model_validator(mode="after")
    def fiche_covers_the_detailed_plan(self) -> "ConciseSynthesisOutput":
        self.pps_fiche.validate_for(self.pps_proposal)
        return self


class InstitutionalSynthesisOutput(DecisionLedSynthesisOutput):
    pps_fiche: InstitutionalFiche

    @model_validator(mode="after")
    def fiche_covers_the_detailed_plan(self) -> "InstitutionalSynthesisOutput":
        self.pps_fiche.validate_for(self.pps_proposal)
        return self


class UncitedEvidence(ValueError):
    """The agent cited an id it was never given."""


class UngroundedRecommendation(ValueError):
    """The agent recommended something without citing any of the evidence it was given."""


class CitationInWrongChannel(ValueError):
    """The agent wrote a real citation into prose instead of `evidence_references`.

    Separate from `UngroundedRecommendation` because the two ask for different fixes. An
    ungrounded recommendation may mean retrieval gave the agent nothing worth citing; a
    citation in the wrong channel means the agent read the evidence, chose the right
    passage, and put the reference where nothing can check it. Reporting the second as the
    first sends the next reader to the retriever, which is not where the fault is.

    This does not make prose citations count as grounding. A recommendation is grounded
    through `evidence_references` and nowhere else -- the structured channel is what a
    reviewer can resolve, and what the fiche renders.
    """


def _prose_of(output: AgentOutput) -> list[str]:
    """Every free-text field an agent could write a citation into."""
    return [
        *(output.pps_fiche.prose() if isinstance(
            output, (ConciseSynthesisOutput, InstitutionalSynthesisOutput)
        ) else []),
        *(output.pps_proposal.prose() if isinstance(output, DecisionLedSynthesisOutput) else []),
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
    on_proposal = (
        output.pps_proposal.cited_ids() if isinstance(output, DecisionLedSynthesisOutput) else set()
    )
    # Matched case-insensitively: an id is only miscased or fabricated, and a reviewer
    # reading "SNIP-001" would find snip-001 without difficulty.
    allowed_folded = {identifier.casefold() for identifier in allowed}
    in_prose = {found for text in _prose_of(output) for found in citations_in_prose(text)}

    invented = sorted(
        {
            identifier
            for identifier in (on_recommendation | on_options | on_proposal)
            if identifier.casefold() not in allowed_folded
        }
        | {identifier for identifier in in_prose if identifier not in allowed_folded}
    )
    if invented:
        raise UncitedEvidence(
            "L'agent a cité des preuves qui ne lui ont pas été fournies : "
            + ", ".join(invented)
        )

    # A real id, written where nothing can resolve it. Checked before grounding so the
    # failure names what the agent actually did: an answer whose references are all in the
    # prose reports as an ungrounded recommendation, which is true and misleading at once.
    #
    # Two exemptions, both measured against the runs this check would have rejected.
    #
    # An id already present in a structured channel is not misplaced -- an alternative
    # carries its evidence in `evidence_ids`, and repeating it in the surrounding sentence
    # is prose about a citation, not a citation hidden in prose.
    #
    # And only *guideline passages* are subject to this at all. A source snippet is a fact
    # from this patient's own dossier, and `key_facts_used` exists precisely to name those
    # in prose: two otherwise healthy runs, carrying eight and six references each, listed
    # twenty-odd `snip-NNN` there. Reading that as a misplaced citation would fail an
    # agent for using the field as intended.
    structured = {
        identifier.casefold() for identifier in (on_recommendation | on_options | on_proposal)
    }
    passages = {passage.id.casefold() for passage in bundle.inputs.guideline_passages}
    misplaced = sorted(
        identifier
        for identifier in in_prose
        if identifier in passages and identifier not in structured
    )
    if misplaced:
        raise CitationInWrongChannel(
            "citation placed in prose instead of evidence_references — "
            "l'agent a cité des preuves dans le texte au lieu de les porter dans "
            "`evidence_references`, où elles peuvent être vérifiées : "
            + ", ".join(misplaced)
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


def output_model_for(
    agent_role: str, *, decision_led: bool = False, concise: bool = False,
    institutional: bool = False,
) -> type[AgentOutput]:
    """Which output schema this role answers under.

    One place decides it, so the schema an agent is *given* and the schema its answer is
    *checked against* cannot drift apart — they are the same call.
    """
    if institutional and agent_role == SYNTHESIS_ROLE:
        return InstitutionalSynthesisOutput
    if concise and agent_role == SYNTHESIS_ROLE:
        return ConciseSynthesisOutput
    if decision_led and agent_role == SYNTHESIS_ROLE:
        return DecisionLedSynthesisOutput
    return SynthesisOutput if agent_role == SYNTHESIS_ROLE else AgentOutput


def agent_output_json_schema(
    agent_role: str = "", *, decision_led: bool = False, concise: bool = False,
    institutional: bool = False,
) -> dict[str, Any]:
    """The schema embedded in the bundle, so the agent is told exactly what to return."""
    model = output_model_for(
        agent_role, decision_led=decision_led, concise=concise, institutional=institutional,
    )
    return model.model_json_schema()


def output_model_from_schema(agent_role: str, schema: dict[str, Any]) -> type[AgentOutput]:
    required = schema.get("required", [])
    contract = None
    if "pps_fiche" in required:
        definition = schema.get("properties", {}).get("pps_fiche", {})
        reference = definition.get("$ref", "")
        if reference.startswith("#/$defs/"):
            definition = schema.get("$defs", {}).get(reference.removeprefix("#/$defs/"), {})
        contract = definition.get("properties", {}).get("contract", {}).get("const")
        if contract not in {"rcpia.pps-fiche.v1", "rcpia.pps-fiche.v2"}:
            raise ValueError("Contrat de fiche absent ou inconnu dans le schéma enregistré.")
    return output_model_for(
        agent_role, decision_led="pps_proposal" in required,
        concise=contract == "rcpia.pps-fiche.v1", institutional=contract == "rcpia.pps-fiche.v2",
    )


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
    if isinstance(output, DecisionLedSynthesisOutput):
        allowed = {identifier.casefold() for identifier in bundle.citable_ids()}
        used = {identifier.casefold() for identifier in output.pps_proposal.cited_ids()}
        if used - allowed:
            raise UnattestedAgreement(
                "La proposition PPS cite des identifiants absents du dossier fourni."
            )
        main = {reference.id.casefold() for reference in output.evidence_references}
        if not used <= main:
            raise UnattestedAgreement(
                "Chaque source de la proposition PPS doit figurer dans evidence_references."
            )
    opinions = {
        normalise_role(opinion.agent_role): _searchable(opinion)
        for opinion in bundle.inputs.agent_opinions
    }

    def _require_attested(role: str, quote: str, *, context: str) -> None:
        haystack = opinions.get(role)
        if haystack is None:
            raise UnattestedAgreement(f"{context} cite {role}, dont aucun avis n'a ete fourni.")
        if not _attested(quote, haystack):
            raise UnattestedAgreement(
                f"La citation attribuee a {role} dans {context.lower()} ne figure pas dans "
                f"son avis : {quote[:80]!r}"
            )

    for convergence in output.convergences:
        for item in convergence.support:
            _require_attested(item.role, item.quote, context="Une convergence")

    # A divergence's two sides are the same kind of claim a convergence makes about a single
    # speciality, doubled: each side is checkable the same way, or the disagreement itself
    # could be invented -- either by putting words in a specialist's mouth, or by making a
    # real disagreement sound sharper than either opinion actually states.
    for divergence in output.divergences:
        _require_attested(
            divergence.agent_a.role, divergence.agent_a.quote, context="Une divergence"
        )
        _require_attested(
            divergence.agent_b.role, divergence.agent_b.quote, context="Une divergence"
        )

    # Every speciality this panel actually knows about: one that answered, or one the panel
    # asked for and got nothing usable from. `silent` is checked against this, not against
    # the global role vocabulary `CoverageGap`'s own field validator uses -- a panel of two
    # convened specialities can otherwise report a third, uninvolved one as "silent", which
    # reads as a real gap in coverage when the speciality was never asked in the first place.
    panel_roles = set(opinions) | {
        normalise_role(role) for role in bundle.inputs.missing_opinions
    }

    # `raised_by` is checkable the same way; `silent` asserts an absence, which no quotation
    # from elsewhere in the opinion could ever prove, so it is checked against panel
    # membership instead of a quotation.
    for gap in output.coverage_gaps:
        for item in gap.raised_by:
            _require_attested(item.role, item.quote, context="Une lacune de couverture")
        for role in gap.silent:
            if role not in panel_roles:
                raise UnattestedAgreement(
                    f"Une lacune de couverture cite {role} comme silencieux, mais cette "
                    "specialite ne fait pas partie de ce panel."
                )


# The ways a model marks "words omitted here" when it shortens a quotation.
_ELLIPSIS = re.compile(r"\[\s*(?:\.{3,}|\u2026)\s*\]|\(\s*(?:\.{3,}|\u2026)\s*\)|\.{3,}|\u2026")

# A fragment shorter than this proves nothing: two or three common words occur everywhere in a
# clinical opinion, and allowing them would let an ellipsis stitch a claim out of scattered
# vocabulary -- which is the fabrication this check exists to refuse.
_MIN_FRAGMENT = 8


def _attested(quote: str, haystack: str) -> bool:
    """Whether this quotation really appears in that agent's opinion.

    **An ellipsis is honest quoting, not evasion.** A specialist's recommendation runs to several
    clauses, and a synthesis shortening one to its load-bearing halves -- *"proposer une
    suppression androgénique immédiate [...] associée à une intensification"* -- is quoting
    correctly. A plain substring test reads the gap as a mismatch and rejects it, and the whole
    synthesis then fails: measured, a legitimate panel produced no fiche at all because of this.

    So the fragments either side of an ellipsis are checked separately, and **in order**. That
    keeps the guarantee the check exists for: every word attributed to an agent is one it wrote,
    in the sequence it wrote them. What it stops requiring is that the agent wrote them without
    a breath in between.
    """
    fragments = [words for part in _ELLIPSIS.split(quote) if (words := _words(part))]
    if not fragments:
        return False
    if len(fragments) > 1 and any(len(f) < _MIN_FRAGMENT for f in fragments):
        # An elision made of scraps is not a quotation of anything.
        return False

    position = 0
    for fragment in fragments:
        found = haystack.find(fragment, position)
        if found == -1:
            return False
        position = found + len(fragment)
    return True


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
