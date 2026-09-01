"""Storing extracted facts, and the job that produces them.

Facts are append-only, so this module never rewrites one. Re-running extraction after a
re-parse, or after another dossier arrives, produces a new generation of facts citing the
new chunks; the ledger reads the newest generation, and the older ones remain as the
record of what the machine said before.

The whole case is read at once. A dossier and its addendum are two documents describing
one patient, so a value read from each must be compared, not stored twice as though each
document were its own case — that is how a creatinine of 17.33 in one and 173.3 in the
other would otherwise be silently reduced to whichever was seen first.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.pi_adapter import PiAdapter
from rcpia.cases.extraction import FactCandidate, ResolvedFact, extract_from_text, resolve
from rcpia.cases.fact_schema import (
    FIELDS_BY_KEY,
    absence_answers,
    fields_for,
    is_reported_absent,
)
from rcpia.cases.llm_extractor import (
    NarrativeExtraction,
    RejectedFact,
    extract,
    flatten,
)
from rcpia.cases.narrative_reader import reader_for
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    Case,
    DocumentChunk,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    JobStatus,
    ParseStatus,
    UploadedDocument,
)
from rcpia.jobs.models import JobOutcome

# Fields whose value is itself a date, worth recording as one.
DATE_FIELDS = frozenset({"diagnosis_date", "pathology_date"})
DATE_PATTERNS = (
    (re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b"), ("day", "month", "year")),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("year", "month", "day")),
)


async def extract_case_facts(
    session: AsyncSession, job: AnalysisJob, *, adapter: PiAdapter | None = None
) -> JobOutcome | None:
    """Extract facts for the whole case, reading every parsed document together.

    `adapter` enables the narrative pass. Without one the deterministic reading stands
    alone, which is the behaviour every environment without a model runtime needs — and
    the behaviour every test gets unless it asks for otherwise.
    """
    case = await session.scalar(
        sa.select(Case).where(
            Case.id == job.case_id, Case.organization_id == job.organization_id
        )
    )
    if case is None:
        return JobOutcome(
            status=JobStatus.failed, error_message="Dossier introuvable", retryable=False
        )

    documents = list(
        (
            await session.execute(
                sa.select(UploadedDocument)
                .where(
                    UploadedDocument.case_id == case.id,
                    UploadedDocument.parse_status == ParseStatus.parsed,
                )
                .order_by(UploadedDocument.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not documents:
        return JobOutcome(
            status=JobStatus.waiting_for_user,
            error_message="Aucun document analysé : déposez un dossier lisible.",
        )

    signature = extraction_signature(documents)
    if await _already_extracted(session, case.id, signature):
        # At-least-once delivery: this exact set of documents and parse runs has already
        # been read, and facts are append-only, so repeating it would duplicate the
        # machine's reading.
        return None

    candidates: list[FactCandidate] = []
    for document in documents:
        candidates.extend(await _candidates_for(session, document))

    # Second pass: read the prose for what the label matcher could not fill. It runs only on
    # fields still unanswered after the first pass, appends its candidates *after* the form's so
    # `resolve` keeps the form's reading, and is skipped entirely when no runtime is configured.
    narrative = await _narrative_pass(
        session,
        case=case,
        documents=documents,
        candidates=candidates,
        adapter=adapter,
    )
    candidates.extend(narrative.candidates)

    resolved = resolve(candidates, cancer_type=case.cancer_type)
    for fact in resolved:
        for row in _to_rows(case, fact, signature):
            session.add(row)

    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            event_type="case.facts_extracted",
            entity_type="cases",
            entity_id=case.id,
            payload={
                "documents": len(documents),
                "facts": len(resolved),
                "extraction_signature": signature,
                # What the second pass was asked for and what it contributed, so a ledger that
                # changed can be explained — and a fill rate measured — without re-running
                # anything. The field names are recorded, not just the counts: "it filled four
                # fields" and "it filled the grade" are different facts about a run.
                # False means the second pass never ran -- no model runtime configured, or
                # no adapter passed by the caller. Without it, a worker wired without an
                # adapter is indistinguishable from a dossier whose form answered everything.
                "narrative_ran": narrative.ran,
                "narrative_wanted": len(narrative.still_missing) + len(narrative.candidates),
                "narrative_facts": len(narrative.candidates),
                "narrative_filled": sorted(
                    {candidate.key for candidate in narrative.candidates}
                ),
                "narrative_rejected": len(narrative.rejected),
                "narrative_rejected_reasons": sorted(
                    {item.reason for item in narrative.rejected}
                ),
                "narrative_seconds": round(narrative.seconds, 1),
            },
        )
    )
    await session.flush()
    return None


async def _narrative_pass(
    session: AsyncSession,
    *,
    case: Case,
    documents: list[UploadedDocument],
    candidates: list[FactCandidate],
    adapter: "PiAdapter | None",
) -> NarrativeExtraction:
    """Ask a model to read the dossier's prose for the fields the form left unanswered.

    **Which fields are asked for is derived, never guessed.** A field is wanted when the first
    pass produced nothing for it, or produced `RAS` where absence cannot be an answer — the
    dossier-8 case, where the form's boxes are empty and the diagnosis is in a sentence. Fields
    the form answered are not re-read: the deterministic reading is better evidence than a
    model's, and re-opening a settled field would only manufacture conflicts.
    """
    reader = reader_for(adapter, case.id)
    if reader is None:
        # No model runtime, so nothing was asked. Recorded as such: the counts alone cannot
        # tell "asked and found nothing" from "never asked", and the second reads like a
        # clean run in an audit trail.
        return NarrativeExtraction(ran=False)

    found: dict[str, str | None] = {}
    for candidate in candidates:
        if candidate.value and candidate.key not in found:
            found[candidate.key] = candidate.value

    wanted = [
        spec.key
        for spec in fields_for(case.cancer_type)
        if _needs_narrative_reading(found.get(spec.key), spec.key)
    ]
    if not wanted:
        # A genuinely empty ask: the form answered every field, so the pass ran and had
        # nothing to do.
        return NarrativeExtraction(ran=True)

    # A field cannot be both *already found* and *still wanted*. The first run of this pass on
    # dossier 8 listed `gleason = RAS` under the fields already read while also asking for
    # gleason, and the model obeyed the instruction not to re-extract a field the first pass had
    # answered — so it skipped the one value the whole pass exists to recover. `RAS` was never an
    # answer; it must not appear as one here either.
    known = {key: value for key, value in found.items() if key not in set(wanted)}

    passages = await _passages(session, documents)
    text = "\n\n".join(passage.text for passage in passages)

    result = await extract(reader, dossier_text=text, known=known, wanted=wanted)
    return _made_traceable(replace(result, ran=True), passages)


@dataclass(frozen=True)
class _Passage:
    """One chunk of one document, with the flattened form used to locate a quotation."""

    document_id: uuid.UUID
    chunk_id: uuid.UUID
    text: str
    flattened: str


async def _passages(
    session: AsyncSession, documents: list[UploadedDocument]
) -> list[_Passage]:
    passages: list[_Passage] = []
    for document in documents:
        rows = (
            (
                await session.execute(
                    sa.select(DocumentChunk.id, DocumentChunk.text)
                    .where(
                        DocumentChunk.document_id == document.id,
                        DocumentChunk.parse_run == document.current_parse_run,
                    )
                    .order_by(DocumentChunk.ordinal)
                )
            )
            .tuples()
            .all()
        )
        passages.extend(
            _Passage(
                document_id=document.id,
                chunk_id=chunk_id,
                text=chunk_text or "",
                flattened=flatten(chunk_text or ""),
            )
            for chunk_id, chunk_text in rows
        )
    return passages


def _made_traceable(
    result: NarrativeExtraction, passages: list[_Passage]
) -> NarrativeExtraction:
    """Pin each narrative reading to the chunk its quotation came from, or drop it.

    A machine fact must name the document, the chunk and the excerpt it was read from —
    `machine_facts_are_traceable` enforces that in the database, and it is the constraint that
    makes a citation checkable rather than decorative. A quotation verified against the whole
    dossier but not locatable in any single chunk is one that spans a chunk boundary; it cannot
    be pinned, so it is refused rather than stored with an approximate source.
    """
    placed: list[FactCandidate] = []
    rejected = list(result.rejected)

    for candidate in result.candidates:
        needle = flatten(candidate.excerpt)
        passage = next((p for p in passages if needle and needle in p.flattened), None)
        if passage is None:
            rejected.append(
                RejectedFact(
                    candidate.key,
                    candidate.value or "",
                    "citation non localisable dans un passage unique du dossier",
                )
            )
            continue
        placed.append(
            replace(
                candidate, document_id=passage.document_id, chunk_id=passage.chunk_id
            )
        )

    placed_keys = {candidate.key for candidate in placed}
    return NarrativeExtraction(
        candidates=placed,
        still_missing=sorted(set(result.still_missing) | (
            {candidate.key for candidate in result.candidates} - placed_keys
        )),
        rejected=rejected,
        raw_response=result.raw_response,
        seconds=result.seconds,
        # Rebuilt field by field, so a flag added to the dataclass and forgotten here would
        # silently reset to its default -- which for `ran` means a pass that ran reporting
        # that it did not.
        ran=result.ran,
    )


def _needs_narrative_reading(value: str | None, key: str) -> bool:
    if value is None or not value.strip():
        return True
    # `RAS` on a diagnosis is an empty box, and the value may be written in prose elsewhere.
    return is_reported_absent(value) and not absence_answers(key)


def extraction_signature(documents: list[UploadedDocument]) -> str:
    """Identify exactly which reading of the case this is.

    Two documents at parse run 0 is a different reading from the same two where one has
    been re-parsed, and from either one alone.
    """
    material = ";".join(
        sorted(f"{document.id}:{document.current_parse_run}" for document in documents)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _document_text(session: AsyncSession, document: UploadedDocument) -> str:
    """The document as one string, in reading order — what the narrative pass is given."""
    chunks = (
        (
            await session.execute(
                sa.select(DocumentChunk.text)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.parse_run == document.current_parse_run,
                )
                .order_by(DocumentChunk.ordinal)
            )
        )
        .scalars()
        .all()
    )
    return "\n".join(chunk for chunk in chunks if chunk)


async def _candidates_for(
    session: AsyncSession, document: UploadedDocument
) -> list[FactCandidate]:
    chunks = list(
        (
            await session.execute(
                sa.select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.parse_run == document.current_parse_run,
                )
                .order_by(DocumentChunk.ordinal)
            )
        )
        .scalars()
        .all()
    )

    candidates: list[FactCandidate] = []
    for position, chunk in enumerate(chunks):
        # A label can end one chunk with its report opening the next, and an imaging report
        # runs across as many chunks as it has paragraphs, so each chunk is given the rest
        # of the document to read into. Extraction stops at the next label it recognises,
        # not at the chunk boundary.
        candidates.extend(
            extract_from_text(
                chunk.text,
                chunk_id=chunk.id,
                document_id=document.id,
                following_chunks=[
                    (later.text, later.id) for later in chunks[position + 1 :]
                ],
            )
        )
    return candidates


def _to_rows(case: Case, fact: ResolvedFact, signature: str) -> list[ExtractedFact]:
    """One row per distinct reading, so every conflicting value keeps its source."""
    spec = FIELDS_BY_KEY.get(fact.key)
    unit = spec.unit if spec else None

    if fact.status is FactStatus.missing:
        return [
            ExtractedFact(
                case_id=case.id,
                organization_id=case.organization_id,
                fact_type=fact.key,
                value=None,
                unit=unit,
                status=FactStatus.missing,
                origin=FactOrigin.machine,
                extraction_signature=signature,
            )
        ]

    note = None
    if fact.conflicting_values:
        note = "Valeurs divergentes dans le dossier : " + " / ".join(fact.conflicting_values)

    readings = fact.readings or ()
    return [
        ExtractedFact(
            case_id=case.id,
            organization_id=case.organization_id,
            fact_type=fact.key,
            value=reading.value,
            unit=unit,
            observed_date=_observed_date(fact.key, reading.value),
            status=fact.status,
            origin=FactOrigin.machine,
            source_document_id=reading.document_id,
            source_chunk_id=reading.chunk_id,
            source_excerpt=reading.excerpt,
            note=note,
            extraction_signature=signature,
        )
        for reading in readings
    ]


def _observed_date(key: str, value: str | None) -> datetime | None:
    """Read a date out of a date-valued field, e.g. `19/05/2025`."""
    if key not in DATE_FIELDS or not value:
        return None

    for pattern, order in DATE_PATTERNS:
        match = pattern.search(value)
        if match is None:
            continue
        parts = dict(zip(order, match.groups(), strict=True))
        try:
            return datetime(
                int(parts["year"]), int(parts["month"]), int(parts["day"]), tzinfo=UTC
            )
        except ValueError:
            return None
    return None


async def _already_extracted(
    session: AsyncSession, case_id: uuid.UUID, signature: str
) -> bool:
    existing = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ExtractedFact)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.extraction_signature == signature,
        )
    )
    return bool(existing)


async def latest_machine_facts(
    session: AsyncSession, *, case_id: uuid.UUID
) -> list[ExtractedFact]:
    """Every fact from the most recent machine reading of this case.

    A conflicting field contributes several rows — one per value read — because that is
    what the disagreement consists of.
    """
    newest_signature = (
        sa.select(ExtractedFact.extraction_signature)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.extraction_signature.is_not(None),
        )
        .order_by(ExtractedFact.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )

    result = await session.execute(
        sa.select(ExtractedFact)
        .where(
            ExtractedFact.case_id == case_id,
            ExtractedFact.origin == FactOrigin.machine,
            ExtractedFact.extraction_signature == newest_signature,
        )
        .order_by(ExtractedFact.fact_type, ExtractedFact.created_at)
    )
    return list(result.scalars().all())
