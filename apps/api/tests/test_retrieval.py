"""Retrieving guideline passages.

Two kinds of property are under test here, and they fail differently.

**Filtering is a safety property.** A prostate question that returns bladder guidance is not
a poor result, it is a wrong one, and nothing downstream can detect it — the passage arrives
with a real citation and a real page number. Those tests assert what must *not* come back.

**Tokenization is a correctness property that only real strings expose.** `Gleason 4+3` is
not one token, `métastatique` and `metastatique` must be one, and `BCG-unresponsive` and
`BCG réfractaire` share only the acronym. Every one of those was measured against the corpus
rather than assumed, and each is pinned here so a configuration change cannot quietly undo
it.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evidence.ingestion import CorpusError
from rcpia.evidence.models import (
    EMBEDDING_DIMENSIONS,
    ENGLISH_CONFIG,
    FRENCH_CONFIG,
    EvidenceChunk,
    EvidenceDocument,
)
from rcpia.evidence.rerank import Reranker
from rcpia.evidence.retrieval import (
    Concepts,
    Filter,
    filter_admits,
    reciprocal_rank_fusion,
    search,
    search_without_reranking,
)

MODEL = "text-embedding-3-large"


def vector(index: int) -> list[float]:
    """A one-hot vector, so cosine distance is controllable.

    Constant vectors would all be parallel and every distance would be zero, which would make
    the dense leg's ordering arbitrary and the tests meaningless.
    """
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[index % EMBEDDING_DIMENSIONS] = 1.0
    return values


async def add_document(session: AsyncSession, document_id: str) -> None:
    session.add(
        EvidenceDocument(
            document_id=document_id, title=document_id, clinical_status="poc_unreviewed"
        )
    )
    await session.flush()


async def add_chunk(
    session: AsyncSession,
    chunk_id: str,
    *,
    document_id: str = "doc",
    text: str = "Passage de test.",
    cancer_types: tuple[str, ...] = ("prostate",),
    disease_setting: str = "prostate_localized_low_risk",
    therapy_line: str = "primary",
    agent_roles: tuple[str, ...] = ("urology_oncology",),
    # `therapy_indication` is a value the live corpus actually uses. The fixture used to say
    # `treatment_selection`, which no release has ever produced — so a filter test written
    # against it would have passed while matching nothing in production.
    clinical_purpose: tuple[str, ...] = ("therapy_indication",),
    language: str = "fr",
    not_specified_reason: str | None = None,
    retrieval_eligible: bool = True,
    retrieval_exclusion_reason: str | None = None,
    embedding_index: int | None = None,
    embedding_model: str = MODEL,
) -> None:
    session.add(
        EvidenceChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            text=text,
            contextual_text=text,
            section_path=["Section"],
            cancer_types=list(cancer_types),
            disease_setting=disease_setting,
            not_specified_reason=not_specified_reason,
            retrieval_eligible=retrieval_eligible,
            retrieval_exclusion_reason=retrieval_exclusion_reason,
            therapy_line=therapy_line,
            clinical_purpose=list(clinical_purpose),
            agent_roles=list(agent_roles),
            language=language,
            clinical_status="poc_unreviewed",
            keyword_text=text,
            recommendation_number=12,
            page_start=42,
            page_end=42,
            locator_method="pdf_text_alignment",
            locator_confidence=0.9,
            content_sha256=chunk_id.ljust(64, "0")[:64],
            embedding=None if embedding_index is None else vector(embedding_index),
            embedding_model=None if embedding_index is None else embedding_model,
        )
    )
    await session.flush()


async def lexemes(session: AsyncSession, config: str, text: str) -> set[str]:
    """The lexemes PostgreSQL actually produces, as a set of bare tokens."""
    rendered = await session.scalar(
        sa.select(
            sa.func.to_tsvector(
                sa.literal_column(f"'{config}'::regconfig"), text
            ).cast(sa.Text)
        )
    )
    assert rendered is not None
    return {piece.split(":")[0].strip("'") for piece in rendered.split() if piece}


# --------------------------------------------------------------------------------------
# Tokenization — pinned against what the corpus actually contains
# --------------------------------------------------------------------------------------


async def test_clinical_identifiers_survive_both_stemmers(session: AsyncSession) -> None:
    """TNM codes, scores and drug names are the vocabulary keyword search exists for.

    They are also the only route a French question has to an English passage through the
    keyword leg, so they must stem identically under both configurations.
    """
    for term in ("pT2N0M0", "pembrolizumab", "BCG", "ISUP", "cT3b"):
        french = await lexemes(session, FRENCH_CONFIG, term)
        english = await lexemes(session, ENGLISH_CONFIG, term)
        assert french == english, f"{term} stems differently per language"


async def test_a_gleason_score_is_not_one_token(session: AsyncSession) -> None:
    """`Gleason 4+3` parses to three lexemes, which is why concepts must be quoted.

    Unquoted and OR-joined, the bare `4` matches roughly a quarter of the English corpus.
    """
    assert await lexemes(session, ENGLISH_CONFIG, "Gleason 4+3") == {"gleason", "4", "+3"}


async def test_accents_are_folded_within_french(session: AsyncSession) -> None:
    """Not a cross-language convenience. French clinical text is written both ways, and
    without folding the two spellings do not match each other."""
    accented = await lexemes(session, FRENCH_CONFIG, "métastatique")
    plain = await lexemes(session, FRENCH_CONFIG, "metastatique")

    assert accented == plain == {"metastat"}


async def test_the_two_spellings_of_bcg_failure_share_only_the_acronym(
    session: AsyncSession,
) -> None:
    """`BCG-unresponsive` and `BCG réfractaire` overlap on `bcg` and nothing else — which is
    exactly why the bilingual terminology map is needed and stemming is not enough."""
    english = await lexemes(session, ENGLISH_CONFIG, "BCG-unresponsive")
    french = await lexemes(session, FRENCH_CONFIG, "BCG réfractaire")

    assert english & french == {"bcg"}


# --------------------------------------------------------------------------------------
# Query construction — the text is never assembled into SQL
# --------------------------------------------------------------------------------------


def test_concepts_are_quoted_and_or_joined() -> None:
    concepts = Concepts(french=("cystectomie totale",), neutral=("Gleason 4+3",))

    assert concepts.french_query == '"cystectomie totale" OR "Gleason 4+3"'


def test_a_concept_carrying_a_quote_cannot_split_the_phrase() -> None:
    """A stray quote would end the phrase early and turn the remainder into separate terms."""
    assert Concepts(french=('cancer "de" la prostate',)).french_query == (
        '"cancer  de  la prostate"'
    )


def test_empty_concepts_are_falsy_rather_than_an_empty_query() -> None:
    """An empty tsquery matches nothing, which reads as "the corpus has nothing" rather than
    "we did not ask". The caller must be able to tell those apart."""
    assert not Concepts()
    assert Concepts(french=("  ",)).french_query == ""


async def test_hostile_query_text_does_not_raise(session: AsyncSession) -> None:
    """`to_tsquery('french', 'Gleason 4+3')` raises a syntax error on a phrase that appears in
    nearly every prostate question. `websearch_to_tsquery` degrades instead."""
    for text in ('"unbalanced', "a & b | c ! ( d", "", "OR OR OR"):
        parsed = await session.scalar(
            sa.select(
                sa.func.websearch_to_tsquery(
                    sa.literal_column(f"'{FRENCH_CONFIG}'::regconfig"), text
                ).cast(sa.Text)
            )
        )
        assert parsed is not None


# --------------------------------------------------------------------------------------
# Filtering — what must not come back
# --------------------------------------------------------------------------------------


async def test_a_prostate_question_returns_no_bladder_passage(session: AsyncSession) -> None:
    """The plan's acceptance criterion, and the reason the filter runs before the ranking."""
    await add_document(session, "doc")
    await add_chunk(session, "prostate-1", text="surveillance active prostate")
    await add_chunk(
        session,
        "bladder-1",
        text="surveillance active vessie",
        cancer_types=("bladder",),
        disease_setting="bladder_nmibc_low_risk",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("surveillance active",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["prostate-1"]


async def test_a_bladder_question_returns_no_prostate_passage(session: AsyncSession) -> None:
    await add_document(session, "doc")
    await add_chunk(session, "prostate-1", text="chimiotherapie prostate")
    await add_chunk(
        session,
        "bladder-1",
        text="chimiotherapie vessie",
        cancer_types=("bladder",),
        disease_setting="bladder_mibc_non_metastatic",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("chimiotherapie",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("bladder"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["bladder-1"]


async def test_a_metastatic_question_returns_no_localized_protocol(
    session: AsyncSession,
) -> None:
    """The distinction the filter exists to enforce. A localized protocol offered for
    metastatic disease arrives with a real citation and looks entirely plausible."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "localized",
        text="traitement radical",
        disease_setting="prostate_localized_low_risk",
    )
    await add_chunk(
        session,
        "metastatic",
        text="traitement radical",
        disease_setting="prostate_metastatic_hormone_sensitive",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("traitement radical",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", disease_setting="prostate_metastatic_hormone_sensitive"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["metastatic"]


async def test_passages_marked_for_no_setting_are_admitted(session: AsyncSession) -> None:
    """Definitions and general principles apply to every setting. Excluding them would drop
    the general statement that frames the specific one."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "general",
        text="definition du cancer",
        disease_setting="not_specified",
        not_specified_reason="general_principle",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("definition du cancer",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", disease_setting="prostate_localized_low_risk"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["general"]


# --------------------------------------------------------------------------------------
# Widening — and what it must never drop
# --------------------------------------------------------------------------------------


def test_widening_relaxes_the_therapy_line_and_nothing_else() -> None:
    """Dropping the disease setting admits passages written for every other stage, and
    re-admits every passage whose scope the corpus could not determine."""
    strict = Filter(
        "prostate",
        disease_setting="prostate_localized_low_risk",
        therapy_line="salvage",
        agent_role="urology_oncology",
    )

    once = strict.widen()
    assert once == Filter(
        "prostate",
        disease_setting="prostate_localized_low_risk",
        therapy_line=None,
        agent_role="urology_oncology",
    )

    # And there is nowhere further to go.
    assert once is not None and once.widen() is None


async def test_widening_finds_a_passage_the_strict_filter_refused(
    session: AsyncSession,
) -> None:
    await add_document(session, "doc")
    await add_chunk(session, "primary-only", text="curage ganglionnaire", therapy_line="primary")

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("curage ganglionnaire",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
    )

    assert report.widened == 1
    assert [chunk.chunk_id for chunk in report.chunks] == ["primary-only"]
    # Recorded rather than blended in: a passage admitted only because the therapy line was
    # dropped is a weaker match, and the reader is entitled to know.
    assert report.filter_used == Filter("prostate", disease_setting="prostate_localized_low_risk")


async def test_widening_never_crosses_the_cancer_type(session: AsyncSession) -> None:
    """Returning nothing is the correct answer. A wider filter that returns something is
    worse than a strict filter that returns nothing."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "bladder-1",
        text="cystectomie totale",
        cancer_types=("bladder",),
        disease_setting="bladder_mibc_non_metastatic",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("cystectomie totale",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="primary"
        ),
    )

    assert report.chunks == []
    assert report.filter_used is None


async def test_widening_never_drops_the_agent_role(session: AsyncSession) -> None:
    """A geriatric question must not be answered with urology evidence because no geriatric
    passage exists. That is exactly the gap the corpus currently has."""
    await add_document(session, "doc")
    await add_chunk(session, "urology-1", text="evaluation du patient")

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("evaluation du patient",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate",
            disease_setting="prostate_localized_low_risk",
            therapy_line="primary",
            agent_role="geriatric_frailty",
        ),
    )

    assert report.chunks == []
    assert report.widened == 1


# --------------------------------------------------------------------------------------
# The two legs
# --------------------------------------------------------------------------------------


async def test_a_query_embedded_with_another_model_retrieves_nothing(
    session: AsyncSession,
) -> None:
    """`text-embedding-ada-002` and `text-embedding-3-large` both produce 1,536 numbers, so a
    mismatched query fits the column and ranks the corpus confidently. Retrieving nothing is
    visible; retrieving badly is not."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", embedding_index=7, embedding_model=MODEL)

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(),
        embedding=vector(7),
        embedding_model="text-embedding-ada-002",
        filter=Filter("prostate"),
    )

    assert report.dense_hits == 0
    assert report.chunks == []


async def test_the_dense_leg_finds_a_passage_sharing_no_words(session: AsyncSession) -> None:
    """The reason both legs exist: keyword search cannot reach a paraphrase."""
    await add_document(session, "doc")
    await add_chunk(session, "paraphrase", text="aucun mot en commun", embedding_index=11)

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("cystectomie",)),
        embedding=vector(11),
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["paraphrase"]
    assert report.keyword_hits == 0


async def test_an_absent_concept_set_is_recorded_rather_than_reported_as_no_hits(
    session: AsyncSession,
) -> None:
    """Zero keyword hits reads as "the corpus has nothing". Not asking is a different fact."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", embedding_index=3)

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(),
        embedding=vector(3),
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert report.keyword_skipped is True
    assert report.keyword_hits == 0
    assert [chunk.chunk_id for chunk in report.chunks] == ["chunk-1"]


async def test_a_passage_without_an_embedding_is_excluded_not_sorted_last(
    session: AsyncSession,
) -> None:
    """pgvector orders NULL distances at the end of an ascending sort, so an unembedded corpus
    would return its first rows and look like a working search."""
    await add_document(session, "doc")
    await add_chunk(session, "unembedded", embedding_index=None)

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(),
        embedding=vector(5),
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert report.dense_hits == 0


# --------------------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------------------


def test_a_passage_found_by_both_legs_beats_one_found_brilliantly_by_one() -> None:
    """The point of fusing by rank. `ts_rank_cd` is unbounded and cosine similarity is not,
    so the scores cannot be compared — only the positions can."""
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])

    assert fused[0][0] == "b"


def test_fusion_keeps_a_passage_only_one_leg_found() -> None:
    """Neither leg is authoritative: keyword misses paraphrase, vectors miss rare tokens."""
    fused = dict(reciprocal_rank_fusion([["a"], ["b"]]))

    assert set(fused) == {"a", "b"}


async def test_both_legs_are_reported_separately(session: AsyncSession) -> None:
    """So an empty result can be explained rather than guessed at."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="cystectomie totale", embedding_index=2)

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("cystectomie totale",)),
        embedding=vector(2),
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert (report.keyword_hits, report.dense_hits) == (1, 1)
    assert report.chunks[0].found_by_both is True


# --------------------------------------------------------------------------------------
# Reranking, and the floor
# --------------------------------------------------------------------------------------


class FakeReranker(Reranker):
    """Scores passages from a lookup, so the floor can be tested without a network call."""

    def __init__(self, scores: dict[str, float], default: float = 0.9) -> None:
        self._scores = scores
        self._default = default
        self.calls: list[tuple[str, int]] = []
        # What the cross-encoder was actually given to read, so a test can assert it was
        # the contextualised text rather than the bare passage.
        self.documents: list[str] = []

    @property
    def model(self) -> str:
        return "fake-rerank"

    @property
    def fingerprint(self) -> str:
        return "fake-rerank:test"

    async def score(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, len(documents)))
        self.documents = list(documents)
        return [self._scores.get(document, self._default) for document in documents]


async def test_a_passage_below_the_floor_is_discarded(session: AsyncSession) -> None:
    """The reason this stage exists, more than the reordering. Without a floor the retriever
    has no way to say the corpus does not answer the question -- it returns its top ten
    regardless, each with a real citation and a real page number."""
    await add_document(session, "doc")
    await add_chunk(session, "relevant", text="pertinent")
    await add_chunk(session, "irrelevant", text="hors sujet")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("pertinent", "hors sujet")),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=FakeReranker({"pertinent": 0.82, "hors sujet": 0.11}),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["relevant"]
    assert report.below_floor == 1
    assert report.reranked is True


