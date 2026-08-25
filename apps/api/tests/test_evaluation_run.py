"""Driving the arms against a real database, and attributing every miss to a stage.

**The attribution is the part worth testing.** A recall number tells someone that a third of
the claims were missed; it does not tell them whether to fix the routing rules, the concept
list, the reranker or the floor. Those are four different afternoons, and an attribution that
names the wrong one costs the whole afternoon before it is noticed.

So each test here loses a passage at exactly one stage and asserts the harness says so.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evaluation.arms import ARMS, Arm
from rcpia.evaluation.goldset import EvidenceGroup, Goldset, Question
from rcpia.evaluation.run import (
    STAGE_CANDIDATES,
    STAGE_FILTER,
    STAGE_FLOOR,
    STAGE_RANK,
    arms_for,
    embed_questions,
    format_report,
    present_chunk_ids,
    report_as_dict,
    run_arm,
    run_evaluation,
    run_question,
)
from rcpia.evidence.models import EMBEDDING_DIMENSIONS, EvidenceChunk, EvidenceDocument
from rcpia.evidence.rerank import Reranker

MODEL = "text-embedding-3-large"
SETTING = "prostate_metastatic_hormone_sensitive"


def vector(index: int) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[index % EMBEDDING_DIMENSIONS] = 1.0
    return values


class FakeReranker(Reranker):
    """Scores from a substring lookup, so the floor can be exercised without a network call."""

    def __init__(self, scores: dict[str, float], default: float = 0.9) -> None:
        self._scores = scores
        self._default = default

    @property
    def model(self) -> str:
        return "fake-rerank"

    @property
    def fingerprint(self) -> str:
        return "fake-rerank:test"

    async def score(self, query: str, documents: list[str]) -> list[float]:
        return [
            next(
                (score for needle, score in self._scores.items() if needle in document),
                self._default,
            )
            for document in documents
        ]


class FakeEmbedder:
    """Returns one vector per text, in order."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.texts: list[str] = []

    @property
    def model(self) -> str:
        return MODEL

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return self._vectors[: len(texts)]


async def add_chunk(
    session: AsyncSession,
    chunk_id: str,
    *,
    text: str = "Le curage ganglionnaire etendu est recommande.",
    disease_setting: str = SETTING,
    clinical_purpose: tuple[str, ...] = ("therapy_indication",),
    agent_roles: tuple[str, ...] = ("urology_oncology",),
    cancer_types: tuple[str, ...] = ("prostate",),
    therapy_line: str = "first_line",
    embedding_index: int | None = 1,
) -> None:
    session.add(
        EvidenceChunk(
            chunk_id=chunk_id,
            document_id="doc",
            text=text,
            contextual_text=text,
            section_path=["Section"],
            cancer_types=list(cancer_types),
            disease_setting=disease_setting,
            not_specified_reason=None,
            retrieval_eligible=True,
            retrieval_exclusion_reason=None,
            therapy_line=therapy_line,
            clinical_purpose=list(clinical_purpose),
            agent_roles=list(agent_roles),
            language="fr",
            clinical_status="poc_unreviewed",
            keyword_text=text,
            recommendation_number=1,
            page_start=10,
            page_end=10,
            locator_method="pdf_text_alignment",
            locator_confidence=0.9,
            content_sha256=chunk_id.ljust(64, "0")[:64],
            embedding=None if embedding_index is None else vector(embedding_index),
            embedding_model=None if embedding_index is None else MODEL,
        )
    )
    await session.flush()


async def add_document(session: AsyncSession) -> None:
    session.add(
        EvidenceDocument(document_id="doc", title="doc", clinical_status="poc_unreviewed")
    )
    await session.flush()


def question(
    *,
    groups: tuple[EvidenceGroup, ...],
    filter: dict | None = None,
    query_id: str = "pr-001",
    unanswerable: bool = False,
) -> Question:
    return Question(
        query_id=query_id,
        split="pilot",
        question="Quel traitement pour ce patient ?",
        cancer_type="prostate",
        concepts={"french": ("curage ganglionnaire",)},
        filter=filter if filter is not None else {"disease_setting": SETTING},
        relevant_groups=groups,
        unanswerable=unanswerable,
        corpus_release="v5.1",
    )


def group(*chunk_ids: str, claim: str = "the claim") -> EvidenceGroup:
    return EvidenceGroup(claim=claim, acceptable_chunk_ids=frozenset(chunk_ids))


ROUTED = next(arm for arm in ARMS if arm.name == "routed")
FULL = next(arm for arm in ARMS if arm.name == "full")


# --------------------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------------------


