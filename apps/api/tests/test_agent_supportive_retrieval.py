"""Each agent asks two questions, and the bundle says which passages answered which.

The nutrition and geriatric guidance was never unreachable: it is eligible, and the filter
admits it. It lost on ranking to treatment passages every time the question was about
treatment, which is the retriever working correctly. These tests cover the second retrieval
that exists because of that — the merge, the de-duplication, and the note that keeps the agent
from citing a screening instrument as though it answered the treatment question.

`search` is faked rather than run: what needs checking here is the composition of two result
sets, not the ranking of either, and ranking has its own tests against the real corpus.
"""

from collections.abc import Sequence

import pytest

from rcpia.agents import evidence as agent_evidence
from rcpia.agents.evidence import guideline_passages_for
from rcpia.agents.schemas import MAX_TEXT
from rcpia.cases.ledger import LedgerEntry
from rcpia.evidence.embedding import Embedder
from rcpia.evidence.models import EMBEDDING_DIMENSIONS
from rcpia.evidence.retrieval import Concepts, RetrievalReport, RetrievedChunk
from rcpia.query.questions import SUPPORTIVE_QUESTION


class FakeEmbedder(Embedder):
    """Deterministic vectors, no network. Records what was embedded, in order."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    @property
    def model(self) -> str:
        return "fake-embed"

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [[0.1] * EMBEDDING_DIMENSIONS for _ in texts]


def chunk(chunk_id: str, document_id: str, text: str = "texte") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        text=text,
        citation_label=f"{document_id} — extrait",
        page_start=1,
        page_end=1,
        recommendation_number=None,
        locator_method="source_html_block",
        locator_confidence=1.0,
        clinical_status="poc_unreviewed",
        version_label="2026",
        publisher="test",
        disease_setting="bladder_mibc_non_metastatic",
        not_specified_reason=None,
        language="fr",
        score=1.0,
    )


TREATMENT = [chunk("aua.bladder-mibc.2024.neoadjuvant.01.0401", "aua.bladder-mibc.2024")]
SUPPORT = [
    chunk("esmo.cancer-cachexia.2021.screening.01.0010", "esmo.cancer-cachexia.2021"),
    chunk("siog.geriatric-screening.2015.g8.01.0020", "siog.geriatric-screening.2015"),
]

def entry(key: str, label: str, value: str) -> LedgerEntry:
    return LedgerEntry(
        key=key,
        label=label,
        category="disease",
        value=value,
        status="confirmed",
        origin="machine",
        highlight="neutral",
    )


LEDGER = [
    entry("ctnm", "cTNM", "cT2 N0 M0"),
    entry("muscle_invasion", "Infiltration", "Oui"),
    entry("mna", "MNA", "7"),
]


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record every `search` call and answer by which question was asked."""
    calls: list[dict] = []

    async def fake_search(session: object, **kwargs: object) -> RetrievalReport:
        calls.append(dict(kwargs))
        supportive = kwargs.get("question") == SUPPORTIVE_QUESTION
        source = SUPPORT if supportive else TREATMENT
        limit = int(kwargs.get("limit") or 0)
        return RetrievalReport(chunks=list(source[:limit]))

    monkeypatch.setattr(agent_evidence, "search", fake_search)
    return calls