async def test_everything_below_the_floor_returns_nothing(session: AsyncSession) -> None:
    """Measured behaviour: a deliberately irrelevant question still passed the filter and
    returned 25 prostate candidates, every one of which the cross-encoder scored under 0.45.
    The filter cannot tell; the floor can."""
    await add_document(session, "doc")
    await add_chunk(session, "weak-1", text="premier")
    await add_chunk(session, "weak-2", text="second")

    report = await search(
        session,
        question="question sans rapport",
        concepts=Concepts(french=("premier", "second")),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=FakeReranker({}, default=0.2),
    )

    assert report.chunks == []
    assert report.below_floor == 2


async def test_a_weak_strict_result_relaxes_the_therapy_line(session: AsyncSession) -> None:
    """The review's scenario. A below-floor strict candidate alongside a relevant passage one
    therapy-line relaxation away returned nothing and never widened -- the answer existed and
    the filter was a shade too tight."""
    await add_document(session, "doc")
    await add_chunk(session, "weak-strict", text="passage de rattrapage", therapy_line="salvage")
    await add_chunk(session, "relevant-relaxed", text="passage initial", therapy_line="primary")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("passage de rattrapage", "passage initial")),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
        reranker=FakeReranker(
            {"passage de rattrapage": 0.1, "passage initial": 0.92}
        ),
    )

    assert report.widened == 1
    assert [chunk.chunk_id for chunk in report.chunks] == ["relevant-relaxed"]


