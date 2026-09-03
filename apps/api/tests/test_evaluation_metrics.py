"""The gold set, the metrics, and the arms — all of it without a database.

Every number the evaluation reports is computed from a returned id list and a gold-set record,
so every definition can be pinned here. That matters more than usual: a metric that quietly
changes definition between two releases makes the two releases incomparable, and nothing about
the run would look wrong.

The three properties worth stating, because each was a decision rather than an obvious default:

- **coverage is over claims, not ids** — one claim with four acceptable passages is found once
- **unanswerable questions are excluded from coverage** — scoring their recall as 0.0 would
  punish an arm for correctly returning nothing
- **safety is over every question** — a forbidden passage is not excused by the question
  having had no answer
"""

import json
from pathlib import Path

import pytest

from rcpia.evaluation.arms import ABLATIONS, ARMS, Arm, concepts_for, filter_for
from rcpia.evaluation.goldset import (
    EvidenceGroup,
    Goldset,
    GoldsetError,
    Question,
    parse_goldset,
    verify_against_corpus,
)
from rcpia.evaluation.metrics import (
    aggregate,
    chunk_recall,
    group_recall,
    harmful_at,
    irrelevant_context,
    ndcg_at,
    precision_at,
    reciprocal_rank,
    score_question,
)


def question(
    query_id: str = "q1",
    *,
    groups: tuple[EvidenceGroup, ...] = (),
    forbidden: frozenset[str] = frozenset(),
    unanswerable: bool = False,
    cancer_type: str = "prostate",
    filter: dict | None = None,
) -> Question:
    return Question(
        query_id=query_id,
        split="pilot",
        question="Quelle prise en charge ?",
        cancer_type=cancer_type,
        concepts={"french": ("curage",), "neutral": ("cT3b",)},
        filter=filter or {},
        relevant_groups=groups,
        forbidden_chunk_ids=forbidden,
        unanswerable=unanswerable,
    )


def record(**overrides: object) -> str:
    base: dict[str, object] = {
        "query_id": "q1",
        "question": "Quelle prise en charge ?",
        "cancer_type": "prostate",
        "relevant_groups": [{"claim": "c", "acceptable_chunk_ids": ["a"]}],
    }
    base.update(overrides)
    return json.dumps(base)


# --------------------------------------------------------------------------------------
# The gold set refuses what it cannot score
# --------------------------------------------------------------------------------------


def test_a_group_with_no_acceptable_passage_is_refused() -> None:
    """It would score 0 for every arm and so distinguish none of them."""
    with pytest.raises(GoldsetError, match="no acceptable passage"):
        parse_goldset([record(relevant_groups=[{"claim": "c", "acceptable_chunk_ids": []}])])


def test_an_answerable_question_with_no_group_is_refused() -> None:
    with pytest.raises(GoldsetError, match="lists no relevant passage"):
        parse_goldset([record(relevant_groups=[])])


def test_an_unanswerable_question_may_not_list_relevant_passages() -> None:
    """It is one or the other: the point of an unanswerable question is that silence is right."""
    with pytest.raises(GoldsetError, match="unanswerable"):
        parse_goldset([record(unanswerable=True)])


def test_a_repeated_query_id_is_refused() -> None:
    with pytest.raises(GoldsetError, match="more than once"):
        parse_goldset([record(), record()])


def test_an_unknown_split_is_refused() -> None:
    with pytest.raises(GoldsetError, match="split"):
        parse_goldset([record(split="holdout")])


def test_labels_the_corpus_does_not_contain_are_refused_not_scored() -> None:
    """A gold set drifting against a re-chunked corpus scores 0.0 and looks like a retrieval
    failure. Naming the missing ids is the difference between a bug report and a wrong number.
    """
    goldset = parse_goldset([record(relevant_groups=[
        {"claim": "c", "acceptable_chunk_ids": ["gone", "here"]}
    ])])
    with pytest.raises(GoldsetError, match="gone"):
        verify_against_corpus(goldset, frozenset({"here"}))
    verify_against_corpus(goldset, frozenset({"here", "gone"}))


def test_a_split_that_does_not_exist_is_named_rather_than_returned_empty() -> None:
    goldset = parse_goldset([record()])
    with pytest.raises(GoldsetError, match="unknown split"):
        goldset.split("nope")
    assert len(goldset.split("dev")) == 0


# --------------------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------------------


