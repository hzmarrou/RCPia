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
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.schemas import GuidelinePassage
from rcpia.cases.ledger import LedgerEntry
from rcpia.evidence.embedding import Embedder
from rcpia.evidence.hashing import payload_sha256, text_identity
from rcpia.evidence.payload import EvidencePreparationError, PreparedEvidence, prepare_evidence
from rcpia.evidence.rerank import Reranker
from rcpia.evidence.retrieval import Concepts, Filter, RetrievalReport, RetrievedChunk, search
from rcpia.query.builder import Fact, build_filter
from rcpia.query.questions import SUPPORTIVE_QUESTION, question_for

# How many passages an agent is given for its own question. Ten is the production `search`
# default; more would dilute a prompt that already carries the whole ledger.
PASSAGE_LIMIT = 10

# How many more it is given from the supportive-care question. Held *alongside* the ten rather
# than carved out of them, deliberately: the primary set is what produced the reasoning this
# project has actually measured — the urologist's rejection of an orthotopic neobladder below
# eGFR 50 came from those slots — and narrowing it to make room would trade a demonstrated gain
# for an untested one. Four is what the supportive question earns: probed against the medical
# oncology filter it returns four distinct documents (SIOG bladder, ESMO-SIOG, SIOG screening,
# ESMO cachexia) before it starts repeating one.
#
# The budget is two constants rather than a split of one because the two questions are not
# competing for the same thing. If prompt size becomes the binding constraint, this is the
# number to cut first: losing supportive grounding degrades an opinion, losing treatment
# passages changes it.
SUPPORTIVE_PASSAGE_LIMIT = 4


@dataclass
class EvidenceSearchTrace:
    question: str
    concepts: Concepts
    filter: Filter
    limit: int
    report: RetrievalReport


@dataclass
class EvidenceTrace:
    """Explicit, single-use collector. Never allocated by ordinary bundle callers.

    A failed retrieval leaves ``complete=False`` and still raises its original exception.
    This is evidence selection/delivery, not a TaskBundle or a generation trace.
    """

    primary: EvidenceSearchTrace | None = None
    supportive: EvidenceSearchTrace | None = None
    primary_ids: list[str] = field(default_factory=list)
    supportive_ids: list[str] = field(default_factory=list)
    supportive_duplicate_ids: list[str] = field(default_factory=list)
    converted: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence_pack_sha256: str | None = None
    complete: bool = False


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
    return _to_passage(chunk, _prepare_chunk(chunk))


def _prepare_chunk(chunk: RetrievedChunk) -> PreparedEvidence:
    if not chunk.contextual_text.endswith(chunk.text):
        raise EvidencePreparationError(
            "Le contexte enregistré ne conserve pas le texte source intégral du passage : "
            "analyse arrêtée, aucune reconstruction n'est autorisée."
        )
    return prepare_evidence(chunk.contextual_text)


def _to_passage(chunk: RetrievedChunk, prepared: PreparedEvidence) -> GuidelinePassage:
    return GuidelinePassage(
        id=chunk.chunk_id,
        text=prepared.text,
        source=chunk.citation_label or chunk.document_id,
        document_id=chunk.document_id,
        clinical_status=chunk.clinical_status,
        section=" > ".join(chunk.section_path) or None,
        version=chunk.version_label,
        page=chunk.page_start,
        page_end=chunk.page_end,
        recommendation_number=chunk.recommendation_number,
        locator_method=chunk.locator_method,
    )


