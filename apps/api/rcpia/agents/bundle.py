"""Building the task bundle one agent is given.

The bundle is assembled from a **stored ledger version**, not from the live ledger. A
correction landing while an agent is running would otherwise change its input halfway
through, and the opinion could never be tied back to what justified it.

Every snippet the agent may cite gets a short, stable id (`snip-001`). Ids are assigned
here and checked on the way back, so a citation is either something the agent was handed
or a fabrication — there is no third case.

Snippet text is the excerpt the fact was read from, not the whole chunk. A fact quotes
the exact line it came from, and that line is what a reviewer will be shown next to the
recommendation; handing over the surrounding page instead would let an agent cite a
snippet whose relevant sentence nobody can find.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.evidence import guideline_passages_for
from rcpia.agents.opinions import prior_opinions
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOpinion,
    BundleInputs,
    ConflictingField,
    GuidelinePassage,
    LedgerField,
    NomogramReading,
    OutputFormat,
    SourceSnippet,
    TaskBundle,
    agent_output_json_schema,
)
from rcpia.agents.serialization import bundle_bytes
from rcpia.cases import completeness
from rcpia.cases.completeness import BLOCKING_FIELDS
from rcpia.cases.fact_schema import required_fields_for
from rcpia.cases.ledger import (
    AMBER,
    RED,
    LedgerEntry,
    LedgerReading,
    adenocarcinoma_confirmation,
)
from rcpia.core.models import CancerType, Case, CaseLedgerVersion, DocumentChunk
from rcpia.evidence.embedding import Embedder
from rcpia.evidence.payload import EvidencePreparationError
from rcpia.evidence.rerank import Reranker
from rcpia.nomograms import evaluate_all
from rcpia.nomograms.record import Tristate

MAX_SNIPPET_CHARACTERS = 4000

# A bundle has to fit in the model's context window. These are deliberately blunt: the
# point is that a large case degrades in a way RCPia chooses and declares, rather than
# failing as a provider error nobody can explain to a clinician.
MAX_SNIPPETS = 250

# **The real limit is compaction, not the context window.** The runtime summarises when
# `contextTokens > contextWindow - reserveTokens`, keeping only the most recent
# `keepRecentTokens` verbatim and condensing everything before that into a checkpoint. A bundle
# large enough to trigger it would have part of the patient's ledger replaced by a summary,
# silently, and the agent would reason about a dossier it can no longer quote.
#
# So the ceiling is derived rather than chosen. Both figures are the runtime's documented
# defaults, overridable in `~/.pi/agent/settings.json`; if they are changed there, change them
# here. The character-per-token ratio is the runtime's own estimator, which divides raw text by
# four — deliberately the same approximation, so this bound errs the same way the check it is
# guarding against does.
PI_RESERVE_TOKENS = 16_384
PI_KEEP_RECENT_TOKENS = 20_000
CHARACTERS_PER_TOKEN = 4

# The smallest window any model RCPia routes an agent to is assumed to be 128k. A model with
# less than this needs its own number, which is why the assumption is written down rather than
# folded into the arithmetic.
ASSUMED_CONTEXT_WINDOW_TOKENS = 128_000

# Half of what fits before compaction. The other half is the agent's own reasoning, its tool
# results and its answer, none of which exist yet when the bundle is measured.
MAX_BUNDLE_BYTES = (
    (ASSUMED_CONTEXT_WINDOW_TOKENS - PI_RESERVE_TOKENS) * CHARACTERS_PER_TOKEN
) // 2

# What every specialist agent is bound by, whatever its speciality. These are constraints
# on the *output*, so they belong in the bundle rather than only in `brain.md`: the bundle
# is stored with the run, so what the agent was told is part of the record.
SAFETY_CONSTRAINTS: tuple[str, ...] = (
    "Cette sortie est provisoire et destinée à préparer la RCP ; elle ne remplace "
    "aucune décision médicale.",
    "Ne citez que des identifiants présents dans les données fournies ; "
    "n'inventez jamais de référence.",
    "N'inventez aucune donnée clinique absente du dossier : signalez-la comme manquante.",
    "Énoncez explicitement les incertitudes et les informations manquantes qui limitent "
    "votre avis.",
    "Si aucune recommandation ne peut être formulée sans risque, indiquez-le "
    "(recommendation_withheld) plutôt que de proposer une conduite mal fondée.",
    "Répondez uniquement par l'objet JSON demandé, en français, sans texte autour.",
)

GOAL = (
    "Produire un avis de spécialité structuré et provisoire sur ce dossier "
    "d'oncologie urologique, destiné à préparer la réunion de concertation "
    "pluridisciplinaire."
)

AUDIENCE = (
    "Agent de synthèse RCP et médecins relecteurs ; toute proposition sera relue et "
    "validée par des experts humains avant tout usage clinique."
)


class BundleError(Exception):
    """The case cannot be turned into a task bundle."""


class LedgerNotReady(BundleError):
    """No ledger snapshot exists yet, so there is nothing to reason about."""


class BundleTooLarge(BundleError):
    """Even after prioritising, the case does not fit in one bundle."""


@dataclass(frozen=True)
class BuiltBundle:
    """The bundle, plus what the caller needs to record and to decide with."""

    bundle: TaskBundle
    ledger_version: CaseLedgerVersion
    report: completeness.CompletenessReport


def entries_from_version(version: CaseLedgerVersion) -> list[LedgerEntry]:
    """Rebuild the ledger entries stored in a version snapshot.

    Unknown keys are dropped rather than passed through: a snapshot written by an older
    build of RCPia must still be readable, and `LedgerEntry` would reject a field it has
    since lost.
    """
    known = set(LedgerEntry.__dataclass_fields__)
    entries: list[LedgerEntry] = []

    for raw in version.ledger.get("entries", []):
        if not isinstance(raw, dict):  # pragma: no cover - snapshots are written by us
            continue
        values = {key: value for key, value in raw.items() if key in known}
        # JSON has no tuple: a snapshot round-trips `source_chunk_ids` as a list, and both
        # dataclasses declare it a tuple. Left uncoerced, concatenating it against a tuple
        # elsewhere (`bundle.py`'s own chunk-id gathering) fails with a plain `TypeError` --
        # a stored version, not a freshly built one, is what actually exercises this path.
        if "source_chunk_ids" in values:
            values["source_chunk_ids"] = tuple(values["source_chunk_ids"] or ())
        readings: list[LedgerReading] = []
        for reading in raw.get("readings", ()):
            if not isinstance(reading, dict):
                continue
            reading_values = {
                key: value
                for key, value in reading.items()
                if key in LedgerReading.__dataclass_fields__
            }
            if "source_chunk_ids" in reading_values:
                reading_values["source_chunk_ids"] = tuple(
                    reading_values["source_chunk_ids"] or ()
                )
            readings.append(LedgerReading(**reading_values))
        values["readings"] = tuple(readings)
        entries.append(LedgerEntry(**values))

    return entries


async def _chunk_metadata(
    session: AsyncSession, chunk_ids: set[uuid.UUID]
) -> dict[uuid.UUID, DocumentChunk]:
    if not chunk_ids:
        return {}
    rows = await session.execute(
        sa.select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
    )
    return {chunk.id: chunk for chunk in rows.scalars().all()}


def _page_span(
    reading: LedgerReading, chunks: dict[uuid.UUID, DocumentChunk]
) -> tuple[int | None, int | None]:
    """The pages a reading's excerpt actually spans, from every chunk it was built from.

    `source_chunk_id` alone names only the first contributing chunk, which for a multi-
    paragraph imaging report can be the acquisition-protocol paragraph while the diagnostic
    sentence the excerpt quotes sits several chunks -- and pages -- later. A citation built
    from that one page would send a reviewer somewhere the sentence is not.

    Returns `(page, None)` when every resolvable chunk lands on the same page -- today's
    behaviour, unchanged -- and `(first_page, last_page)` only when the excerpt genuinely
    spans more than one.
    """
    ids = dict.fromkeys(
        cid for cid in (reading.source_chunk_id, *reading.source_chunk_ids) if cid
    )
    resolved_pages: set[int] = set()
    for cid in ids:
        chunk = chunks.get(uuid.UUID(cid))
        if chunk is not None and chunk.page is not None:
            resolved_pages.add(chunk.page)
    pages = sorted(resolved_pages)
    if not pages:
        return None, None
    if len(pages) == 1:
        return pages[0], None
    return pages[0], pages[-1]


def _readings_of(entry: LedgerEntry) -> tuple[LedgerReading, ...]:
    """Every reading behind a field, including the single implicit one."""
    if entry.readings:
        return entry.readings
    if entry.source_excerpt is None:
        return ()
    return (
        LedgerReading(
            value=entry.value,
            source_excerpt=entry.source_excerpt,
            source_document_id=entry.source_document_id,
            source_chunk_id=entry.source_chunk_id,
            source_chunk_ids=entry.source_chunk_ids,
            fact_id=entry.fact_id,
        ),
    )


async def build_bundle(
    session: AsyncSession,
    *,
    case: Case,
    agent_role: str,
    version: CaseLedgerVersion,
    clinical_question: str | None = None,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    panel_id: uuid.UUID | None = None,
    missing_roles: Sequence[str] = (),
) -> BuiltBundle:
    """Turn a stored ledger version into the file Pi is given.

    `panel_id` scopes a synthesis to the opinions its own panel gathered. Without it the
    synthesis reads the newest opinion per speciality across the whole case, which silently
    mixes panels once a case has been analysed twice at two ledger versions.
    """
    entries = entries_from_version(version)
    if not entries:
        raise LedgerNotReady(
            f"La version {version.version} du dossier ne contient aucun champ."
        )

    report = completeness.assess(entries, cancer_type=case.cancer_type)

    # The synthesis reads opinions, not the corpus. Its evidence is whatever the specialists
    # already cited, gathered below, so that it can quote their grounds without being handed a
    # separate search that might ground a *different* claim than the one it is reconciling.
    opinions: list[AgentOpinion] = []
    passages: list[GuidelinePassage] = []
    evidence_notes: list[str] = []
    if agent_role == SYNTHESIS_ROLE:
        opinions, passages, synthesis_notes = await prior_opinions(
            session, case_id=case.id, panel_id=panel_id, missing_roles=missing_roles
        )
        evidence_notes.extend(synthesis_notes)
    elif embedder is not None and reranker is not None:
        try:
            passages, evidence_notes = await guideline_passages_for(
                session,
                cancer_type=str(case.cancer_type),
                agent_role=agent_role,
                entries=entries,
                question=clinical_question,
                embedder=embedder,
                reranker=reranker,
            )
        except EvidencePreparationError as exc:
            raise BundleError(str(exc)) from exc

    # Every chunk any reading was assembled from, not only the one it is filed under. A
    # multi-chunk imaging report needs all of them resolved, or the snippet below can only
    # ever locate its excerpt on the first contributing chunk's page.
    chunk_ids = {
        uuid.UUID(chunk_id)
        for entry in entries
        for reading in _readings_of(entry)
        for chunk_id in (reading.source_chunk_id, *reading.source_chunk_ids)
        if chunk_id
    }
    chunks = await _chunk_metadata(session, chunk_ids)

    snippets: list[SourceSnippet] = []
    # One id per distinct excerpt: two fields read from the same line cite the same
    # snippet, which is what a reviewer expects to see.
    ids_by_excerpt: dict[tuple[str, str | None], str] = {}
    source_ids_by_key: dict[str, list[str]] = {}
    dropped: list[str] = []

    # Snippets are assigned in order of clinical weight, so that when a large case runs
    # out of room it is the least consequential sources that go. A bundle truncated by
    # arrival order would just as easily drop the muscle-invasion line.
    for entry in sorted(entries, key=lambda item: _priority(item, case.cancer_type)):
        source_ids: list[str] = []
        lost = False

        for reading in _readings_of(entry):
            excerpt = (reading.source_excerpt or "").strip()
            # A snippet with no document behind it cannot be traced back, and an
            # untraceable quotation is worse than none.
            if not excerpt or not reading.source_document_id:
                continue
            key = (excerpt[:MAX_SNIPPET_CHARACTERS], reading.source_chunk_id)
            snippet_id = ids_by_excerpt.get(key)

            if snippet_id is None:
                if len(snippets) >= MAX_SNIPPETS:
                    lost = True
                    continue
                snippet_id = f"snip-{len(snippets) + 1:03d}"
                ids_by_excerpt[key] = snippet_id
                chunk = (
                    chunks.get(uuid.UUID(reading.source_chunk_id))
                    if reading.source_chunk_id
                    else None
                )
                page, page_end = _page_span(reading, chunks)
                snippets.append(
                    SourceSnippet(
                        id=snippet_id,
                        text=key[0],
                        source_document_id=reading.source_document_id,
                        source_chunk_id=reading.source_chunk_id,
                        page=page,
                        page_end=page_end,
                        section=chunk.section if chunk else None,
                    )
                )
            if snippet_id not in source_ids:
                source_ids.append(snippet_id)

        source_ids_by_key[entry.key] = source_ids
        if lost:
            dropped.append(entry.label)

    # The ledger itself keeps schema order, which is the order a clinician reads it in.
    ledger_fields = [
        LedgerField(
            key=entry.key,
            label=entry.label,
            value=entry.value,
            unit=entry.unit,
            status=entry.status,
            origin=entry.origin if entry.origin in ("machine", "human") else "machine",
            # A correction has no excerpt to cite, so its reason is the only provenance
            # it has. Without it the value would look like an unsourced machine reading.
            correction_note=entry.note if entry.corrected else None,
            source_ids=source_ids_by_key.get(entry.key, []),
        )
        for entry in entries
    ]

    # A contradiction is stated as one, with each reading's own source. Presenting only
    # the first value would hide the very thing a clinician has to settle.
    conflicts = [
        ConflictingField(
            field=entry.key,
            values=[reading.value or "" for reading in entry.readings],
            source_ids=source_ids_by_key.get(entry.key, []),
        )
        for entry in entries
        if entry.highlight == AMBER and len(entry.readings) > 1
    ]

    # **Deterministic calculators, run over this patient's own ledger.**
    #
    # Handed to the agent so that it never has to estimate a risk the clinician asked us to
    # compute -- and handed to it *with its refusals*, because "Briganti 2023 needs the mpMRI
    # index-lesion diameter" is an instruction to a tumour board, while silence is not.
    #
    # The synthesis is excluded on purpose. It reconciles what the specialists said, and a
    # figure appearing there for the first time would be a new clinical claim rather than a
    # reconciliation of existing ones.
    nomograms: list[NomogramReading] = []
    if agent_role != SYNTHESIS_ROLE:
        facts = {entry.key: entry.value for entry in entries if entry.value}
        # Read from the stored version's own entries, so the agent is handed the calculators'
        # answers as they stood for the ledger the clinician approved -- not as they would be
        # recomputed against whatever the case says now.
        for result in evaluate_all(
            facts,
            cancer_type=str(case.cancer_type),
            adenocarcinoma_confirmed=Tristate(adenocarcinoma_confirmation(entries)),
        ):
            nomograms.append(
                NomogramReading(
                    model_id=result.model_id,
                    display_name=result.display_name,
                    status=str(result.status),
                    estimates=dict(result.estimates),
                    missing_fields=list(result.missing_fields),
                    reason_codes=[str(code) for code in result.reason_codes],
                    inputs_used=dict(result.inputs_used),
                    limitation=result.provenance.limitation if result.provenance else "",
                    source_url=result.provenance.source_url if result.provenance else "",
                )
            )

    inputs = BundleInputs(
        case_id=str(case.id),
        agent_role=agent_role,
        cancer_type=str(case.cancer_type),
        clinical_question=clinical_question,
        # Free text can carry another agent's opinion, so a bundle carrying a question is
        # not an independent first pass and must not claim to be.
        ledger_version=version.version,
        case_ledger=ledger_fields,
        source_snippets=snippets,
        # Retrieved from the corpus when a retriever is supplied, using a filter derived
        # from this patient's own ledger. Still empty without one, and the constraints say
        # so -- an agent told its evidence is absent reports an ungrounded opinion rather
        # than inventing a citation to look grounded.
        guideline_passages=passages,
        missing_fields=[entry.key for entry in entries if entry.highlight == RED],
        conflicting_fields=conflicts,
        nomograms=nomograms,
        safety_constraints=list(SAFETY_CONSTRAINTS),
        # Populated only for the synthesis role; every first pass leaves this empty.
        agent_opinions=opinions,
        missing_opinions=list(missing_roles),
    )

    constraints = [
        *SAFETY_CONSTRAINTS,
        *report.limitation_notes(),
        *evidence_notes,
    ]
    if nomograms:
        computed = [reading for reading in nomograms if reading.estimates]
        if computed:
            constraints.append(
                "Des calculateurs validés ont été exécutés sur ce dossier et leurs résultats "
                "figurent dans `nomograms`. Reprenez ces chiffres tels quels en nommant le "
                "modèle et sa version ; n'estimez jamais vous-même une probabilité qu'un "
                "calculateur a produite, et ne recalculez pas la sienne."
            )
        refused = [reading for reading in nomograms if not reading.estimates]
        if refused:
            constraints.append(
                "Certains calculateurs n'ont pas pu conclure : `nomograms` indique lequel et "
                "quelle donnée lui manque. Rapportez ce manque à la RCP plutôt que de fournir "
                "une estimation de remplacement."
            )
    if agent_role == SYNTHESIS_ROLE:
        constraints.append(
            "Vous recevez les avis déjà rendus par les spécialistes. Rapportez leurs "
            "convergences et leurs divergences ; n'arbitrez pas un désaccord et ne "
            "choisissez pas entre deux spécialités — exposez le désaccord à la RCP."
        )
        if not opinions:
            constraints.append(
                "Aucun avis de spécialité n'a pu être chargé : aucune synthèse n'est "
                "possible et vous devez le dire explicitement."
            )
    elif not passages:
        constraints.append(
            "Aucun passage des recommandations n'est fourni : votre avis n'est pas étayé "
            "par un référentiel et doit le dire explicitement."
        )
    if not snippets:
        constraints.append(
            "Aucun extrait source n'est disponible : appuyez-vous sur le tableau de "
            "données et dites-le explicitement."
        )
    if dropped:
        # Declared, never silent. An agent reasoning from a truncated dossier without
        # being told it was truncated would report a confidence it has not earned.
        constraints.append(
            "Dossier volumineux : les extraits sources de certains champs ont été omis "
            f"faute de place ({', '.join(sorted(dropped))}). Traitez ces champs comme "
            "non sourcés et signalez-le."
        )
    if clinical_question:
        constraints.append(
            "Une question a été posée par un opérateur : cet avis n'est donc pas un "
            "premier passage indépendant."
        )

    bundle = TaskBundle(
        goal=GOAL,
        audience=AUDIENCE,
        inputs=inputs,
        # By field name, not by the `schema` alias: `schema_` avoids shadowing
        # pydantic's own attribute, and the alias is what serialization emits.
        output_format=OutputFormat(schema_=agent_output_json_schema(agent_role)),
        constraints=constraints,
    )

    # Refused, not silently trimmed further. A case this large is a situation a person
    # has to look at, and a provider error would tell them nothing about why.
    size = len(bundle_bytes(bundle))
    if size > MAX_BUNDLE_BYTES:
        raise BundleTooLarge(
            f"Le dossier dépasse la taille transmissible à un agent "
            f"({size // 1024} Ko pour un maximum de {MAX_BUNDLE_BYTES // 1024} Ko). "
            "Réduisez le nombre de documents ou scindez l'analyse."
        )

    return BuiltBundle(bundle=bundle, ledger_version=version, report=report)


def _priority(entry: LedgerEntry, cancer_type: CancerType) -> tuple[int, str]:
    """Rank a field by how badly its sources are needed, lowest first.

    Blocking fields decide whether an answer is possible at all; a contradiction is the
    thing a clinician must settle; a correction carries a person's judgment. Those three
    keep their sources longest.
    """
    if entry.key in BLOCKING_FIELDS.get(cancer_type, ()):
        rank = 0
    elif entry.highlight == AMBER:
        rank = 1
    elif entry.corrected:
        rank = 2
    elif entry.key in {spec.key for spec in required_fields_for(cancer_type)}:
        rank = 3
    else:
        rank = 4
    return rank, entry.key
