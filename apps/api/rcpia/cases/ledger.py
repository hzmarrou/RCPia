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

from rcpia.cases.fact_schema import FIELDS_BY_KEY, REPORTED_ABSENT, fields_for
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
    fact_id: str | None = None
    corrected: bool = False


def highlight_for(status: FactStatus) -> str:
    if status is FactStatus.missing:
        return RED
    if status in (FactStatus.uncertain, FactStatus.conflicting):
        return AMBER
    return NEUTRAL


def display_label(key: str) -> str:
    spec = FIELDS_BY_KEY.get(key)
    if spec is None:
        return key
    label = spec.labels[0]
    return label[:1].upper() + label[1:]


async def current_facts(session: AsyncSession, *, case_id: uuid.UUID) -> dict[str, ExtractedFact]:
    """The fact that currently speaks for each field.

    A human correction supersedes the machine reading, and the most recent row wins
    within each origin — so a corrected field shows the correction, and an uncorrected
    one shows the latest extraction.
    """
    result = await session.execute(
        sa.select(ExtractedFact)
        .where(ExtractedFact.case_id == case_id)
        .order_by(ExtractedFact.created_at, ExtractedFact.id)
    )

    chosen: dict[str, ExtractedFact] = {}
    for fact in result.scalars().all():
        existing = chosen.get(fact.fact_type)
        if existing is None:
            chosen[fact.fact_type] = fact
            continue
        # A human correction outranks any machine reading; otherwise the newer row wins.
        if fact.origin is FactOrigin.human or existing.origin is not FactOrigin.human:
            chosen[fact.fact_type] = fact

    return chosen


async def build_entries(session: AsyncSession, *, case: Case) -> list[LedgerEntry]:
    facts = await current_facts(session, case_id=case.id)
    entries: list[LedgerEntry] = []

    for spec in fields_for(case.cancer_type):
        fact = facts.get(spec.key)
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

        entries.append(
            LedgerEntry(
                key=spec.key,
                label=display_label(spec.key),
                category=str(spec.category),
                value=fact.value,
                status=str(fact.status),
                origin=str(fact.origin),
                highlight=highlight_for(fact.status),
                unit=spec.unit,
                note=fact.note,
                source_excerpt=fact.source_excerpt,
                source_document_id=(
                    str(fact.source_document_id) if fact.source_document_id else None
                ),
                source_chunk_id=str(fact.source_chunk_id) if fact.source_chunk_id else None,
                fact_id=str(fact.id),
                corrected=fact.origin is FactOrigin.human,
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
