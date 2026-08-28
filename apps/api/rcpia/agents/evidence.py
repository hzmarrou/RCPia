"""Putting guideline passages in front of the agent.

Until now `BundleInputs.guideline_passages` was always empty, and `bundle.py` said so in a
comment: *"Empty until evidence ingestion exists. The agent is told so through the constraints,
so it reports an ungrounded opinion rather than inventing a citation to look grounded."* The
constraint worked — the rendered fiche says *"Référentiel : Non référencé"* and the only citation
is an excerpt of the patient's own dossier. This module fills the gap.

**The query is derived, not written.** The filter and the search terms come from
`rcpia.query`, which reads the patient's own ledger. Nothing here asks a person which disease
setting to search in, which is the whole point — a fiche that needed a curated query per patient
would not be a system.

**Where the setting cannot be derived, the search is widened rather than guessed.** An
underivable setting leaves that dimension unset, which returns more and weaker evidence. It is
visible in the bundle's constraints, so the agent is told its evidence is broad rather than being
handed a confident-looking narrow set chosen at random.

**Every passage arrives with `clinical_status`.** The schema requires it with no default, and it
is `poc_unreviewed` for every passage in the corpus. A passage nobody has read must not reach a
clinician's page looking like one that has been.
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.schemas import MAX_TEXT, GuidelinePassage
from rcpia.cases.ledger import LedgerEntry
from rcpia.evidence.embedding import Embedder
from rcpia.evidence.rerank import Reranker
from rcpia.evidence.retrieval import RetrievedChunk, search
from rcpia.query.builder import Fact, build_filter

# How many passages an agent is given. Ten is the production `search` default; more would
# dilute a prompt that already carries the whole ledger.
PASSAGE_LIMIT = 10

# What a truncated passage says about itself. The corpus holds decision tables that run past
# the bundle's text cap, and a passage silently cut mid-table would be quoted in a fiche as
# though it were whole. The marker is French because everything the agent reads is.
TRUNCATION_MARKER = " […] (passage tronqué — consulter la source pour le texte intégral)"


def facts_from(entries: Sequence[LedgerEntry]) -> list[Fact]:
    """The ledger as the query builder reads it."""
    return [Fact(key=entry.key, value=entry.value) for entry in entries]


def to_passage(chunk: RetrievedChunk) -> GuidelinePassage:
    """One retrieved chunk as the agent sees it.

    **The chunk id is the citation key.** It could have been a short `gl-001` label, and that
    would have been easier — the id field was capped below the length of a corpus id. But a
    citation the agent emits should be resolvable straight back to the corpus, the gold set and
    the safety rules without a lookup table that can drift. The cap moved instead.
    """
    text = chunk.text
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    return GuidelinePassage(
        id=chunk.chunk_id,
        text=text,
        source=chunk.citation_label or chunk.document_id,
        document_id=chunk.document_id,
        clinical_status=chunk.clinical_status,
        section=None,
        version=chunk.version_label,
        page=chunk.page_start,
        recommendation_number=chunk.recommendation_number,
        locator_method=chunk.locator_method,
    )


async def guideline_passages_for(
    session: AsyncSession,
    *,
    cancer_type: str,
    agent_role: str,
    entries: Sequence[LedgerEntry],
    question: str,
    embedder: Embedder,
    reranker: Reranker,
    limit: int = PASSAGE_LIMIT,
) -> tuple[list[GuidelinePassage], list[str]]:
    """Retrieve the guidance this agent may see for this patient.

    Returns the passages and any **notes for the agent's constraints** — how the query was
    derived, and what the record did not contain. Those notes are the difference between an
    agent that knows its evidence is broad and one that assumes it is precise.
    """
    query = build_filter(
        cancer_type,
        facts_from(entries),
        agent_role=agent_role,
    )

    notes: list[str] = []
    if not query.decision.determined:
        notes.append(
            "Le stade de la maladie n'a pas pu être déduit du dossier "
            f"({query.decision.reason}) : la recherche documentaire n'a donc pas été "
            "restreinte au stade, et les passages fournis sont plus larges que nécessaire."
        )
    elif query.decision.missing:
        notes.append(
            "Stade retenu pour la recherche documentaire : "
            f"{query.decision.setting}. Éléments manquants pour affiner : "
            f"{', '.join(query.decision.missing)}."
        )

    vectors = await embedder.embed([question])
    report = await search(
        session,
        question=question,
        concepts=query.concepts,
        embedding=vectors[0] if vectors else None,
        embedding_model=embedder.model,
        filter=query.filter,
        reranker=reranker,
        limit=limit,
    )

    truncated = sum(1 for chunk in report.chunks if len(chunk.text) > MAX_TEXT)
    if truncated:
        notes.append(
            f"{truncated} passage(s) des recommandations ont été tronqués faute de place : "
            "leur texte est incomplet et signalé comme tel. Ne citez pas un passage tronqué "
            "comme s'il était complet."
        )
    if not report.chunks:
        notes.append(
            "Aucun passage des recommandations n'a été retenu pour ce patient : "
            "formulez un avis non étayé et dites-le explicitement."
        )
    if report.widened:
        notes.append(
            "La recherche documentaire a dû être élargie pour trouver des passages : "
            "les recommandations citées correspondent moins précisément à ce patient."
        )

    return [to_passage(chunk) for chunk in report.chunks], notes