async def guideline_passages_for(
    session: AsyncSession,
    *,
    cancer_type: str,
    agent_role: str,
    entries: Sequence[LedgerEntry],
    question: str | None = None,
    embedder: Embedder,
    reranker: Reranker,
    limit: int = PASSAGE_LIMIT,
    supportive_limit: int = SUPPORTIVE_PASSAGE_LIMIT,
    trace: EvidenceTrace | None = None,
) -> tuple[list[GuidelinePassage], list[str]]:
    """Retrieve the guidance this agent may see for this patient.

    Returns the passages and any **notes for the agent's constraints** — how the query was
    derived, and what the record did not contain. Those notes are the difference between an
    agent that knows its evidence is broad and one that assumes it is precise.

    **The question is derived too, unless an operator asked one.** It used to be a single
    generic string per organ, and that string cost the system most of its corpus: it matched
    AFU's French section headings so closely that AFU filled all ten slots for every prostate
    patient, and eleven core urology guidelines holding 1,465 eligible passages were never
    retrieved once across fourteen dossiers. The setting was already being derived here to build
    the filter; it now names the question as well. See `rcpia.query.questions`.
    """
    if trace is not None and (trace.primary is not None or trace.complete):
        raise ValueError("EvidenceTrace collectors are single-use.")
    query = build_filter(
        cancer_type,
        facts_from(entries),
        agent_role=agent_role,
    )
    # An operator's own question always wins. Otherwise the patient's derived setting *and* the
    # asking specialist pick it, falling back to the setting alone and then to the organ's
    # generic question. The role matters here for the same reason it matters in the filter: two
    # specialists reading the same ten passages are not two opinions.
    search_question = question or question_for(
        cancer_type, query.decision.setting, agent_role
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

    vectors = await embedder.embed([search_question])
    report = await search(
        session,
        question=search_question,
        concepts=query.concepts,
        embedding=vectors[0] if vectors else None,
        embedding_model=embedder.model,
        filter=query.filter,
        reranker=reranker,
        limit=limit,
        trace=trace is not None,
    )
    if trace is not None:
        trace.primary = EvidenceSearchTrace(
            search_question, query.concepts, query.filter, limit, report
        )
        trace.primary_ids = [c.chunk_id for c in report.chunks]

    # The second search: what this patient needs before any treatment, rather than which
    # treatment. It runs under the *same* filter, so each specialist gets what it is permitted
    # to read — the nutrition and geriatric corpus for medical oncology, surgical nutrition for
    # urology — and it is a separate query because no single question ranks both a cisplatin
    # decision table and cachexia guidance. See `SUPPORTIVE_PASSAGE_LIMIT`.
    supportive: list[RetrievedChunk] = []
    if supportive_limit > 0:
        support_vectors = await embedder.embed([SUPPORTIVE_QUESTION])
        support_report = await search(
            session,
            question=SUPPORTIVE_QUESTION,
            # No concepts: those are built from the treatment question's vocabulary and would
            # pull this search back toward the subject it exists to leave.
            concepts=Concepts(),
            embedding=support_vectors[0] if support_vectors else None,
            embedding_model=embedder.model,
            filter=query.filter,
            reranker=reranker,
            limit=supportive_limit,
            trace=trace is not None,
        )
        # A passage both questions found is kept once, in the primary set. Handing the agent the
        # same text twice under two headings would misrepresent how much evidence it has.
        already = {chunk.chunk_id for chunk in report.chunks}
        supportive = [c for c in support_report.chunks if c.chunk_id not in already]
        if trace is not None:
            trace.supportive = EvidenceSearchTrace(
                SUPPORTIVE_QUESTION, Concepts(), query.filter, supportive_limit, support_report
            )
            trace.supportive_ids = [c.chunk_id for c in supportive]
            trace.supportive_duplicate_ids = [
                c.chunk_id for c in support_report.chunks if c.chunk_id in already
            ]

    chunks = list(report.chunks) + supportive

    if supportive:
        # Which passages answer which question. Without this the agent sees fourteen passages of
        # equal standing and may cite a nutrition screening instrument as though it were the
        # guidance on the treatment it was asked about.
        notes.append(
            "Une seconde recherche, distincte de la question thérapeutique, a été menée sur les "
            "soins de support, la dénutrition et la fragilité gériatrique. Les passages suivants "
            "en proviennent et ne répondent pas à la question thérapeutique : "
            + ", ".join(chunk.chunk_id for chunk in supportive)
            + "."
        )

    if not chunks:
        notes.append(
            "Aucun passage des recommandations n'a été retenu pour ce patient : "
            "formulez un avis non étayé et dites-le explicitement."
        )
    if report.widened:
        notes.append(
            "La recherche documentaire a dû être élargie pour trouver des passages : "
            "les recommandations citées correspondent moins précisément à ce patient."
        )

    prepared = [_prepare_chunk(chunk) for chunk in chunks]
    compacted = sum(item.transformation != "verbatim" for item in prepared)
    if compacted:
        notes.append(
            f"{compacted} passage(s) ont été compactés uniquement par retrait des espaces "
            "d'alignement des cellules Markdown. Le contenu de chaque unité source enregistrée "
            "est conservé ; les en-têtes ou notes absents de cette unité ne sont pas reconstitués."
        )
    passages = [
        _to_passage(chunk, item) for chunk, item in zip(chunks, prepared, strict=True)
    ]
    if trace is not None:
        trace.converted = [
            {
                "id": chunk.chunk_id,
                "raw_text": text_identity(chunk.text),
                "contextual_text": text_identity(chunk.contextual_text),
                "delivered_text": text_identity(passage.text),
                "transformation": item.transformation,
                "clipped": False,
                "unit_type": chunk.unit_type,
                "section": passage.section,
                "passage_sha256": payload_sha256(passage.model_dump(mode="json")),
            }
            for chunk, item, passage in zip(chunks, prepared, passages, strict=True)
        ]
        trace.notes = list(notes)
        trace.evidence_pack_sha256 = payload_sha256({
            "passages": [p.model_dump(mode="json") for p in passages], "notes": notes,
        })
        trace.complete = True
    return passages, notes