async def test_nothing_relevant_anywhere_still_returns_nothing(
    session: AsyncSession,
) -> None:
    """Relaxing the therapy line is a second chance, not a lowering of the bar."""
    await add_document(session, "doc")
    await add_chunk(session, "weak", text="peu pertinent", therapy_line="primary")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("peu pertinent",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
        reranker=FakeReranker({}, default=0.1),
    )

    assert report.chunks == []


async def test_the_reranker_reorders_what_fusion_ranked(session: AsyncSession) -> None:
    """Otherwise the cross-encoder is an expensive filter and nothing more."""
    await add_document(session, "doc")
    await add_chunk(session, "fused-first", text="alpha", embedding_index=1)
    await add_chunk(session, "fused-second", text="beta")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("alpha", "beta")),
        embedding=vector(1),
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=FakeReranker({"alpha": 0.6, "beta": 0.95}),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["fused-second", "fused-first"]
    assert report.chunks[0].relevance == 0.95


async def test_the_fusion_score_survives_reranking(session: AsyncSession) -> None:
    """Two different judgements, kept apart. The reader should be able to see which one
    ordered the list, and they are not on comparable scales."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="alpha")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("alpha",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=FakeReranker({"alpha": 0.77}),
    )

    chunk = report.chunks[0]
    assert chunk.relevance == 0.77
    assert 0 < chunk.score < 0.1  # the RRF score, on its own scale


async def test_without_a_reranker_nothing_is_refused_and_the_report_says_so(
    session: AsyncSession,
) -> None:
    """Correct for a test or an offline experiment, wrong for a clinician-facing answer."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="alpha")

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("alpha",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert report.reranked is False
    assert report.below_floor == 0
    assert len(report.chunks) == 1


async def test_the_reranker_sees_the_question_not_the_concepts(
    session: AsyncSession,
) -> None:
    """A cross-encoder reads the question and the passage together. Handing it a bag of
    quoted keywords would throw away the sentence it is meant to reason about."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="alpha")
    reranker = FakeReranker({"alpha": 0.9})

    await search(
        session,
        question="Quelle est la conduite a tenir ?",
        concepts=Concepts(french=("alpha",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=reranker,
    )

    assert reranker.calls == [("Quelle est la conduite a tenir ?", 1)]


# --------------------------------------------------------------------------------------
# `not_specified` is three different claims
# --------------------------------------------------------------------------------------


async def test_a_passage_of_undeterminable_scope_is_not_treated_as_universal(
    session: AsyncSession,
) -> None:
    """"We could not tell what this applies to" and "this applies to everyone" are different
    claims. Conflating them admitted 292 prostate passages of unknown scope to every patient,
    74 of which name castration or metastatic therapy -- so a localized low-risk filter could
    return bilateral orchiectomy."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "undeterminable",
        text="orchidectomie bilaterale",
        disease_setting="not_specified",
        not_specified_reason="not_determinable",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("orchidectomie bilaterale",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", disease_setting="prostate_localized_low_risk"),
    )

    assert report.chunks == []


@pytest.mark.parametrize("reason", ["general_principle", "spans_settings"])
async def test_a_genuinely_universal_passage_is_still_admitted(
    session: AsyncSession, reason: str
) -> None:
    """Excluding these would drop the general statement that frames the specific one, and
    over-filtering is invisible."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "universal",
        text="definition du cancer",
        disease_setting="not_specified",
        not_specified_reason=reason,
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("definition du cancer",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", disease_setting="prostate_localized_low_risk"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["universal"]


async def test_an_unspecified_passage_with_no_stated_reason_is_not_admitted(
    session: AsyncSession,
) -> None:
    """A label carrying no justification is not evidence that the passage is universal."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "unexplained",
        text="texte sans raison",
        disease_setting="not_specified",
        not_specified_reason=None,
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("texte sans raison",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", disease_setting="prostate_localized_low_risk"),
    )

    assert report.chunks == []


# --------------------------------------------------------------------------------------
# Provenance, and the safety stage that cannot be skipped by accident
# --------------------------------------------------------------------------------------


async def test_a_result_says_whether_a_clinician_has_reviewed_it(
    session: AsyncSession,
) -> None:
    """Every passage in the corpus is `poc_unreviewed`. If retrieval drops that, nothing
    downstream can say so, and an unreviewed passage reaches the page looking like one that
    has been reviewed."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="alpha")

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("alpha",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert report.chunks[0].clinical_status == "poc_unreviewed"


async def test_a_result_carries_the_edition_it_was_cited_against(
    session: AsyncSession,
) -> None:
    """An opinion cited against AFU 2024-2026 has to stay checkable after 2027 lands, and the
    edition lives on the document rather than the passage."""
    session.add(
        EvidenceDocument(
            document_id="versioned",
            title="AFU Prostate",
            clinical_status="poc_unreviewed",
            version_label="2024-2026",
            publisher="AFU",
        )
    )
    await session.flush()
    await add_chunk(session, "chunk-1", document_id="versioned", text="alpha")

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("alpha",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert report.chunks[0].version_label == "2024-2026"
    assert report.chunks[0].publisher == "AFU"


async def test_the_locator_says_how_far_to_trust_the_page(session: AsyncSession) -> None:
    """`source_html_block` is exact and `pdf_text_alignment` is inferred. A reader sent to the
    wrong page loses confidence in every citation, not just that one."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="alpha")

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("alpha",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    found = report.chunks[0]
    assert found.recommendation_number == 12
    assert found.locator_method == "pdf_text_alignment"
    assert found.page_start == 42


def test_the_production_search_cannot_be_called_without_a_reranker() -> None:
    """The floor is the only stage that can say the corpus does not answer the question. When
    the reranker was optional, omitting it returned ten unfiltered passages with real
    citations and no signal that the safety stage had been skipped."""
    import inspect

    parameter = inspect.signature(search).parameters["reranker"]

    assert parameter.default is inspect.Parameter.empty


async def test_a_superseded_passage_is_never_retrieved(session: AsyncSession) -> None:
    """Corpus v3.1 blocks the treatment recommendations of the SIOG 2010 prostate paper while
    keeping its assessment guidance, a split a clinician confirmed.

    A superseded recommendation is more dangerous than an irrelevant one: it is on topic, it
    reads as authoritative, and it carries a real citation and page number, so nothing
    downstream has any reason to question it."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "superseded",
        text="hormonotherapie selon les recommandations de 2010",
        retrieval_eligible=False,
        retrieval_exclusion_reason="historical_treatment_guidance_superseded",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("hormonotherapie selon les recommandations de 2010",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert report.chunks == []


async def test_widening_cannot_reach_a_superseded_passage(session: AsyncSession) -> None:
    """Relaxation loosens which patient a passage suits. It must never reach a passage that
    suits no patient at all."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "superseded",
        text="protocole retire",
        therapy_line="primary",
        retrieval_eligible=False,
        retrieval_exclusion_reason="historical_treatment_guidance_superseded",
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("protocole retire",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
    )

    assert report.chunks == []
    assert report.widened == 1


async def test_a_search_refuses_an_altered_configuration(session: AsyncSession) -> None:
    """Verified per search, not once per process.

    "Once per process" is not fail-closed: alter the configuration after the first request
    and every later search runs against stale vectors, unchecked, for as long as the process
    lives. The catalogue query costs about 3.6ms, against an embedding call and a rerank call
    of several hundred milliseconds each.
    """
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="alpha")
    await session.execute(
        sa.text(
            "ALTER TEXT SEARCH CONFIGURATION public.rcpia_fr "
            "ALTER MAPPING FOR asciiword WITH simple"
        )
    )

    with pytest.raises(CorpusError, match="has been altered"):
        await search_without_reranking(
            session,
            question="question de test",
            concepts=Concepts(french=("alpha",)),
            embedding=None,
            embedding_model=MODEL,
            filter=Filter("prostate"),
        )


# --------------------------------------------------------------------------------------
# Clinical purpose — what the passage is *for*
# --------------------------------------------------------------------------------------


async def test_a_passage_serving_another_purpose_is_refused(session: AsyncSession) -> None:
    """The gap this filter closes: `clinical_purpose` was required of every release, given
    its own index, and read by nothing. A follow-up question could be answered with a
    screening passage and no stage of the pipeline would notice."""
    await add_document(session, "doc")
    await add_chunk(
        session, "screening", text="protocole de suivi", clinical_purpose=("screening",)
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("protocole de suivi",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", clinical_purpose=("screening",)),
    )
    assert [chunk.chunk_id for chunk in report.chunks] == ["screening"]

    strict = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("protocole de suivi",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", clinical_purpose=("follow_up",)),
        trace=True,
    )

    # Refused, and not rescued. Widening once relaxed the purpose, which admitted every
    # purpose at once — so a follow-up question that found nothing could be answered with a
    # treatment-selection passage. That is not a weaker match to the same question, it is an
    # answer to a different one.
    assert strict.attempts[0].keyword_ids == []
    assert strict.widened == 0
    assert strict.chunks == []


async def test_serving_any_requested_purpose_is_enough(session: AsyncSession) -> None:
    """Overlap, not containment. 477 chunks in the live corpus carry more than one purpose,
    and requiring a passage to serve every purpose asked about would admit almost nothing."""
    await add_document(session, "doc")
    await add_chunk(
        session,
        "both",
        text="protocole de suivi",
        clinical_purpose=("follow_up", "safety"),
    )

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("protocole de suivi",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", clinical_purpose=("follow_up", "staging")),
        trace=True,
    )

    # Strictly, without widening. Asserting only on `chunks` would not distinguish overlap
    # from containment: containment refuses the passage, widening drops the purpose clause,
    # and the same chunk comes back by the wrong route.
    assert report.widened == 0
    assert report.attempts[0].keyword_ids == ["both"]


async def test_general_passages_are_admitted_alongside_the_requested_purpose(
    session: AsyncSession,
) -> None:
    """243 eligible passages carry `general` and nothing else. Excluding them would drop a
    fifth of the corpus from every purpose-filtered question."""
    await add_document(session, "doc")
    await add_chunk(session, "general", text="protocole de suivi", clinical_purpose=("general",))

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("protocole de suivi",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", clinical_purpose=("follow_up",)),
        trace=True,
    )

    # Strictly. `general` must be admitted by the filter itself, not recovered by widening.
    assert report.widened == 0
    assert report.attempts[0].keyword_ids == ["general"]


async def test_general_can_be_excluded_for_the_evaluation(session: AsyncSession) -> None:
    """`general` is the same shape of hazard as `general_principle` — a label meaning
    "applies broadly" that a mislabelled passage can hide behind. Sweeping it must be
    possible without editing the clause."""
    await add_document(session, "doc")
    await add_chunk(session, "general", text="protocole de suivi", clinical_purpose=("general",))

    report = await search_without_reranking(
        session,
        question="question de test",
        concepts=Concepts(french=("protocole de suivi",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", clinical_purpose=("follow_up",), admit_universal_purpose=False),
        trace=True,
    )

    assert report.attempts[0].keyword_ids == []
    assert report.chunks == []


async def test_only_the_therapy_line_is_ever_relaxed(session: AsyncSession) -> None:
    """`clinical_purpose` was briefly relaxed and is not any more.

    Dropping it admitted every purpose at once. It also made the purpose filter untestable
    from outside: an `overlap`-versus-`contains` mutation survived because widening rescued
    the passage by the wrong route, and the test could not tell the two apart.
    """
    strict = Filter(
        "prostate",
        disease_setting="prostate_localized_low_risk",
        therapy_line="salvage",
        clinical_purpose=("follow_up",),
        agent_role="urology_oncology",
    )

    once = strict.widen()
    assert once is not None
    assert once.therapy_line is None

    # And there is nowhere further to go. Everything that says *who the passage is for* is
    # held: the patient, the setting, what the question asks, and which specialist is asking.
    assert once.widen() is None
    assert once.clinical_purpose == ("follow_up",)
    assert once.disease_setting == "prostate_localized_low_risk"
    assert once.cancer_type == "prostate"
    assert once.agent_role == "urology_oncology"


async def test_filter_loss_is_measured_by_predicate_not_by_rank(
    session: AsyncSession,
) -> None:
    """**Filter loss is a property of the predicate, not of a ranked list.**

    The first attempt ran both legs with the routing clauses removed and recorded what came
    back — but both legs cut at `CANDIDATE_LIMIT`, so "unfiltered" meant the top fifty of a
    wider pool. A labelled passage could fall below fifty unfiltered while sitting inside the
    filtered fifty, giving retention above 100% and conflating ranking with filtering.

    The gold set names the passages, so the filter is asked about those directly.
    """
    await add_document(session, "doc")
    await add_chunk(session, "wanted", clinical_purpose=("follow_up",))
    await add_chunk(session, "wrong-purpose", clinical_purpose=("screening",))
    await add_chunk(
        session,
        "ineligible",
        clinical_purpose=("follow_up",),
        retrieval_eligible=False,
        retrieval_exclusion_reason="bibliography_entry",
    )
    await add_chunk(session, "other-cancer", cancer_types=("bladder",))

    decision = await filter_admits(
        session,
        filter=Filter("prostate", clinical_purpose=("follow_up",)),
        chunk_ids=["wanted", "wrong-purpose", "ineligible", "other-cancer"],
    )

    # Routing loss: `wrong-purpose` is the only passage the router turned away.
    assert decision.admitted == {"wanted"}
    # The denominator excludes what the invariants refuse, so an ineligible bibliography entry
    # and another cancer's guidance are not counted against the router.
    assert decision.invariant == {"wanted", "wrong-purpose"}


async def test_asking_the_filter_about_nothing_costs_nothing(
    session: AsyncSession,
) -> None:
    decision = await filter_admits(
        session, filter=Filter("prostate"), chunk_ids=[]
    )
    assert (decision.admitted, decision.invariant) == (frozenset(), frozenset())



async def test_the_reranker_reads_the_contextualised_text(session: AsyncSession) -> None:
    """It was given the bare passage while the dense leg embedded `contextual_text`.

    The prefix carries the source and the section path, and the section path is where a
    guideline states who a passage is about — "Adjuvant treatment in pN1 disease" against
    "The role of imaging in PSA-only recurrence". Asked to tell those apart from a
    recommendation sentence naming neither, the cross-encoder was working blind at exactly
    the stage meant to catch disease-setting confusion.
    """
    contextual = (
        "Source: EAU 2026. Section: 6.3.5.f Adjuvant treatment in pN1 disease."
        "\n\noption therapeutique"
    )
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="option therapeutique")
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "chunk-1")
        .values(contextual_text=contextual)
    )
    await session.flush()

    reranker = FakeReranker({})
    await search(
        session,
        question="question de test",
        concepts=Concepts(french=("option therapeutique",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=reranker,
    )

    assert reranker.documents == [contextual]


# --------------------------------------------------------------------------------------
# The trace, and what the scalar fields describe
# --------------------------------------------------------------------------------------


async def test_the_report_describes_the_attempt_that_returned_the_passages(
    session: AsyncSession,
) -> None:
    """The scalars once described the *last* attempt, which is a different thing whenever
    the search widened: `keyword_hits` counted a search whose results had been thrown away,
    and `filter_used` named a filter the returned passages had not been selected by."""
    await add_document(session, "doc")
    # Admitted by the strict filter, and nothing the cross-encoder will keep.
    await add_chunk(session, "weak", text="curage ganglionnaire", therapy_line="salvage")
    # Reached only once the therapy line is dropped.
    await add_chunk(session, "strong", text="curage ganglionnaire pelvien", therapy_line="primary")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("curage ganglionnaire", "curage ganglionnaire pelvien")),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
        reranker=FakeReranker({"curage ganglionnaire": 0.1}, default=0.95),
        trace=True,
    )

    assert report.widened == 1
    assert [chunk.chunk_id for chunk in report.chunks] == ["strong"]
    # The widened attempt saw two passages and kept one; the strict attempt saw one and kept
    # none. The report must describe the former.
    assert report.filter_used == Filter("prostate", disease_setting="prostate_localized_low_risk")
    assert report.keyword_hits == 2
    assert report.below_floor == 1


async def test_the_trace_records_every_attempt_in_chunk_ids(session: AsyncSession) -> None:
    """Counts cannot separate the four stages the evaluation measures. `keyword_hits = 25`
    does not say whether the passage a clinician labelled relevant was one of the 25."""
    await add_document(session, "doc")
    await add_chunk(session, "weak", text="curage ganglionnaire", therapy_line="salvage")
    await add_chunk(session, "strong", text="curage ganglionnaire pelvien", therapy_line="primary")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("curage ganglionnaire", "curage ganglionnaire pelvien")),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
        reranker=FakeReranker({"curage ganglionnaire": 0.1}, default=0.95),
        trace=True,
    )

    assert len(report.attempts) == 2
    strict, widened = report.attempts

    assert strict.widening_step == 0
    assert strict.widened_from is None
    assert strict.filter_requested.therapy_line == "salvage"
    assert strict.keyword_ids == ["weak"]
    assert strict.above_floor == []
    assert strict.below_floor_ids == ["weak"]
    assert strict.returned == []

    assert widened.widening_step == 1
    assert widened.widened_from is not None
    assert widened.widened_from.therapy_line == "salvage"
    assert widened.filter_requested.therapy_line is None
    assert set(widened.keyword_ids) == {"weak", "strong"}
    assert widened.returned == ["strong"]
    assert widened.relevances["strong"] == 0.95


async def test_the_trace_is_empty_unless_asked_for(session: AsyncSession) -> None:
    """The production path allocates none of these lists."""
    await add_document(session, "doc")
    await add_chunk(session, "chunk-1", text="curage ganglionnaire")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("curage ganglionnaire",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate"),
        reranker=FakeReranker({}),
    )

    assert report.chunks != []
    assert report.attempts == []


async def test_an_empty_result_is_explained_by_the_widest_attempt(
    session: AsyncSession,
) -> None:
    """`below_floor` is what separates "the filter admitted nothing" from "it admitted
    passages and none of them answered". A reader of an empty report needs that."""
    await add_document(session, "doc")
    await add_chunk(session, "weak", text="curage ganglionnaire", therapy_line="primary")

    report = await search(
        session,
        question="question de test",
        concepts=Concepts(french=("curage ganglionnaire",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter(
            "prostate", disease_setting="prostate_localized_low_risk", therapy_line="salvage"
        ),
        reranker=FakeReranker({}, default=0.1),
    )

    assert report.chunks == []
    assert report.filter_used is None
    assert (report.keyword_hits, report.below_floor) == (1, 1)
