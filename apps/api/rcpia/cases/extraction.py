"""Extracting clinical facts from parsed dossier text.

Reference dossiers are structured French forms — `Créatinine : 17.33` — so extraction is
deterministic label matching rather than a language model. That matters here: every fact
must quote the exact text it came from, and a rule that matched a line can say precisely
which line. Free-text extraction can be layered on later for the parts that resist this,
but it must produce the same provenance.

Nothing in this module touches the database; it turns chunks into candidate facts, and
`rcpia.cases.facts` decides what to store.
"""

import re
import unicodedata
from dataclasses import dataclass

from rcpia.cases.fact_schema import (
    FIELDS,
    REPORTED_ABSENT,
    FactField,
    is_reported_absent,
)
from rcpia.core.models import CancerType, FactStatus

# "Label : value", tolerating the bold markers LlamaParse emits for form labels.
LABEL_LINE = re.compile(r"^\s*[*_#>\-\s]*(?P<label>[^:*_]{2,70}?)\s*[*_]*\s*:\s*(?P<value>.*)$")
MAX_VALUE_LENGTH = 4000


def normalize_label(label: str) -> str:
    """Case- and accent-insensitive, whitespace-collapsed."""
    decomposed = unicodedata.normalize("NFKD", label.strip().lower())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.replace("’", "'").split())


LABEL_INDEX: dict[str, FactField] = {}
for field_spec in FIELDS:
    for label in field_spec.labels:
        LABEL_INDEX.setdefault(normalize_label(label), field_spec)


@dataclass(frozen=True)
class FactCandidate:
    """A fact found in one chunk, with the text that produced it."""

    key: str
    value: str | None
    excerpt: str
    chunk_id: object | None = None
    document_id: object | None = None
    reported_absent: bool = False


def extract_from_text(
    text: str,
    *,
    chunk_id: object | None = None,
    document_id: object | None = None,
) -> list[FactCandidate]:
    """Find every recognised field in one chunk of dossier text.

    A label whose value is empty takes the following lines as its value, which is how
    the reference dossiers write imaging reports: the label on one line, the report
    beneath it.
    """
    lines = text.splitlines()
    candidates: list[FactCandidate] = []

    for index, line in enumerate(lines):
        match = LABEL_LINE.match(line)
        if match is None:
            continue

        field_spec = LABEL_INDEX.get(normalize_label(match.group("label")))
        if field_spec is None:
            continue

        value = match.group("value").strip().strip("*").strip()
        excerpt = line.strip()

        if not value:
            continuation, consumed = _read_continuation(lines, index + 1)
            if continuation:
                value = continuation
                excerpt = "\n".join([line.strip(), *consumed]).strip()

        if not value:
            # A label with nothing after it says nothing; treat the field as absent
            # rather than inventing an empty value.
            continue

        candidates.append(
            FactCandidate(
                key=field_spec.key,
                value=REPORTED_ABSENT if is_reported_absent(value) else value[:MAX_VALUE_LENGTH],
                excerpt=excerpt[:MAX_VALUE_LENGTH],
                chunk_id=chunk_id,
                document_id=document_id,
                reported_absent=is_reported_absent(value),
            )
        )

    return candidates


def _read_continuation(lines: list[str], start: int) -> tuple[str, list[str]]:
    """Collect the lines under a bare label, stopping at the next recognised label."""
    collected: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        match = LABEL_LINE.match(line)
        if match is not None and normalize_label(match.group("label")) in LABEL_INDEX:
            break
        collected.append(stripped)
        if sum(len(item) for item in collected) > MAX_VALUE_LENGTH:
            break

    return " ".join(collected)[:MAX_VALUE_LENGTH], collected


def normalize_value(value: str | None) -> str:
    """For comparing two readings of the same field."""
    if value is None:
        return ""
    return " ".join(value.strip().lower().split())


@dataclass(frozen=True)
class ResolvedFact:
    """What extraction concluded about one field, before it is written down."""

    key: str
    value: str | None
    status: FactStatus
    excerpt: str | None
    chunk_id: object | None
    document_id: object | None
    conflicting_values: tuple[str, ...] = ()


def resolve(
    candidates: list[FactCandidate], *, cancer_type: CancerType
) -> list[ResolvedFact]:
    """Turn candidates into one conclusion per field, including the fields nobody filled in.

    Three outcomes matter clinically and are kept apart: a value that was read, a field
    the clinician marked `RAS`, and a field that is simply absent. Two different readings
    of the same field are reported as conflicting rather than silently picking one — a
    PSA of 12.4 and 124 must never be resolved by a coin toss.
    """
    by_key: dict[str, list[FactCandidate]] = {}
    for candidate in candidates:
        by_key.setdefault(candidate.key, []).append(candidate)

    resolved: list[ResolvedFact] = []

    for field_spec in FIELDS:
        if not field_spec.applies_to(cancer_type):
            continue

        found = by_key.get(field_spec.key, [])
        if not found:
            resolved.append(
                ResolvedFact(
                    key=field_spec.key,
                    value=None,
                    status=FactStatus.missing,
                    excerpt=None,
                    chunk_id=None,
                    document_id=None,
                )
            )
            continue

        distinct = {normalize_value(candidate.value) for candidate in found}
        first = found[0]

        if len(distinct) > 1:
            resolved.append(
                ResolvedFact(
                    key=field_spec.key,
                    value=first.value,
                    status=FactStatus.conflicting,
                    excerpt=first.excerpt,
                    chunk_id=first.chunk_id,
                    document_id=first.document_id,
                    conflicting_values=tuple(
                        sorted({candidate.value or "" for candidate in found})
                    ),
                )
            )
            continue

        resolved.append(
            ResolvedFact(
                key=field_spec.key,
                value=first.value,
                status=FactStatus.confirmed,
                excerpt=first.excerpt,
                chunk_id=first.chunk_id,
                document_id=first.document_id,
            )
        )

    return resolved