async def test_a_reachable_passage_is_returned_and_nothing_is_attributed(
    session: AsyncSession,
) -> None:
    await add_document(session)
    await add_chunk(session, "alpha")

    run = await run_question(
        session,
        question=question(groups=(group("alpha"),)),
        arm=ROUTED,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert run.returned == ("alpha",)
    assert run.losses == ()
    assert run.score.group_recall == 1.0


async def test_a_claim_is_satisfied_by_any_one_of_its_acceptable_passages(
    session: AsyncSession,
) -> None:
    """Three of four never come back, and the claim is still met -- so no loss is attributed."""
    await add_document(session)
    await add_chunk(session, "alpha")

    run = await run_question(
        session,
        question=question(groups=(group("alpha", "beta", "gamma", "delta"),)),
        arm=ROUTED,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert run.losses == ()
    assert run.score.group_recall == 1.0
    assert run.score.chunk_recall == 0.25


# --------------------------------------------------------------------------------------
# Attribution -- one stage per test
# --------------------------------------------------------------------------------------


async def test_a_passage_the_filter_refused_is_attributed_to_the_dimension_that_refused_it(
    session: AsyncSession,
) -> None:
    """The whole reason the ablations exist. `refused by disease_setting` is a routing-rule
    repair; `admitted, but neither leg surfaced it` is a concepts-or-embedding repair. Sending
    someone to the wrong one costs the afternoon.
    """
    await add_document(session)
    await add_chunk(session, "wrong-setting", disease_setting="prostate_localized_low_risk")

    run = await run_question(
        session,
        question=question(groups=(group("wrong-setting"),)),
        arm=ROUTED,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert run.returned == ()
    assert [(loss.chunk_id, loss.stage) for loss in run.losses] == [
        ("wrong-setting", STAGE_FILTER)
    ]
    assert "disease_setting" in run.losses[0].detail


async def test_a_passage_neither_leg_surfaced_is_not_blamed_on_the_filter(
    session: AsyncSession,
) -> None:
    """It is admissible and it has no vector and no matching term. That is a retrieval gap,
    and the filter had nothing to do with it.
    """
    await add_document(session)
    await add_chunk(session, "alpha")
    await add_chunk(
        session, "unreachable", text="Sujet entierement different.", embedding_index=None
    )

    run = await run_question(
        session,
        question=question(groups=(group("alpha"), group("unreachable", claim="second"))),
        arm=ROUTED,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert [(loss.chunk_id, loss.stage) for loss in run.losses] == [
        ("unreachable", STAGE_CANDIDATES)
    ]


async def test_a_candidate_left_outside_the_returned_list_is_attributed_to_ranking(
    session: AsyncSession,
) -> None:
    await add_document(session)
    await add_chunk(session, "alpha")
    await add_chunk(session, "beta")

    run = await run_question(
        session,
        question=question(groups=(group("alpha"), group("beta", claim="second"))),
        arm=ROUTED,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
        limit=1,
    )
    assert len(run.returned) == 1
    assert [loss.stage for loss in run.losses] == [STAGE_RANK]


async def test_a_passage_the_floor_discarded_is_attributed_to_the_floor_with_its_score(
    session: AsyncSession,
) -> None:
    """Only a reranked arm can lose a passage here, and the detail carries the relevance so a
    reader can see whether the floor is set wrong or the passage really is weak.
    """
    await add_document(session)
    await add_chunk(session, "alpha")
    await add_chunk(session, "weak", text="Le curage ganglionnaire, mention marginale.")

    run = await run_question(
        session,
        question=question(groups=(group("alpha"), group("weak", claim="second"))),
        arm=FULL,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({"marginale": 0.05}),
    )
    assert "weak" not in run.returned
    assert [(loss.chunk_id, loss.stage) for loss in run.losses] == [("weak", STAGE_FLOOR)]
    assert "0.05" in run.losses[0].detail


async def test_the_best_placed_passage_in_a_group_explains_the_loss(
    session: AsyncSession,
) -> None:
    """One acceptable passage reached the candidate set and two were refused by the filter.
    Blaming routing would be wrong: relaxing the filter would not have helped, because a
    candidate was already there and the ranking still did not return it.
    """
    await add_document(session)
    await add_chunk(session, "filler-1")
    await add_chunk(session, "filler-2")
    await add_chunk(session, "candidate")
    await add_chunk(session, "refused-a", disease_setting="prostate_localized_low_risk")
    await add_chunk(session, "refused-b", disease_setting="prostate_localized_low_risk")

    run = await run_question(
        session,
        question=question(groups=(group("refused-a", "refused-b", "candidate"),)),
        arm=ROUTED,
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
        limit=2,
    )
    assert "candidate" not in run.returned
    assert [(loss.chunk_id, loss.stage) for loss in run.losses] == [("candidate", STAGE_RANK)]


# --------------------------------------------------------------------------------------
# The single-leg arms are configuration, not a private code path
# --------------------------------------------------------------------------------------


async def test_the_dense_arm_asks_no_keyword_question(session: AsyncSession) -> None:
    await add_document(session)
    await add_chunk(session, "alpha")

    run = await run_question(
        session,
        question=question(groups=(group("alpha"),)),
        arm=Arm("dense", routed=False, rerank=False, legs="dense"),
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert run.keyword_hits == 0
    assert run.dense_hits == 1
    assert run.returned == ("alpha",)


async def test_the_keyword_arm_asks_no_dense_question(session: AsyncSession) -> None:
    await add_document(session)
    await add_chunk(session, "alpha")

    run = await run_question(
        session,
        question=question(groups=(group("alpha"),)),
        arm=Arm("fts", routed=False, rerank=False, legs="keyword"),
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert run.dense_hits == 0
    assert run.keyword_hits == 1


async def test_an_unrouted_arm_still_refuses_the_other_cancer(session: AsyncSession) -> None:
    """The invariants hold in every arm. A bladder passage reaching a prostate question is not
    a routing subtlety the ablations are meant to price.
    """
    await add_document(session)
    await add_chunk(
        session,
        "bladder",
        cancer_types=("bladder",),
        disease_setting="bladder_mibc",
    )

    run = await run_question(
        session,
        question=question(groups=(group("bladder"),), filter={}),
        arm=Arm("fused", routed=False, rerank=False),
        embedding=vector(1),
        embedding_model=MODEL,
        reranker=FakeReranker({}),
    )
    assert run.returned == ()
    assert run.losses[0].stage == STAGE_FILTER
    assert "invariant" in run.losses[0].detail


# --------------------------------------------------------------------------------------
# The run as a whole
# --------------------------------------------------------------------------------------


async def test_a_question_is_embedded_once_and_reused_across_arms() -> None:
    """Embedding per arm would bill nine times for one text and, worse, would let two arms be
    compared across two different vectors.
    """
    embedder = FakeEmbedder([vector(1), vector(2)])
    questions = (
        question(groups=(group("alpha"),), query_id="a"),
        question(groups=(group("beta"),), query_id="b"),
    )
    embeddings = await embed_questions(embedder, questions)
    assert set(embeddings) == {"a", "b"}
    assert len(embedder.texts) == 2


async def test_the_evaluation_refuses_a_gold_set_the_corpus_cannot_support(
    session: AsyncSession,
) -> None:
    from rcpia.evaluation.goldset import GoldsetError

    await add_document(session)
    await add_chunk(session, "alpha")
    goldset = Goldset(questions=(question(groups=(group("vanished"),)),))

    with pytest.raises(GoldsetError, match="vanished"):
        await run_evaluation(
            session,
            goldset=goldset,
            split="pilot",
            arms=(ROUTED,),
            embedder=FakeEmbedder([vector(1)]),
            reranker=FakeReranker({}),
        )


async def test_a_full_run_scores_every_arm_and_renders(session: AsyncSession) -> None:
    await add_document(session)
    await add_chunk(session, "alpha")

    report = await run_evaluation(
        session,
        goldset=Goldset(questions=(question(groups=(group("alpha"),)),)),
        split="pilot",
        arms=ARMS,
        embedder=FakeEmbedder([vector(1)]),
        reranker=FakeReranker({}),
    )
    assert [result.arm.name for result in report.arms] == [arm.name for arm in ARMS]
    assert report.corpus_release == "v5.1"

    rendered = format_report(report)
    for arm in ARMS:
        assert f"| {arm.name} |" in rendered
    # Safety before coverage, deliberately.
    assert rendered.index("harm@1") < rendered.index("group rec")

    payload = report_as_dict(report)
    assert len(payload["arms"]) == len(ARMS)
    assert payload["arms"][0]["runs"][0]["query_id"] == "pr-001"


async def test_an_unanswerable_question_scores_as_a_correct_refusal(
    session: AsyncSession,
) -> None:
    """Nothing in the corpus answers it, the floor says so, and that is the right behaviour --
    it must not be scored as recall 0.0.
    """
    await add_document(session)
    await add_chunk(session, "alpha", text="Sujet sans rapport.")

    result = await run_arm(
        session,
        arm=FULL,
        questions=(question(groups=(), query_id="un-001", unanswerable=True),),
        embeddings={"un-001": vector(9)},
        embedding_model=MODEL,
        reranker=FakeReranker({}, default=0.05),
    )
    assert result.score.correct_refusal == 1.0
    assert result.score.answerable == 0
    assert result.score.group_recall == 0.0


async def test_present_chunk_ids_reports_what_is_loaded(session: AsyncSession) -> None:
    await add_document(session)
    await add_chunk(session, "alpha")
    assert await present_chunk_ids(session) == frozenset({"alpha"})


def test_an_unknown_arm_name_is_named_rather_than_silently_skipped() -> None:
    with pytest.raises(SystemExit, match="nonsense"):
        arms_for(["nonsense"], ablations=False)
    assert [arm.name for arm in arms_for(["fused"], ablations=False)] == ["fused"]
    assert len(arms_for(None, ablations=True)) > len(arms_for(None, ablations=False))
