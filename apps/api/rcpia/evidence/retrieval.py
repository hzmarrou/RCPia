"""Finding the passages an agent is allowed to see.

The pipeline is filter, then search, then fuse:

1. **Filter on the patient's structured ledger.** This is the safety mechanism. Ranking
   decides what is best among the candidates; the filter decides which candidates are
   clinically admissible at all. A ranking mistake surfaces a mediocre passage, a filter
   mistake surfaces guidance written for a different disease.
2. **Search the filtered set twice** — PostgreSQL full-text for the exact tokens embeddings
   blur, pgvector for the paraphrase full-text cannot reach.
3. **Fuse by rank, not by score.** `ts_rank_cd` is unbounded and cosine similarity is not,
   so averaging them is meaningless. Reciprocal Rank Fusion sidesteps it.
4. **Rerank, and refuse what is too weak.** A cross-encoder reads the question and the
   passage together, which neither leg does. The floor matters more than the reordering: a
   retriever that always returns its top ten cannot say the corpus does not answer the
   question, and every one of those ten arrives with a real citation and a real page.

**Two keyword queries, not one.** The corpus is 81% English and every question is French, and
no stemmer bridges that: `métastatique` and `metastatic` both reduce to `metastat`, but
`vessie` and `bladder` share nothing at all. So French concepts are searched against French
passages, English concepts against English passages, and language-neutral concepts — TNM
codes, drug names, scores — against both, because those are the tokens that survive either
stemmer unchanged.

**The query text is never assembled into SQL.** `to_tsquery` is a parser, and
`to_tsquery('french', 'Gleason 4+3')` raises `syntax error in tsquery` — on a phrase that
appears in almost every prostate question we will ask. `websearch_to_tsquery` accepts the
same string as an ordinary bound parameter and degrades rather than raising, so that is what
this module uses.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evidence.ingestion import verify_search_configurations
from rcpia.evidence.models import (
    ENGLISH_CONFIG,
    FRENCH_CONFIG,
    EvidenceChunk,
    EvidenceDocument,
)
from rcpia.evidence.rerank import Reranker

# Both legs offer this many candidates to the fusion. Wide enough that a passage ranked
# poorly by one leg can still be rescued by the other, narrow enough to stay one query.
CANDIDATE_LIMIT = 50

# Cormack et al., SIGIR 2009. The constant flattens the contribution of top ranks so that a
# passage found by both legs beats one found brilliantly by a single leg.
RRF_K = 60

UNSPECIFIED = "not_specified"

# `not_specified` is not one claim but three, and only two of them mean "applies to every
# patient". The third — `not_determinable` — means the corpus could not tell what the passage
# applies to, which is a statement about our knowledge, not about the passage's scope.
#
# Treating them alike admitted 292 prostate passages of unknown applicability to every
# patient, 74 of which name castration or metastatic therapy. A localized low-risk filter
# could return bilateral orchiectomy.
#
# `ancestry_unreliable` is excluded for the same reason, and a NULL reason with it: a passage
# whose label carries no stated justification is not evidence that it is universal.
#
# Leakage is reduced, not eliminated — `general_principle` has the highest rate of all three
# (15 of 33). That residue is a corpus finding rather than a filter bug, and it is recorded
# in `docs/reviews/corpus-v3-change-request.md`.
ADMISSIBLE_UNSPECIFIED_REASONS = ("general_principle", "spans_settings")

# Below this, a passage is discarded rather than returned. Measured against the loaded corpus
# with three probe queries: the worst genuinely relevant candidate scored 0.579, and the best
# candidate for a deliberately irrelevant question scored 0.447. This sits in that gap.
#
# Three queries on one corpus is a starting point, not an authority. The retrieval evaluation
# should revise it, which is why it is a parameter of `search` rather than a constant buried
# in the call.
RELEVANCE_FLOOR = 0.5

# `websearch_to_tsquery` reads a double quote as a phrase delimiter, so a concept containing
# one would split into two. Concepts are clinical phrases and never need internal quotes.
_QUOTES = re.compile(r'["“”]')

# Verified once per process, before the first search.
#
# Checking only at ingestion left a gap: an alteration made after the last import leaves live
# retrieval querying with rules the stored vectors were never built under, and nothing raises.
# Checking on every query would cost a catalogue round trip per search for a condition that
# changes at most once in a process's life, so it is checked once and remembered.
#
# It fails closed. A search against a configuration we do not recognise returns nothing
# useful, and returning nothing is the correct answer when we cannot trust the index.
_configuration_verified = False


async def _ensure_configuration(session: AsyncSession) -> None:
    global _configuration_verified
    if not _configuration_verified:
        await verify_search_configurations(session)
        _configuration_verified = True


_FRENCH: sa.ColumnClause[str] = sa.literal_column(f"'{FRENCH_CONFIG}'::regconfig")
_ENGLISH: sa.ColumnClause[str] = sa.literal_column(f"'{ENGLISH_CONFIG}'::regconfig")


def _quoted_or(concepts: tuple[str, ...]) -> str:
    """Compose concepts into one `websearch_to_tsquery` string.

    Each concept is quoted so it matches as a phrase. Unquoted, `Gleason 4+3` parses to the
    three lexemes `gleason`, `4` and `+3`, and the bare `4` alone matches 292 of the 1,029
    English passages — the keyword leg would return a quarter of the corpus as noise.

    The concepts are joined with OR because this stage is about recall. Precision comes from
    the reranker, which reads the passage rather than counting words.
    """
    cleaned = [_QUOTES.sub(" ", concept).strip() for concept in concepts]
    return " OR ".join(f'"{concept}"' for concept in cleaned if concept)


@dataclass(frozen=True)
class Concepts:
    """What to search for, separated by which language can express it.

    `neutral` is the vocabulary that means the same thing in both corpora — `pT2N0M0`,
    `ISUP 3`, `BCG`, `cisplatine`, drug names. Those go into both queries, and they are the
    only reason a French question reaches an English passage through the keyword leg at all.
    """

    french: tuple[str, ...] = ()
    english: tuple[str, ...] = ()
    neutral: tuple[str, ...] = ()

    @property
    def french_query(self) -> str:
        return _quoted_or(self.french + self.neutral)

    @property
    def english_query(self) -> str:
        return _quoted_or(self.english + self.neutral)

    def __bool__(self) -> bool:
        return bool(self.french_query or self.english_query)


@dataclass(frozen=True)
class Filter:
    """Which passages are clinically admissible for this patient.

    `cancer_type` is never relaxed. Everything else can be, in the order given by `widen()`.
    """

    cancer_type: str
    disease_setting: str | None = None
    therapy_line: str | None = None
    agent_role: str | None = None
    # Which `not_specified` passages count as universally applicable. A parameter rather than
    # a constant so the retrieval evaluation can measure the choice instead of inheriting it.
    admit_unspecified: tuple[str, ...] = ADMISSIBLE_UNSPECIFIED_REASONS

    def widen(self) -> "Filter | None":
        """The next-looser filter, or None when nothing further may be dropped.

        **Only the therapy line is ever relaxed.** It is the narrowest claim and the one a
        guideline is least likely to state explicitly, so a passage that omits it is usually
        still applicable.

        `disease_setting`, `cancer_type` and `agent_role` are never dropped. Dropping the
        cancer type would offer bladder guidance to a prostate patient. Dropping the disease
        setting is subtler and just as wrong: it admits passages written for *every other*
        stage — localized protocols for metastatic disease — and it re-admits every passage
        whose scope the corpus could not determine, undoing the admissibility rule above.

        Nothing is lost by holding the setting: genuinely universal passages are already
        admitted through `admit_unspecified`. A wider filter that returns something is worse
        than a strict filter that returns nothing.
        """
        if self.therapy_line is not None:
            return replace(self, therapy_line=None)
        return None

    def as_clauses(self) -> list[sa.ColumnElement[bool]]:
        clauses: list[sa.ColumnElement[bool]] = [
            EvidenceChunk.cancer_types.contains([self.cancer_type]),
            # Unconditional, and not relaxed by widening. A superseded recommendation is more
            # dangerous than an irrelevant one: it is on topic, reads as authoritative, and
            # carries a real citation, so nothing downstream would question it.
            EvidenceChunk.retrieval_eligible.is_(True),
        ]
        if self.disease_setting is not None:
            universal = sa.and_(
                EvidenceChunk.disease_setting == UNSPECIFIED,
                EvidenceChunk.not_specified_reason.in_(self.admit_unspecified),
            )
            clauses.append(
                sa.or_(EvidenceChunk.disease_setting == self.disease_setting, universal)
            )
        if self.therapy_line is not None:
            clauses.append(
                EvidenceChunk.therapy_line.in_((self.therapy_line, "not_applicable"))
            )
        if self.agent_role is not None:
            clauses.append(EvidenceChunk.agent_roles.contains([self.agent_role]))
        return clauses


@dataclass(frozen=True)
class RetrievedChunk:
    """One candidate, with enough provenance for a clinician to check the citation."""

    chunk_id: str
    document_id: str
    text: str
    citation_label: str | None
    page_start: int | None
    page_end: int | None
    recommendation_number: int | None
    # How the page was derived and how far to trust it — `source_html_block` is exact,
    # `pdf_text_alignment` is inferred.
    locator_method: str | None
    locator_confidence: float | None
    # `poc_unreviewed` until a clinician has read the passage. Carried so the page can say so.
    clinical_status: str
    # The edition the passage was cited against.
    version_label: str | None
    publisher: str | None
    disease_setting: str
    not_specified_reason: str | None
    language: str
    score: float
    keyword_rank: int | None = None
    dense_rank: int | None = None
    # The cross-encoder's judgement, or None when no reranker ran. Kept apart from `score`,
    # which is the fusion's, because the two are not comparable and the reader should be able
    # to see which one ordered the list.
    relevance: float | None = None

    @property
    def found_by_both(self) -> bool:
        return self.keyword_rank is not None and self.dense_rank is not None


@dataclass
class RetrievalReport:
    """What the search did, so an empty result can be explained rather than guessed at."""

    widened: int = 0
    keyword_hits: int = 0
    dense_hits: int = 0
    keyword_skipped: bool = False
    reranked: bool = False
    below_floor: int = 0
    filter_used: Filter | None = None
    chunks: list[RetrievedChunk] = field(default_factory=list)


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Combine rankings by position rather than by score."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


def _to_chunk(
    row: sa.Row,
    *,
    score: float,
    relevance: float | None,
    keyword_rank: int | None,
    dense_rank: int | None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        text=row.text,
        citation_label=row.citation_label,
        page_start=row.page_start,
        page_end=row.page_end,
        recommendation_number=row.recommendation_number,
        locator_method=row.locator_method,
        locator_confidence=row.locator_confidence,
        clinical_status=row.clinical_status,
        version_label=row.version_label,
        publisher=row.publisher,
        disease_setting=row.disease_setting,
        not_specified_reason=row.not_specified_reason,
        language=row.language,
        score=score,
        keyword_rank=keyword_rank,
        dense_rank=dense_rank,
        relevance=relevance,
    )


# Everything a clinician needs to check a citation, and everything a reader needs to judge
# how much weight it carries. `clinical_status` is the one that must never be dropped: every
# passage in the corpus is `poc_unreviewed`, and a passage nobody has reviewed must not reach
# a page looking like one that has been.
_COLUMNS = (
    EvidenceChunk.chunk_id,
    EvidenceChunk.document_id,
    EvidenceChunk.text,
    EvidenceChunk.citation_label,
    EvidenceChunk.page_start,
    EvidenceChunk.page_end,
    EvidenceChunk.recommendation_number,
    EvidenceChunk.locator_method,
    EvidenceChunk.locator_confidence,
    EvidenceChunk.clinical_status,
    EvidenceChunk.disease_setting,
    EvidenceChunk.not_specified_reason,
    EvidenceChunk.language,
    # From the document, so an opinion stays checkable after the guideline is superseded.
    EvidenceDocument.version_label,
    EvidenceDocument.publisher,
)


def _base_select() -> sa.Select[Any]:
    """The candidate query, joined to its document for the edition it was cited against."""
    return sa.select(*_COLUMNS).join(
        EvidenceDocument, EvidenceChunk.document_id == EvidenceDocument.document_id
    )


async def _keyword_leg(
    session: AsyncSession, *, concepts: Concepts, clauses: list[sa.ColumnElement[bool]]
) -> list[sa.Row]:
    """Full-text search, each language against its own query.

    The configuration is a SQL literal and the query text is a bound parameter — never the
    other way round.
    """
    french = sa.func.websearch_to_tsquery(_FRENCH, concepts.french_query)
    english = sa.func.websearch_to_tsquery(_ENGLISH, concepts.english_query)
    is_french = EvidenceChunk.language == "fr"
    vector = EvidenceChunk.search_vector

    matches = sa.or_(
        sa.and_(is_french, vector.op("@@")(french)),
        sa.and_(sa.not_(is_french), vector.op("@@")(english)),
    )
    rank = sa.case(
        (is_french, sa.func.ts_rank_cd(vector, french)),
        else_=sa.func.ts_rank_cd(vector, english),
    )

    statement = (
        _base_select()
        .where(sa.and_(*clauses, matches))
        .order_by(rank.desc())
        .limit(CANDIDATE_LIMIT)
    )
    return list((await session.execute(statement)).all())


async def _dense_leg(
    session: AsyncSession,
    *,
    embedding: list[float],
    embedding_model: str,
    clauses: list[sa.ColumnElement[bool]],
) -> list[sa.Row]:
    """Vector search by cosine distance, restricted to one embedding model.

    **The model restriction is not an optimisation.** Vectors from different models are not
    comparable, and the failure is silent: `text-embedding-ada-002` and
    `text-embedding-3-large` both produce 1,536 numbers, so a query embedded with the wrong
    one fits the column, ranks the corpus confidently, and returns nonsense. Matching on
    `embedding_model` makes that impossible rather than unlikely — a misconfigured deployment
    retrieves nothing, which is visible, instead of retrieving badly, which is not.

    Passages with no embedding are excluded rather than sorted last: pgvector orders NULL
    distances at the end of an ascending sort, so an unembedded corpus would return its first
    fifty rows and look like a working search.
    """
    distance = EvidenceChunk.embedding.cosine_distance(embedding)
    statement = (
        _base_select()
        .where(
            sa.and_(
                *clauses,
                EvidenceChunk.embedding.is_not(None),
                EvidenceChunk.embedding_model == embedding_model,
            )
        )
        .order_by(distance)
        .limit(CANDIDATE_LIMIT)
    )
    return list((await session.execute(statement)).all())


async def _search(
    session: AsyncSession,
    *,
    question: str,
    concepts: Concepts,
    embedding: list[float] | None,
    embedding_model: str,
    filter: Filter,
    limit: int,
    reranker: Reranker | None,
    floor: float,
) -> RetrievalReport:
    """Retrieve the passages an agent may see for one clinical question.

    Widens the filter when the strict one yields nothing usable — whether because it admitted
    no passages, or because every passage it admitted fell below the relevance floor.

    A widened search is recorded in the report rather than blended in silently: a passage
    admitted only because the therapy line was relaxed is a weaker match than one that
    satisfied the full filter, and whoever reads the result is entitled to know which
    happened.

    """
    await _ensure_configuration(session)

    report = RetrievalReport(keyword_skipped=not concepts)
    current: Filter | None = filter

    while current is not None:
        clauses = current.as_clauses()

        # An empty concept set produces an empty tsquery, which matches nothing. Running it
        # anyway would report zero keyword hits, which reads as "the corpus has nothing"
        # rather than "we did not ask".
        keyword = [] if report.keyword_skipped else await _keyword_leg(
            session, concepts=concepts, clauses=clauses
        )
        dense = (
            []
            if embedding is None
            else await _dense_leg(
                session,
                embedding=embedding,
                embedding_model=embedding_model,
                clauses=clauses,
            )
        )

        if keyword or dense:
            attempted = current
            report.keyword_hits = len(keyword)
            report.dense_hits = len(dense)
            report.filter_used = attempted

            by_id = {row.chunk_id: row for row in (*dense, *keyword)}
            keyword_order = {row.chunk_id: i for i, row in enumerate(keyword, start=1)}
            dense_order = {row.chunk_id: i for i, row in enumerate(dense, start=1)}

            fused = reciprocal_rank_fusion([list(keyword_order), list(dense_order)])

            if reranker is None:
                report.chunks = [
                    _to_chunk(
                        by_id[chunk_id],
                        score=score,
                        relevance=None,
                        keyword_rank=keyword_order.get(chunk_id),
                        dense_rank=dense_order.get(chunk_id),
                    )
                    for chunk_id, score in fused[:limit]
                ]
                return report

            # The whole fused set goes to the cross-encoder, not just the head of it: the
            # point of reranking is that fusion's order is not the final word.
            candidates = fused[:CANDIDATE_LIMIT]
            relevances = await reranker.score(
                question, [by_id[chunk_id].text for chunk_id, _ in candidates]
            )
            report.reranked = True

            scored = [
                (chunk_id, score, relevance)
                for (chunk_id, score), relevance in zip(candidates, relevances, strict=True)
            ]
            report.below_floor = sum(1 for _, _, relevance in scored if relevance < floor)
            kept = sorted(
                (item for item in scored if item[2] >= floor), key=lambda item: -item[2]
            )
            if kept:
                report.chunks = [
                    _to_chunk(
                        by_id[chunk_id],
                        score=score,
                        relevance=relevance,
                        keyword_rank=keyword_order.get(chunk_id),
                        dense_rank=dense_order.get(chunk_id),
                    )
                    for chunk_id, score, relevance in kept[:limit]
                ]
                return report
            # Everything the filter admitted was judged irrelevant. That can mean the corpus
            # has nothing to say — or that the filter was a shade too tight, and the passage
            # that answers the question sits one therapy-line relaxation away. Relaxing costs
            # one more round trip; not relaxing returns nothing when an answer existed.

        current = current.widen()
        if current is not None:
            report.widened += 1

    # Nothing survived even the widest admissible filter. Returning an empty report is the
    # correct answer: the alternative is offering a passage the filter refused.
    report.filter_used = None
    return report


async def search(
    session: AsyncSession,
    *,
    question: str,
    concepts: Concepts,
    embedding: list[float] | None,
    embedding_model: str,
    filter: Filter,
    reranker: Reranker,
    limit: int = 10,
    floor: float = RELEVANCE_FLOOR,
) -> RetrievalReport:
    """Retrieve the passages an agent may see for one clinical question.

    **The reranker is required, and that is the point.** The relevance floor is the only
    stage that can say the corpus does not answer the question, and it exists only where a
    cross-encoder has scored the candidates. When the reranker was optional, a caller who
    omitted it received the fused candidates unfiltered — ten passages with real citations
    and real page numbers, and no signal that the safety stage had been skipped. Making it a
    keyword argument without a default means that mistake cannot be made silently.

    Retrieval without reranking is a legitimate thing to want, for an ablation or an offline
    experiment. It has its own name: `search_without_reranking`.
    """
    return await _search(
        session,
        question=question,
        concepts=concepts,
        embedding=embedding,
        embedding_model=embedding_model,
        filter=filter,
        limit=limit,
        reranker=reranker,
        floor=floor,
    )


async def search_without_reranking(
    session: AsyncSession,
    *,
    question: str,
    concepts: Concepts,
    embedding: list[float] | None,
    embedding_model: str,
    filter: Filter,
    limit: int = 10,
) -> RetrievalReport:
    """Filter, search and fuse, with no cross-encoder and no relevance floor.

    **Not for clinician-facing use.** Every fused candidate is returned however weak, because
    nothing has judged whether any of them answer the question. This exists so the retrieval
    evaluation can measure what reranking actually contributes, and so an ablation does not
    have to reach for a `None` that the production path deliberately forbids.

    `report.reranked` is False on everything it returns, so a result from here can always be
    told apart from a result that passed the floor.
    """
    return await _search(
        session,
        question=question,
        concepts=concepts,
        embedding=embedding,
        embedding_model=embedding_model,
        filter=filter,
        limit=limit,
        reranker=None,
        floor=RELEVANCE_FLOOR,
    )
