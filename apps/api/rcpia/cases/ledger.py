"""The case ledger.

The ledger is the normalized view of a case that agents read and clinicians correct. It
is derived, never edited in place: every entry points back at the fact it came from, and
a correction is a new fact superseding the old one. Snapshots are stored as versions, so
what the agents were given can always be reconstructed.

Colour follows status, and the distinction the whole design turns on:

- **red** — the field is missing or explicitly not recorded;
- **amber** — the value is uncertain, or the dossier says two different things;
- **plain** — a value was read, including `RAS`, which means the clinician looked.
"""

import uuid
from dataclasses import asdict, dataclass, replace

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases.fact_schema import (
    FIELDS_BY_KEY,
    REPORTED_ABSENT,
    absence_answers,
    fields_for,
    is_reported_absent,
    is_unknown_value,
)
from rcpia.core.models import (
    AuditEvent,
    Case,
    CaseLedgerVersion,
    CaseStatus,
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


def _entry_reading(entry: LedgerEntry) -> LedgerReading:
    return LedgerReading(
        value=entry.value, source_excerpt=entry.source_excerpt,
        source_document_id=entry.source_document_id, source_chunk_id=entry.source_chunk_id,
        source_chunk_ids=entry.source_chunk_ids, fact_id=entry.fact_id,
    )


def _unanswered(key: str, value: str | None) -> bool:
    return is_unknown_value(value) or (
        value is not None and is_reported_absent(value) and not absence_answers(key)
    )


def _current_readings(entry: LedgerEntry) -> tuple[LedgerReading, ...]:
    representative = _entry_reading(entry)
    if entry.origin == "human":
        return (representative,)
    readings = entry.readings or (representative,)
    if entry.value is not None and not any(r.value == entry.value for r in readings):
        readings = (representative, *readings)
    return readings


def effective_readings(entry: LedgerEntry) -> tuple[LedgerReading, ...]:
    """Current answers only; placeholders and superseded readings are provenance, not answers."""
    return tuple(r for r in _current_readings(entry) if not _unanswered(entry.key, r.value))


def project_entry(entry: LedgerEntry) -> LedgerEntry:
    """Apply current missingness semantics without rewriting a fact or stored version.

    The literal value survives in the note and readings, alongside its original citation.
    Returning a new entry also makes repeated projection safe for live and snapshot readers.
    """
    unanswered = _unanswered(entry.key, entry.value)
    current = _current_readings(entry)
    answered = effective_readings(entry)
    note = entry.note
    readings = entry.readings
    if unanswered and entry.value is not None:
        explanation = (
            f"Valeur source « {entry.value} » : information non renseignée, "
            "ni réponse négative ni résultat normal ; les autres lectures sont conservées."
            if is_unknown_value(entry.value)
            else (
                "Le formulaire porte « RAS » pour ce champ. « Rien à signaler » ne peut pas "
                "répondre à ce champ : la valeur est traitée comme absente."
            )
        )
        note = f"{note}\n{explanation}" if note else explanation
        if not any(r.value == entry.value for r in readings):
            readings = (_entry_reading(entry), *readings)
    if not answered:
        return replace(
            entry, value=None, status=str(FactStatus.missing), highlight=RED,
            note=note, readings=readings,
        )

    status, highlight = entry.status, entry.highlight
    distinct = {" ".join((r.value or "").casefold().split()) for r in answered}
    if len(distinct) > 1:
        status, highlight = str(FactStatus.conflicting), AMBER
    elif status == str(FactStatus.conflicting) and any(
        _unanswered(entry.key, r.value) for r in current
    ):
        # The sole answer only disagreed with a blank box, not another answer.
        status, highlight = str(FactStatus.confirmed), NEUTRAL
    elif unanswered and status == str(FactStatus.missing):
        # A legacy missing status cannot attest that the surviving reading was confirmed.
        status, highlight = str(FactStatus.uncertain), AMBER

    if unanswered:
        representative = answered[0]
        entry = replace(
            entry, value=representative.value, source_excerpt=representative.source_excerpt,
            source_document_id=representative.source_document_id,
            source_chunk_id=representative.source_chunk_id,
            source_chunk_ids=representative.source_chunk_ids, fact_id=representative.fact_id,
        )
        if not readings:
            readings = current
    return replace(
        entry, status=status, highlight=highlight, note=note, readings=readings,
    )


# Fields whose label is an acronym or has internal capitals. Capitalising the first
# letter of the matching label turns `ptnm` into `Ptnm` and `oms` into `Oms`, which reads
# as a typo to the clinicians these labels are for.
DISPLAY_OVERRIDES: dict[str, str] = {
    # Asked as a question, because it is one, and because the clinician answering it is what
    # authorises the two prostate nomograms to compute anything at all.
    "prostate_adenocarcinoma_confirmed": (
        "Adénocarcinome prostatique confirmé histologiquement ?"
    ),
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
    # A recovered answer leads; retained not-recorded readings remain source history,
    # not competing answers. A human correction still outranks every machine reading.
    return {
        key: sorted(rows, key=lambda row: is_unknown_value(row.value))
        for key, rows in chosen.items()
    }


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

    # **A confirmation answers a histology, and stops answering when that histology changes.**
    # The clinician who confirmed adenocarcinoma did so having read a particular pathology
    # line; if that line is later corrected, their answer was given about something else and
    # cannot carry over. Resolved here, where both facts' timestamps are available, so the
    # stored version -- the thing agents and the fiche read -- already holds the right answer
    # rather than leaving every reader to work it out again.
    histology_rows = readings.get("histology") or []
    histology_recorded_at = histology_rows[0].created_at if histology_rows else None

    for spec in fields_for(case.cancer_type):
        rows = readings.get(spec.key) or []
        fact = rows[0] if rows else None

        if (
            fact is not None
            and spec.key == ADENOCARCINOMA_CONFIRMATION
            and histology_recorded_at is not None
            and fact.created_at is not None
            and histology_recorded_at > fact.created_at
        ):
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
                    note=(
                        "Le type histologique a été modifié après cette confirmation : "
                        "la question doit être posée à nouveau."
                    ),
                )
            )
            continue

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

    return [project_entry(entry) for entry in entries]


