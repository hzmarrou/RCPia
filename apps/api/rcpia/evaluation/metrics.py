"""What a retrieval run is worth, defined as pure functions.

No database, no network, no model. Every number here is computable from a returned id list and
a gold-set record, which is what makes them unit-testable and what makes a disagreement about a
score a disagreement about the definition rather than about the run.

**Recall is not the headline, and that is deliberate.** This tool drafts clinical documents. A
system that finds the right passage nine times in ten and puts a contraindicated drug at rank 1
on the tenth is worse than one that finds it eight times and never does. `harmful@1` and
`false_refusal` outrank coverage, and are reported first.

**Coverage is measured over groups, not ids.** A claim supported by any one of several
equivalent passages is found once, not one-of-four. Recall over ids is reported beside it and
answers a different question — how much of the labelled evidence was gathered, which matters for
an agent that will cite from what it receives.
"""

import math
from dataclasses import dataclass

from rcpia.evaluation.goldset import EvidenceGroup, Question


def group_recall(groups: tuple[EvidenceGroup, ...], returned: list[str]) -> float:
    """Fraction of claims satisfied by at least one returned passage."""
    if not groups:
        return 0.0
    got = set(returned)
    return sum(1 for g in groups if g.acceptable_chunk_ids & got) / len(groups)


def chunk_recall(question: Question, returned: list[str]) -> float:
    relevant = question.relevant_ids
    if not relevant:
        return 0.0
    return len(relevant & set(returned)) / len(relevant)


def precision_at(question: Question, returned: list[str], k: int) -> float:
    """Fraction of the first k returned passages that are labelled relevant."""
    head = returned[:k]
    if not head:
        return 0.0
    return len(question.relevant_ids & set(head)) / len(head)


def irrelevant_context(question: Question, returned: list[str]) -> float:
    """Fraction of returned passages in no group and not forbidden.

    Every returned passage enters the agent's context. Ten passages of which two are relevant
    is a real cost: it dilutes the prompt and offers eight plausible things to cite.
    """
    if not returned:
        return 0.0
    accounted = question.relevant_ids | question.forbidden_chunk_ids
    return sum(1 for chunk_id in returned if chunk_id not in accounted) / len(returned)


def reciprocal_rank(question: Question, returned: list[str]) -> float:
    relevant = question.relevant_ids
    for position, chunk_id in enumerate(returned, start=1):
        if chunk_id in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at(question: Question, returned: list[str], k: int = 10) -> float:
    """Binary-gain nDCG.

    Included because the others cannot see it: `group_recall@10` is blind to a reranker moving
    a passage from rank 9 to rank 1, and reciprocal rank only looks at the first hit. Graded
    relevance is deliberately out of scope -- grading 0-3 is a clinician cost, not a property
    the metric needs.
    """
    relevant = question.relevant_ids
    if not relevant:
        return 0.0
    gains = [1.0 if chunk_id in relevant else 0.0 for chunk_id in returned[:k]]
    dcg = sum(gain / math.log2(position + 1) for position, gain in enumerate(gains, start=1))
    ideal_hits = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0


def harmful_at(question: Question, returned: list[str], k: int) -> bool:
    """Whether a forbidden passage appears in the first k returned."""
    return bool(question.forbidden_chunk_ids & set(returned[:k]))


def source_family_cover(returned_families: list[str], expected: set[str]) -> float:
    if not expected:
        return 1.0
    return len(set(returned_families) & expected) / len(expected)


@dataclass(frozen=True)
class QuestionScore:
    """Every metric for one question under one arm."""

    query_id: str
    returned: int
    group_recall: float
    required_group_recall: float
    chunk_recall: float
    precision_at_5: float
    precision_at_10: float
    irrelevant_context: float
    reciprocal_rank: float
    ndcg_at_10: float
    harmful_at_1: bool
    harmful_at_k: bool
    refused: bool
    unanswerable: bool

    @property
    def false_refusal(self) -> bool:
        return self.refused and not self.unanswerable

    @property
    def correct_refusal(self) -> bool:
        return self.refused and self.unanswerable


def score_question(question: Question, returned: list[str]) -> QuestionScore:
    return QuestionScore(
        query_id=question.query_id,
        returned=len(returned),
        group_recall=group_recall(question.relevant_groups, returned),
        required_group_recall=group_recall(question.required_groups, returned),
        chunk_recall=chunk_recall(question, returned),
        precision_at_5=precision_at(question, returned, 5),
        precision_at_10=precision_at(question, returned, 10),
        irrelevant_context=irrelevant_context(question, returned),
        reciprocal_rank=reciprocal_rank(question, returned),
        ndcg_at_10=ndcg_at(question, returned, 10),
        harmful_at_1=harmful_at(question, returned, 1),
        harmful_at_k=harmful_at(question, returned, len(returned) or 1),
        refused=not returned,
        unanswerable=question.unanswerable,
    )


@dataclass(frozen=True)
class ArmScore:
    """One arm, aggregated over the questions it was run on.

    Coverage metrics are averaged over **answerable** questions only. An unanswerable question
    has no passage to find, so counting its recall as 0.0 would punish an arm for behaving
    correctly, and counting it as 1.0 would reward silence.
    """

    arm: str
    questions: int
    answerable: int
    unanswerable: int
    group_recall: float
    required_group_recall: float
    chunk_recall: float
    precision_at_5: float
    precision_at_10: float
    irrelevant_context: float
    mrr: float
    ndcg_at_10: float
    harmful_at_1: float
    harmful_at_k: float
    false_refusal: float
    correct_refusal: float


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(arm: str, scores: list[QuestionScore]) -> ArmScore:
    answerable = [s for s in scores if not s.unanswerable]
    unanswerable = [s for s in scores if s.unanswerable]
    return ArmScore(
        arm=arm,
        questions=len(scores),
        answerable=len(answerable),
        unanswerable=len(unanswerable),
        group_recall=_mean([s.group_recall for s in answerable]),
        required_group_recall=_mean([s.required_group_recall for s in answerable]),
        chunk_recall=_mean([s.chunk_recall for s in answerable]),
        precision_at_5=_mean([s.precision_at_5 for s in answerable]),
        precision_at_10=_mean([s.precision_at_10 for s in answerable]),
        irrelevant_context=_mean([s.irrelevant_context for s in answerable]),
        mrr=_mean([s.reciprocal_rank for s in answerable]),
        ndcg_at_10=_mean([s.ndcg_at_10 for s in answerable]),
        # Safety is over every question. A forbidden passage returned for an unanswerable
        # question is not excused by the question having no answer.
        harmful_at_1=_mean([1.0 if s.harmful_at_1 else 0.0 for s in scores]),
        harmful_at_k=_mean([1.0 if s.harmful_at_k else 0.0 for s in scores]),
        false_refusal=_mean([1.0 if s.false_refusal else 0.0 for s in answerable]),
        correct_refusal=_mean([1.0 if s.correct_refusal else 0.0 for s in unanswerable]),
    )
