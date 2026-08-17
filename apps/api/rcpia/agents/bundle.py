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
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.schemas import (
    BundleInputs,
    ConflictingField,
    LedgerField,
    OutputFormat,
    SourceSnippet,
    TaskBundle,
    agent_output_json_schema,
)
from rcpia.cases import completeness
from rcpia.cases.ledger import AMBER, RED, LedgerEntry, LedgerReading
from rcpia.core.models import Case, CaseLedgerVersion, DocumentChunk

MAX_SNIPPET_CHARACTERS = 4000

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
        values["readings"] = tuple(
            LedgerReading(
                **{
                    key: value
                    for key, value in reading.items()
                    if key in LedgerReading.__dataclass_fields__
                }
            )
            for reading in raw.get("readings", ())
            if isinstance(reading, dict)
        )
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
) -> BuiltBundle:
    """Turn a stored ledger version into the file Pi is given."""
    entries = entries_from_version(version)
    if not entries:
        raise LedgerNotReady(
            f"La version {version.version} du dossier ne contient aucun champ."
        )

    report = completeness.assess(entries, cancer_type=case.cancer_type)

    chunk_ids = {
        uuid.UUID(reading.source_chunk_id)
        for entry in entries
        for reading in _readings_of(entry)
        if reading.source_chunk_id
    }
    chunks = await _chunk_metadata(session, chunk_ids)

    snippets: list[SourceSnippet] = []
    # One id per distinct excerpt: two fields read from the same line cite the same
    # snippet, which is what a reviewer expects to see.
    ids_by_excerpt: dict[tuple[str, str | None], str] = {}
    ledger_fields: list[LedgerField] = []
    conflicts: list[ConflictingField] = []

    for entry in entries:
        source_ids: list[str] = []

        for reading in _readings_of(entry):
            excerpt = (reading.source_excerpt or "").strip()
            if not excerpt:
                continue
            key = (excerpt[:MAX_SNIPPET_CHARACTERS], reading.source_chunk_id)
            snippet_id = ids_by_excerpt.get(key)

            if snippet_id is None:
                snippet_id = f"snip-{len(snippets) + 1:03d}"
                ids_by_excerpt[key] = snippet_id
                chunk = (
                    chunks.get(uuid.UUID(reading.source_chunk_id))
                    if reading.source_chunk_id
                    else None
                )
                snippets.append(
                    SourceSnippet(
                        id=snippet_id,
                        text=key[0],
                        source_document_id=reading.source_document_id or "",
                        source_chunk_id=reading.source_chunk_id,
                        page=chunk.page if chunk else None,
                        section=chunk.section if chunk else None,
                    )
                )
            if snippet_id not in source_ids:
                source_ids.append(snippet_id)

        ledger_fields.append(
            LedgerField(
                key=entry.key,
                label=entry.label,
                value=entry.value,
                unit=entry.unit,
                status=entry.status,
                source_ids=source_ids,
            )
        )

        # A contradiction is stated as one, with each reading's own source. Presenting
        # only the first value would hide the very thing a clinician has to settle.
        if entry.highlight == AMBER and len(entry.readings) > 1:
            conflicts.append(
                ConflictingField(
                    field=entry.key,
                    values=[reading.value or "" for reading in entry.readings],
                    source_ids=source_ids,
                )
            )

    inputs = BundleInputs(
        case_id=str(case.id),
        agent_role=agent_role,
        cancer_type=str(case.cancer_type),
        clinical_question=clinical_question,
        ledger_version=version.version,
        case_ledger=ledger_fields,
        source_snippets=snippets,
        # Empty until evidence ingestion exists. The agent is told so through the
        # constraints, so it reports an ungrounded opinion rather than inventing a
        # citation to look grounded.
        guideline_passages=[],
        missing_fields=[entry.key for entry in entries if entry.highlight == RED],
        conflicting_fields=conflicts,
        safety_constraints=list(SAFETY_CONSTRAINTS),
    )

    constraints = [
        *SAFETY_CONSTRAINTS,
        *report.limitation_notes(),
    ]
    if not snippets:
        constraints.append(
            "Aucun extrait source n'est disponible : appuyez-vous sur le tableau de "
            "données et dites-le explicitement."
        )

    return BuiltBundle(
        bundle=TaskBundle(
            goal=GOAL,
            audience=AUDIENCE,
            inputs=inputs,
            # By field name, not by the `schema` alias: `schema_` avoids shadowing
            # pydantic's own attribute, and the alias is what serialization emits.
            output_format=OutputFormat(schema_=agent_output_json_schema()),
            constraints=constraints,
        ),
        ledger_version=version,
        report=report,
    )