ADENOCARCINOMA_CONFIRMATION = "prostate_adenocarcinoma_confirmed"

# What the clinician's answer has to be, matched **whole** rather than as a substring.
#
# Substring matching is what this project has spent seven rounds of review paying for: an
# early version of this list accepted "confirmé", which matches inside "à confirmer" -- an
# answer meaning the opposite, authorising a calculator on a question still open. A yes/no
# question has two answers, so they are compared exactly and everything else is no answer.
_CONFIRMED = frozenset({"oui", "yes", "o"})
_DENIED = frozenset({"non", "no", "n"})


def adenocarcinoma_confirmation(entries: list[LedgerEntry]) -> str:
    """The clinician's answer to the eligibility question: `"yes"`, `"no"` or `"unknown"`.

    **Only a human's answer counts.** Extraction may fill this field in as a suggestion, and
    a suggestion is exactly what seven rounds of review showed cannot be trusted to authorise
    a calculator: the machine's reading of free text was defeated by a denial, a hedge, a
    search result, a percentage of zero. So the origin is checked before the value is, and a
    machine-origin answer -- however confident, however correct it may happen to be --
    authorises nothing.

    Returned as a string rather than a `Tristate` because the nomogram package owns that type
    and the ledger should not have to import it to answer a question about its own contents.
    """
    for entry in entries:
        if entry.key != ADENOCARCINOMA_CONFIRMATION:
            continue
        if entry.origin != str(FactOrigin.human):
            return "unknown"
        # Case and surrounding whitespace only. **Nothing is stripped from the answer
        # itself.** An earlier version also stripped `.!?`, which turned "Oui ?" -- a
        # clinician recording that they are not sure -- into an unqualified yes, and both
        # calculators duly computed. A punctuation mark a person took the trouble to type is
        # part of what they said, and the only safe reading of an answer this code does not
        # recognise is that the question is still open.
        answer = (entry.value or "").strip().lower()
        if answer in _CONFIRMED:
            return "yes"
        if answer in _DENIED:
            return "no"
        return "unknown"
    return "unknown"


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
    await session.execute(
        sa.select(Case.id).where(Case.id == case.id).with_for_update(key_share=True)
    )

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
    # A fiche over the previous ledger remains historical; it does not approve this version.
    await session.execute(
        sa.update(Case)
        .where(
            Case.id == case.id,
            Case.status.in_((CaseStatus.analyzing, CaseStatus.completed, CaseStatus.blocked)),
        )
        .values(status=CaseStatus.ready_for_review)
    )
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
        status=FactStatus.missing if is_unknown_value(value) else FactStatus.confirmed,
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
