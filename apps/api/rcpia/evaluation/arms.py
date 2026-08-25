"""The configurations a question is asked under, and what each one is for.

Every arm answers a question the others cannot. `dense` and `fts` ask whether either leg is
carrying the search alone; `fused` asks whether combining them is worth the complexity;
`routed` prices the routing filter; `full` is what production does.

**If `fused` does not beat both `dense` and `fts`, half the pipeline should be deleted.** That
is a real possible outcome, and the harness is built to be able to report it.

**Three constraints stay on in every arm.** They are invariants of the system rather than
routing choices, and switching them off would measure a configuration nobody would ship:

    retrieval_eligible = true
    cancer_type matches
    the document is not superseded

918 of 3,532 passages in corpus v5.1 are ineligible -- bibliographies, panel rosters, edition
changelogs. An unfiltered dense arm that dragged those in would score badly for a reason that
has nothing to do with embeddings, and would make the routing filter look better than it is.
The comparison has to isolate the routing dimensions.
"""

from dataclasses import dataclass, replace

from rcpia.evaluation.goldset import Question
from rcpia.evidence.retrieval import Concepts, Filter

# Routing dimensions, as opposed to the invariants above. Dropping one at a time is how each is
# priced individually: what it gains in coverage, and what it prevents in harm.
ROUTING_DIMENSIONS = ("disease_setting", "therapy_line", "clinical_purpose", "agent_role")


@dataclass(frozen=True)
class Arm:
    """One configuration, named for what it establishes."""

    name: str
    routed: bool
    rerank: bool
    legs: str = "both"  # "dense", "keyword", or "both"
    drop_dimension: str | None = None
    # Whether a risk-band question also admits its risk-unspecified parent. An admission
    # option rather than a routing dimension, so it is named rather than folded into
    # `drop_dimension`.
    admit_parent: bool = True
    note: str = ""

    @property
    def uses_floor(self) -> bool:
        """The relevance floor exists only where a cross-encoder has scored candidates."""
        return self.rerank


ARMS: tuple[Arm, ...] = (
    Arm("dense", routed=False, rerank=False, legs="dense",
        note="Is the embedding doing the work?"),
    Arm("fts", routed=False, rerank=False, legs="keyword",
        note="Is keyword doing the work?"),
    Arm("fused", routed=False, rerank=False,
        note="Does hybrid beat both legs? If not, delete half the pipeline."),
    Arm("routed", routed=True, rerank=False,
        note="What the routing filter costs in coverage and buys in safety."),
    Arm("full", routed=True, rerank=True,
        note="Production: routing, reranking and the relevance floor."),
)

ABLATIONS: tuple[Arm, ...] = (
    *(
        Arm(f"routed−{dimension}", routed=True, rerank=False, drop_dimension=dimension,
            note=f"What {dimension} is worth on its own.")
        for dimension in ROUTING_DIMENSIONS
    ),
    # Not a dimension: an admission option, priced the same way. A risk-band question that
    # cannot see guidance written across the bands reaches 14 of 332 non-metastatic prostate
    # passages, which is what this arm exists to show rather than assert.
    Arm("routed−parent_setting", routed=True, rerank=False, admit_parent=False,
        note="What admitting the risk-unspecified parent is worth."),
)


def concepts_for(question: Question) -> Concepts:
    """The hand-written concepts from the gold set.

    **This is what makes every number an upper bound.** Nothing in the system builds `Concepts`
    from a case ledger yet, so these were written by a person. A run with perfect concepts says
    how well retrieval *can* do; the deployed system will be worse by an unknown margin, and
    the gap is the query builder's score once it exists.
    """
    return Concepts(
        french=question.concepts.get("french", ()),
        english=question.concepts.get("english", ()),
        neutral=question.concepts.get("neutral", ()),
    )


def filter_for(question: Question, arm: Arm) -> Filter:
    """The filter this arm asks under.

    An unrouted arm keeps the cancer type, because that is an invariant: a prostate question
    reaching a bladder document is not a routing subtlety, it is a different disease.
    """
    invariants = Filter(cancer_type=question.cancer_type, admit_parent_setting=arm.admit_parent)
    if not arm.routed:
        return invariants

    declared = question.filter
    setting = declared.get("disease_setting")
    line = declared.get("therapy_line")
    raw_purposes = declared.get("clinical_purpose")
    purposes: tuple[str, ...] = (
        tuple(str(p) for p in raw_purposes) if isinstance(raw_purposes, list | tuple) else ()
    )
    role = declared.get("agent_role")

    # Written out per dimension rather than through a mapping, so the types stay checkable:
    # three of the four are `str | None` and the fourth is a tuple.
    if arm.drop_dimension == "disease_setting":
        setting = None
    elif arm.drop_dimension == "therapy_line":
        line = None
    elif arm.drop_dimension == "clinical_purpose":
        purposes = ()
    elif arm.drop_dimension == "agent_role":
        role = None

    return replace(
        invariants,
        admit_parent_setting=arm.admit_parent,
        disease_setting=str(setting) if setting else None,
        therapy_line=str(line) if line else None,
        clinical_purpose=purposes,
        agent_role=str(role) if role else None,
    )