def test_a_claim_with_four_acceptable_passages_is_found_once_not_one_of_four() -> None:
    """The reason group recall is the headline and chunk recall is reported beside it."""
    group = EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a", "b", "c", "d"}))
    assert group_recall((group,), ["a"]) == 1.0
    assert chunk_recall(question(groups=(group,)), ["a"]) == 0.25


def test_optional_groups_count_in_coverage_but_not_in_required_coverage() -> None:
    required = EvidenceGroup(claim="r", acceptable_chunk_ids=frozenset({"a"}))
    optional = EvidenceGroup(claim="o", acceptable_chunk_ids=frozenset({"b"}), required=False)
    q = question(groups=(required, optional))
    score = score_question(q, ["a"])
    assert score.group_recall == 0.5
    assert score.required_group_recall == 1.0


def test_precision_counts_only_what_was_returned() -> None:
    q = question(groups=(EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a", "b"})),))
    assert precision_at(q, ["a", "x", "b", "y", "z"], 5) == pytest.approx(0.4)
    # Fewer than k returned divides by what came back, not by k -- otherwise an arm that
    # returns two passages, both relevant, scores 0.4 for being brief.
    assert precision_at(q, ["a", "b"], 5) == 1.0
    assert precision_at(q, [], 5) == 0.0


def test_irrelevant_context_excludes_the_forbidden_from_the_numerator() -> None:
    """A forbidden passage is counted by the safety metric. Counting it here as well would
    make one failure look like two, in two places that read as independent.
    """
    q = question(
        groups=(EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a"})),),
        forbidden=frozenset({"bad"}),
    )
    assert irrelevant_context(q, ["a", "bad", "x", "y"]) == 0.5


def test_reciprocal_rank_and_ndcg_see_what_recall_cannot() -> None:
    """`group_recall@10` is blind to a reranker moving a passage from rank 9 to rank 1."""
    q = question(groups=(EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a"})),))
    buried = ["x"] * 8 + ["a"]
    surfaced = ["a"] + ["x"] * 8
    assert group_recall(q.relevant_groups, buried) == group_recall(q.relevant_groups, surfaced)
    assert reciprocal_rank(q, buried) < reciprocal_rank(q, surfaced)
    assert ndcg_at(q, buried) < ndcg_at(q, surfaced)
    assert ndcg_at(q, surfaced) == pytest.approx(1.0)


def test_ndcg_ideal_is_capped_at_k() -> None:
    """With twenty relevant passages and ten slots, a perfect ten scores 1.0, not 0.5."""
    q = question(groups=(EvidenceGroup(
        claim="c", acceptable_chunk_ids=frozenset(f"c{i}" for i in range(20))
    ),))
    assert ndcg_at(q, [f"c{i}" for i in range(10)], 10) == pytest.approx(1.0)


# --------------------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------------------


def test_harm_at_one_is_the_metric_that_outranks_recall() -> None:
    q = question(
        groups=(EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a"})),),
        forbidden=frozenset({"cisplatine-dose"}),
    )
    assert harmful_at(q, ["cisplatine-dose", "a"], 1) is True
    assert harmful_at(q, ["a", "cisplatine-dose"], 1) is False
    assert harmful_at(q, ["a", "cisplatine-dose"], 10) is True


def test_refusing_an_answerable_question_is_a_false_refusal() -> None:
    answerable = score_question(
        question(groups=(EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a"})),)), []
    )
    assert answerable.false_refusal is True
    assert answerable.correct_refusal is False

    silent = score_question(question("un-001", unanswerable=True), [])
    assert silent.correct_refusal is True
    assert silent.false_refusal is False


def test_answering_an_unanswerable_question_is_not_a_refusal() -> None:
    spoke = score_question(question("un-001", unanswerable=True), ["something"])
    assert spoke.refused is False
    assert spoke.correct_refusal is False


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------


def test_coverage_averages_over_answerable_questions_only() -> None:
    """An unanswerable question has no passage to find. Counting its recall as 0.0 would
    punish an arm for behaving correctly, and as 1.0 would reward silence.
    """
    found = score_question(
        question("a", groups=(EvidenceGroup(claim="c", acceptable_chunk_ids=frozenset({"a"})),)),
        ["a"],
    )
    silent = score_question(question("un-001", unanswerable=True), [])
    arm = aggregate("full", [found, silent])
    assert arm.questions == 2
    assert arm.answerable == 1
    assert arm.group_recall == 1.0
    assert arm.correct_refusal == 1.0
    assert arm.false_refusal == 0.0


def test_a_forbidden_passage_is_counted_even_for_an_unanswerable_question() -> None:
    harmful = score_question(
        question("un-001", unanswerable=True, forbidden=frozenset({"bad"})), ["bad"]
    )
    assert aggregate("full", [harmful]).harmful_at_1 == 1.0


def test_aggregating_nothing_does_not_divide_by_zero() -> None:
    assert aggregate("full", []).group_recall == 0.0


# --------------------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------------------


def test_every_arm_has_a_distinct_name() -> None:
    names = [arm.name for arm in (*ARMS, *ABLATIONS)]
    assert len(names) == len(set(names))


def test_an_unrouted_arm_still_filters_on_cancer_type() -> None:
    """A prostate question reaching a bladder document is not a routing subtlety, it is a
    different disease. `dense` measures embeddings, not whether the invariants hold.
    """
    q = question(filter={"disease_setting": "prostate_metastatic_hormone_sensitive"})
    unrouted = filter_for(q, Arm("dense", routed=False, rerank=False, legs="dense"))
    assert unrouted.cancer_type == "prostate"
    assert unrouted.disease_setting is None
    assert unrouted.clinical_purpose == ()


def test_a_routed_arm_applies_every_declared_dimension() -> None:
    q = question(filter={
        "disease_setting": "prostate_metastatic_hormone_sensitive",
        "therapy_line": "first_line",
        "clinical_purpose": ["therapy_indication", "prognosis"],
        "agent_role": "medical_oncology",
    })
    routed = filter_for(q, Arm("routed", routed=True, rerank=False))
    assert routed.disease_setting == "prostate_metastatic_hormone_sensitive"
    assert routed.therapy_line == "first_line"
    assert routed.clinical_purpose == ("therapy_indication", "prognosis")
    assert routed.agent_role == "medical_oncology"


@pytest.mark.parametrize(
    "dimension", [a.drop_dimension for a in ABLATIONS if a.drop_dimension]
)
def test_each_ablation_drops_exactly_the_dimension_it_names(dimension: str) -> None:
    """The ablations exist to price one dimension at a time. An arm that dropped two would
    attribute both losses to one name.
    """
    q = question(filter={
        "disease_setting": "prostate_metastatic_hormone_sensitive",
        "therapy_line": "first_line",
        "clinical_purpose": ["therapy_indication"],
        "agent_role": "medical_oncology",
    })
    arm = next(a for a in ABLATIONS if a.drop_dimension == dimension)
    ablated = filter_for(q, arm)
    full = filter_for(q, Arm("routed", routed=True, rerank=False))

    empty = () if dimension == "clinical_purpose" else None
    assert getattr(ablated, dimension) == empty
    for other in ("disease_setting", "therapy_line", "clinical_purpose", "agent_role"):
        if other != dimension:
            assert getattr(ablated, other) == getattr(full, other)


def test_the_relevance_floor_exists_only_where_a_reranker_ran() -> None:
    """The floor is the only stage that can say the corpus does not answer the question, and
    it needs a cross-encoder score to apply. An unreranked arm that claimed a floor would be
    reporting a refusal nothing produced.
    """
    assert [arm.name for arm in ARMS if arm.uses_floor] == ["full"]


def test_concepts_come_from_the_gold_set_and_are_therefore_an_upper_bound() -> None:
    """Nothing builds `Concepts` from a case ledger yet. Every number the harness reports is
    what retrieval can do with perfect concepts; the deployed system will be worse.
    """
    concepts = concepts_for(question())
    assert concepts.french == ("curage",)
    assert concepts.neutral == ("cT3b",)
    assert concepts.english == ()


def test_the_pilot_gold_set_parses_and_is_internally_consistent() -> None:
    """The shipped file, not a fixture. A gold set that stops parsing is a broken harness."""
    path = Path(__file__).resolve().parents[3] / "docs" / "evaluation" / "goldset-v1.jsonl"
    goldset = Goldset(questions=parse_goldset(
        path.read_text(encoding="utf-8").splitlines()
    ).questions)
    pilot = goldset.split("pilot")
    assert len(pilot) >= 12
    assert sum(1 for q in pilot if q.unanswerable) == 1
    assert all(q.corpus_release for q in pilot), "every question names the release it was cut on"
    assert all(q.cancer_type for q in pilot)
