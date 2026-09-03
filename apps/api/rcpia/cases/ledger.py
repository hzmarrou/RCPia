"""The case ledger.

The ledger is the normalized view of a case that agents read and clinicians correct. It
is derived, never edited in place: every entry points back at the fact it came from, and
a correction is a new fact superseding the old one. Snapshots are stored as versions, so
what the agents were given can always be reconstructed.

Colour follows status, and the distinction the whole design turns on:

- **red** — the field is missing, nobody recorded it;
- **amber** — the value is uncertain, or the dossier says two different things;
- **plain** — a value was read, including `RAS`, which means the clinician looked.
"""

import uuid
from dataclasses import asdict, dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases.fact_schema import (
    FIELDS_BY_KEY,
    REPORTED_ABSENT,
    absence_answers,
    fields_for,
    is_reported_absent,
)
from rcpia.core.models import (
    AuditEvent,
    Case,
    CaseLedgerVersion,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    User,
)

RED = "red"
AMBER = "amber"
NEUTRAL = "neutral"


class LedgerError(Exception):
    """Base class for ledger failures."""


class UnknownField(LedgerError):
    """The correction names a field RCPia does not know."""


class FieldNotInScope(LedgerError):
    """The field does not apply to this case's cancer type."""


@dataclass(frozen=True)
class LedgerReading:
    """One value read for a field, and where it was written."""

    value: str | None
    source_excerpt: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    # Every chunk `source_excerpt` was assembled from, `source_chunk_id` included. A
    # multi-paragraph imaging report spans several; naming only one meant a citation could
    # send a reviewer to a page that does not hold the sentence being quoted.
    source_chunk_ids: tuple[str, ...] = ()
    fact_id: str | None = None


@dataclass(frozen=True)
class LedgerEntry:
    key: str
    label: str
    category: str
    value: str | None
    status: str
    origin: str
    highlight: str
    unit: str | None = None
    note: str | None = None
    source_excerpt: str | None = None
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_chunk_ids: tuple[str, ...] = ()
    fact_id: str | None = None
    corrected: bool = False
    # Populated when the dossier says more than one thing: every reading with its own
    # source, so a clinician can see where each version was written.
    readings: tuple[LedgerReading, ...] = ()


def highlight_for(status: FactStatus) -> str:
    if status is FactStatus.missing:
        return RED
    if status in (FactStatus.uncertain, FactStatus.conflicting):
        return AMBER
    return NEUTRAL


# Fields whose label is an acronym or has internal capitals. Capitalising the first
# letter of the matching label turns `ptnm` into `Ptnm` and `oms` into `Oms`, which reads
# as a typo to the clinicians these labels are for.
DISPLAY_OVERRIDES: dict[str, str] = {
    "ptnm": "pTNM",
    "cis": "CIS",
    "who_status": "OMS",
    "g8_score": "Score G8",
    "mna": "Score MNA",
    "adl": "ADL",
    "ipss": "Score IPSS",
    "iief5": "Score IIEF5",
    "pallia10": "Pallia 10",
    "psa_total": "PSA total",
    "psa_free": "PSA libre",
    "egfr": "DFG",
    "ct_tap": "TDM TAP",
    "urine_culture": "ECBU",
    "crp": "CRP",
    "ldh": "LDH",
    "cpk": "CPK",
    "alat": "ALAT",
    "asat": "ASAT",
    "prothrombin": "TP",
    "haemoglobin": "Hb",
    "prostate_mri": "IRM prostatique multiparamétrique",
}


def display_label(key: str) -> str:
    override = DISPLAY_OVERRIDES.get(key)
    if override:
        return override

    spec = FIELDS_BY_KEY.get(key)
    if spec is None:
        return key
    label = spec.labels[0]
    return label[:1].upper() + label[1:]


