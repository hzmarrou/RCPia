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

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evidence.models import (
    EMBEDDING_DIMENSIONS,
    ENGLISH_CONFIG,
    FRENCH_CONFIG,
    EvidenceChunk,
    EvidenceDocument,
)
from rcpia.evidence.retrieval import (
    Concepts,
    Filter,
    reciprocal_rank_fusion,
    search,
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
    language: str = "fr",
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
            therapy_line=therapy_line,
            clinical_purpose=["treatment_selection"],
            agent_roles=list(agent_roles),
            language=language,
            clinical_status="poc_unreviewed",
            keyword_text=text,
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

    report = await search(
        session,
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

    report = await search(
        session,
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

    report = await search(
        session,
        concepts=Concepts(french=("traitement radical",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", "prostate_metastatic_hormone_sensitive"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["metastatic"]


async def test_passages_marked_for_no_setting_are_admitted(session: AsyncSession) -> None:
    """Definitions and general principles apply to every setting. Excluding them would drop
    the general statement that frames the specific one."""
    await add_document(session, "doc")
    await add_chunk(
        session, "general", text="definition du cancer", disease_setting="not_specified"
    )

    report = await search(
        session,
        concepts=Concepts(french=("definition du cancer",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", "prostate_localized_low_risk"),
    )

    assert [chunk.chunk_id for chunk in report.chunks] == ["general"]


# --------------------------------------------------------------------------------------
# Widening — and what it must never drop
# --------------------------------------------------------------------------------------


def test_widening_drops_therapy_line_before_disease_setting() -> None:
    """Therapy line is the narrowest claim and the one a guideline is least likely to state.
    Disease setting is the difference between localized and metastatic disease."""
    strict = Filter("prostate", "prostate_localized_low_risk", "salvage", "urology_oncology")

    once = strict.widen()
    assert once == Filter(
        "prostate", "prostate_localized_low_risk", None, "urology_oncology"
    )

    twice = once.widen() if once else None
    assert twice == Filter("prostate", None, None, "urology_oncology")

    assert twice is not None and twice.widen() is None


async def test_widening_finds_a_passage_the_strict_filter_refused(
    session: AsyncSession,
) -> None:
    await add_document(session, "doc")
    await add_chunk(session, "primary-only", text="curage ganglionnaire", therapy_line="primary")

    report = await search(
        session,
        concepts=Concepts(french=("curage ganglionnaire",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", "prostate_localized_low_risk", "salvage"),
    )

    assert report.widened == 1
    assert [chunk.chunk_id for chunk in report.chunks] == ["primary-only"]
    # Recorded rather than blended in: a passage admitted only because the therapy line was
    # dropped is a weaker match, and the reader is entitled to know.
    assert report.filter_used == Filter("prostate", "prostate_localized_low_risk")


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

    report = await search(
        session,
        concepts=Concepts(french=("cystectomie totale",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", "prostate_localized_low_risk", "primary"),
    )

    assert report.chunks == []
    assert report.filter_used is None


async def test_widening_never_drops_the_agent_role(session: AsyncSession) -> None:
    """A geriatric question must not be answered with urology evidence because no geriatric
    passage exists. That is exactly the gap the corpus currently has."""
    await add_document(session, "doc")
    await add_chunk(session, "urology-1", text="evaluation du patient")

    report = await search(
        session,
        concepts=Concepts(french=("evaluation du patient",)),
        embedding=None,
        embedding_model=MODEL,
        filter=Filter("prostate", "prostate_localized_low_risk", "primary", "geriatric_frailty"),
    )

    assert report.chunks == []
    assert report.widened == 2


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

    report = await search(
        session,
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

    report = await search(
        session,
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

    report = await search(
        session,
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

    report = await search(
        session,
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

    report = await search(
        session,
        concepts=Concepts(french=("cystectomie totale",)),
        embedding=vector(2),
        embedding_model=MODEL,
        filter=Filter("prostate"),
    )

    assert (report.keyword_hits, report.dense_hits) == (1, 1)
    assert report.chunks[0].found_by_both is True