async def run(
    spy: list[dict], *, entries: Sequence[LedgerEntry] = LEDGER, **kwargs: object
) -> tuple[list, list[str]]:
    return await guideline_passages_for(
        None,  # type: ignore[arg-type]
        cancer_type="bladder",
        agent_role="medical_oncology",
        entries=list(entries),
        embedder=FakeEmbedder(),
        reranker=None,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


async def test_two_searches_run_and_the_second_is_the_supportive_one(spy: list[dict]) -> None:
    """The defect this exists to fix: one retrieval could only ever answer one subject."""
    await run(spy)

    assert len(spy) == 2
    assert spy[0]["question"] != SUPPORTIVE_QUESTION
    assert spy[1]["question"] == SUPPORTIVE_QUESTION


async def test_the_supportive_search_uses_the_same_filter(spy: list[dict]) -> None:
    """The role gate is what makes one question return different evidence per specialist.

    Medical oncology reaches SIOG and ESMO cachexia through it; urology, not admitted to those,
    reaches surgical nutrition instead. Dropping the filter here would hand every agent the
    same supportive passages and undo that.
    """
    await run(spy)

    assert spy[0]["filter"] == spy[1]["filter"]
    assert spy[1]["filter"].agent_role == "medical_oncology"


async def test_the_supportive_search_carries_no_treatment_concepts(spy: list[dict]) -> None:
    """Concepts are built from the treatment question's vocabulary. Passing them into the
    supportive search would pull it back toward the subject it exists to leave."""
    await run(spy)

    assert spy[1]["concepts"] == Concepts()
    assert spy[0]["concepts"] != Concepts()


async def test_both_result_sets_reach_the_agent(spy: list[dict]) -> None:
    passages, _ = await run(spy)

    ids = [p.id for p in passages]
    assert ids[0] == TREATMENT[0].chunk_id
    assert {c.chunk_id for c in SUPPORT} <= set(ids)


async def test_a_passage_found_by_both_questions_is_handed_over_once(
    spy: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two headings over the same text would overstate how much evidence the agent has."""
    shared = TREATMENT[0]

    async def overlapping(session: object, **kwargs: object) -> RetrievalReport:
        supportive = kwargs.get("question") == SUPPORTIVE_QUESTION
        return RetrievalReport(chunks=[shared, *SUPPORT] if supportive else [shared])

    monkeypatch.setattr(agent_evidence, "search", overlapping)
    passages, _ = await run(spy)

    ids = [p.id for p in passages]
    assert ids.count(shared.chunk_id) == 1


async def test_the_agent_is_told_which_passages_answered_the_other_question(
    spy: list[dict],
) -> None:
    """Without this the agent sees passages of equal standing and may cite a screening
    instrument as the guidance on the treatment it was asked about."""
    _, notes = await run(spy)

    note = next((n for n in notes if "seconde recherche" in n), None)
    assert note is not None
    for supporting in SUPPORT:
        assert supporting.chunk_id in note
    assert TREATMENT[0].chunk_id not in note


async def test_no_supportive_note_when_the_second_search_returns_nothing(
    spy: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A note naming an empty list would be noise in the agent's constraints."""

    async def treatment_only(session: object, **kwargs: object) -> RetrievalReport:
        supportive = kwargs.get("question") == SUPPORTIVE_QUESTION
        return RetrievalReport(chunks=[] if supportive else list(TREATMENT))

    monkeypatch.setattr(agent_evidence, "search", treatment_only)
    _, notes = await run(spy)

    assert not any("seconde recherche" in n for n in notes)


async def test_the_supportive_search_can_be_switched_off(spy: list[dict]) -> None:
    """`supportive_limit=0` is the way back to single-question retrieval, so the change can be
    measured against its predecessor rather than argued about."""
    passages, notes = await run(spy, supportive_limit=0)

    assert len(spy) == 1
    assert [p.id for p in passages] == [TREATMENT[0].chunk_id]
    assert not any("seconde recherche" in n for n in notes)


async def test_truncation_is_counted_across_both_result_sets(
    spy: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The truncation warning used to count the treatment set only. A supportive passage cut
    mid-table is just as quotable as a treatment one."""
    long_support = chunk("espen.nutrition-cancer.2021.long.01.0001", "espen.nutrition-cancer.2021",
                         text="x" * (MAX_TEXT + 500))

    async def with_a_long_supportive_passage(
        session: object, **kwargs: object
    ) -> RetrievalReport:
        supportive = kwargs.get("question") == SUPPORTIVE_QUESTION
        return RetrievalReport(chunks=[long_support] if supportive else list(TREATMENT))

    monkeypatch.setattr(agent_evidence, "search", with_a_long_supportive_passage)
    passages, notes = await run(spy)

    assert any("tronqués" in n for n in notes)
    assert any(p.text.endswith(agent_evidence.TRUNCATION_MARKER) for p in passages)


async def test_an_empty_result_from_both_questions_is_still_reported(
    spy: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 'formulez un avis non étayé' note must not disappear because the merge made a list."""

    async def nothing(session: object, **kwargs: object) -> RetrievalReport:
        return RetrievalReport(chunks=[])

    monkeypatch.setattr(agent_evidence, "search", nothing)
    passages, notes = await run(spy)

    assert passages == []
    assert any("non étayé" in n for n in notes)