async def current_readings(
    session: AsyncSession, *, case_id: uuid.UUID
) -> dict[str, list[ExtractedFact]]:
    """Every fact that currently speaks for each field.

    Usually one. A field the dossier contradicts itself about keeps them all, because
    the disagreement *is* the information — collapsing it to the first value read would
    hide exactly what a clinician needs to settle.

    A human correction outranks the machine entirely: once someone has decided, the case
    shows their decision rather than the argument behind it.
    """
    result = await session.execute(
        sa.select(ExtractedFact)
        .where(ExtractedFact.case_id == case_id)
        .order_by(ExtractedFact.created_at, ExtractedFact.id)
    )

    machine: dict[str, tuple[str | None, list[ExtractedFact]]] = {}
    human: dict[str, ExtractedFact] = {}

    for fact in result.scalars().all():
        if fact.origin is FactOrigin.human:
            human[fact.fact_type] = fact
            continue
        # Facts from one extraction share a signature; a newer reading replaces the
        # older one wholesale rather than being merged with it.
        generation, rows = machine.get(fact.fact_type, (None, []))
        if generation is not None and fact.extraction_signature != generation:
            rows = []
        rows.append(fact)
        machine[fact.fact_type] = (fact.extraction_signature, rows)

    chosen: dict[str, list[ExtractedFact]] = {
        key: rows for key, (_, rows) in machine.items()
    }
    for key, correction in human.items():
        chosen[key] = [correction]
    return chosen


async def current_facts(session: AsyncSession, *, case_id: uuid.UUID) -> dict[str, ExtractedFact]:
    """The single fact that currently speaks for each field, for callers needing one."""
    return {
        key: rows[0]
        for key, rows in (await current_readings(session, case_id=case_id)).items()
        if rows
    }


async def build_entries(session: AsyncSession, *, case: Case) -> list[LedgerEntry]:
    readings = await current_readings(session, case_id=case.id)
    entries: list[LedgerEntry] = []

    for spec in fields_for(case.cancer_type):
        rows = readings.get(spec.key) or []
        fact = rows[0] if rows else None
        if fact is None:
            entries.append(
                LedgerEntry(
                    key=spec.key,
                    label=display_label(spec.key),
                    category=str(spec.category),
                    value=None,
                    status=str(FactStatus.missing),
                    origin=str(FactOrigin.machine),
                    highlight=RED,
                    unit=spec.unit,
                )
            )
            continue

        # `RAS` on a field where absence cannot be an answer is an empty box, not a finding.
        # Recorded as missing so the query builder, the completeness check, the agents and the
        # rendered page all read it the same way -- and the form's own wording is kept in the
        # note, because "the box said RAS" and "nobody filled the box" are different facts about
        # the dossier even though both leave the value unknown.
        blank_box = (
            fact.value is not None
            and is_reported_absent(fact.value)
            and not absence_answers(spec.key)
        )
        note = fact.note
        if blank_box:
            note = (
                "Le formulaire porte « RAS » pour ce champ. « Rien à signaler » ne peut pas "
                "répondre à un type histologique, un grade ou un stade : la valeur est traitée "
                "comme absente."
            )

        entries.append(
            LedgerEntry(
                key=spec.key,
                label=display_label(spec.key),
                category=str(spec.category),
                value=None if blank_box else fact.value,
                status=str(FactStatus.missing) if blank_box else str(fact.status),
                origin=str(fact.origin),
                highlight=RED if blank_box else highlight_for(fact.status),
                unit=spec.unit,
                note=note,
                source_excerpt=fact.source_excerpt,
                source_document_id=(
                    str(fact.source_document_id) if fact.source_document_id else None
                ),
                source_chunk_id=str(fact.source_chunk_id) if fact.source_chunk_id else None,
                source_chunk_ids=tuple(str(cid) for cid in (fact.source_chunk_ids or ())),
                fact_id=str(fact.id),
                corrected=fact.origin is FactOrigin.human,
                readings=tuple(
                    LedgerReading(
                        value=row.value,
                        source_excerpt=row.source_excerpt,
                        source_document_id=(
                            str(row.source_document_id) if row.source_document_id else None
                        ),
                        source_chunk_id=(
                            str(row.source_chunk_id) if row.source_chunk_id else None
                        ),
                        source_chunk_ids=tuple(
                            str(cid) for cid in (row.source_chunk_ids or ())
                        ),
                        fact_id=str(row.id),
                    )
                    for row in rows
                ),
            )
        )

    return entries


def missing_keys(entries: list[LedgerEntry]) -> list[str]:
    return [entry.key for entry in entries if entry.highlight == RED]


def uncertain_keys(entries: list[LedgerEntry]) -> list[str]:
    return [entry.key for entry in entries if entry.highlight == AMBER]


async def record_version(
    session: AsyncSession,
    *,
    case: Case,
    change_reason: str,
    user: User | None = None,
) -> CaseLedgerVersion:
    """Snapshot the ledger as a new version.

    Versions are immutable, so the input any agent run was given can be reproduced
    exactly, however many corrections followed.
    """
    # Lock the case first: two clinicians correcting different fields at the same moment
    # would otherwise both compute the same next version number, and one would lose its
    # correction to a unique-constraint violation.
    await session.execute(sa.select(Case.id).where(Case.id == case.id).with_for_update())

    entries = await build_entries(session, case=case)
    highest = await session.scalar(
        sa.select(sa.func.max(CaseLedgerVersion.version)).where(
            CaseLedgerVersion.case_id == case.id
        )
    )

    version = CaseLedgerVersion(
        case_id=case.id,
        organization_id=case.organization_id,
        version=1 if highest is None else int(highest) + 1,
        ledger={"entries": [asdict(entry) for entry in entries]},
        missing_fields=missing_keys(entries),
        uncertain_fields=uncertain_keys(entries),
        change_reason=change_reason,
        created_by_user_id=user.id if user else None,
    )
    session.add(version)
    await session.flush()
    return version


async def latest_version(
    session: AsyncSession, *, case_id: uuid.UUID
) -> CaseLedgerVersion | None:
    return await session.scalar(
        sa.select(CaseLedgerVersion)
        .where(CaseLedgerVersion.case_id == case_id)
        .order_by(CaseLedgerVersion.version.desc())
        .limit(1)
    )


async def apply_correction(
    session: AsyncSession,
    *,
    case: Case,
    user: User,
    fact_type: str,
    value: str | None,
    note: str,
) -> ExtractedFact:
    """Record a human correction as a new fact superseding the current one.

    The machine's reading is never overwritten. A reviewer looking at the case later can
    see what was extracted, what a clinician changed it to, and why.
    """
    spec = FIELDS_BY_KEY.get(fact_type)
    if spec is None:
        raise UnknownField(fact_type)
    if not spec.applies_to(case.cancer_type):
        raise FieldNotInScope(fact_type)

    # Correcting a contradictory field supersedes the first reading; the others remain
    # on record, and the correction is what the ledger shows from now on.
    facts = await current_facts(session, case_id=case.id)
    superseded = facts.get(fact_type)

    correction = ExtractedFact(
        case_id=case.id,
        organization_id=case.organization_id,
        fact_type=fact_type,
        value=value,
        status=FactStatus.missing if value is None else FactStatus.confirmed,
        origin=FactOrigin.human,
        supersedes_fact_id=superseded.id if superseded is not None else None,
        recorded_by_user_id=user.id,
        note=note,
    )
    session.add(correction)

    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type="ledger.corrected",
            entity_type="extracted_facts",
            entity_id=correction.id,
            payload={
                "fact_type": fact_type,
                "previous_value": superseded.value if superseded else None,
                "new_value": value,
                "note": note,
            },
        )
    )

    await session.flush()
    return correction


def reported_absent(entry: LedgerEntry) -> bool:
    return (entry.value or "").strip().upper() == REPORTED_ABSENT
