"""Stage 1, last step: a section becomes passages a reader can be handed.

A section runs from a sentence to several pages, and retrieval returns passages, so sections have
to be cut. The cut is where the damage happens.

**A table is never cut.** Measured on corpus v4.0: 20 tables are split across consecutive
passages of the same section, and one of them is the TNM staging table — one passage ends at T3b
and the next begins at T4, neither carrying the column headings. Retrieved on its own, that
passage offers stage definitions with nothing to read them against. So a table is an atom here:
it is placed whole or it is placed alone, and a table larger than the size limit stays oversized
rather than becoming two useless halves. An oversized passage is a retrieval inefficiency; a
halved decision table is a clinical error waiting for someone to cite it.

Everything else is cut at the largest boundary that fits — paragraphs first, sentences only when
a single paragraph will not fit on its own. Splitting mid-sentence is never done: the fragment
that results reads as guidance and is missing its condition.

**The limits are measured, not chosen.** Corpus v4.0 runs from 100 to 3,598 characters with a
median of 1,241, so `MAX_CHARS` is 3,600 and `MIN_CHARS` is 100 — the same envelope, which keeps
the rebuilt corpus comparable to the one the retrieval evaluation will be scored against.

Each passage says whether it is prose or a table, because they are retrieved differently: a
table's numbers are exact tokens that full-text search handles well and an embedding destroys,
which is §4.4 of the build plan one layer down.
"""

import re
from dataclasses import dataclass
from typing import Literal

# Measured from rcpia-corpus-v4.0: min 100, max 3,598, median 1,241.
MAX_CHARS = 3600
MIN_CHARS = 100

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")

# A reference list rendered as a table: `| [1] | Carioli G., Bertuccio P., ... |`. AFU's captures
# end with one and it carries no heading, so the heading-based detector corpus v4.0 used cannot
# see it -- v4.0 avoided it instead by hard-coded block ranges. Matching the shape finds exactly
# one per AFU document and nothing else among 96 table atoms.
#
# It is flagged rather than dropped. The release schema already carries
# `retrieval_exclusion_reason: bibliography_entry`, and a passage excluded with a reason and a
# passage that silently vanished must not look the same.
_REFERENCE_ROW = re.compile(r"^\|\s*\[\d+\]")
MIN_REFERENCE_ROWS = 5

PassageKind = Literal["prose", "table", "mixed", "references"]


@dataclass(frozen=True)
class Passage:
    text: str
    kind: PassageKind
    # The budget this passage was cut against. Carried rather than assumed: a passage compared to
    # the module default would report the wrong answer whenever a caller passes its own limit,
    # and finding the tables that could not fit is the whole point of the flag.
    limit: int = MAX_CHARS

    @property
    def is_oversized(self) -> bool:
        """A table too large to fit, kept whole deliberately. Never true for prose."""
        return len(self.text) > self.limit


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


@dataclass(frozen=True)
class _Atom:
    text: str
    kind: PassageKind


def atoms(text: str) -> list[_Atom]:
    """The largest pieces that must not be broken: whole tables, and paragraphs.

    Consecutive table lines are one atom however many rows they run to, which is the whole point
    of this function.
    """
    out: list[_Atom] = []
    table: list[str] = []
    prose: list[str] = []

    def flush_table() -> None:
        if not table:
            return
        every_row_numbered = all(_REFERENCE_ROW.match(line) for line in table)
        kind: PassageKind = (
            "references"
            if every_row_numbered and len(table) >= MIN_REFERENCE_ROWS
            else "table"
        )
        out.append(_Atom("\n".join(table), kind))
        table.clear()

    def flush_prose() -> None:
        joined = "\n".join(prose).strip()
        if joined:
            out.append(_Atom(joined, "prose"))
        prose.clear()

    for line in text.splitlines():
        if _is_table_line(line):
            flush_prose()
            table.append(line)
            continue
        flush_table()
        if line.strip():
            prose.append(line)
        else:
            flush_prose()
    flush_table()
    flush_prose()
    return out


def _split_prose(text: str, max_chars: int) -> list[str]:
    """Cut a paragraph too large to place, at sentence ends only."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in _SENTENCE_END.split(text):
        if current and size + len(sentence) + 1 > max_chars:
            parts.append(" ".join(current).strip())
            current, size = [], 0
        current.append(sentence)
        size += len(sentence) + 1
    if current:
        parts.append(" ".join(current).strip())
    # A single sentence longer than the limit is left long. Cutting it would land mid-clause,
    # and a clause of a recommendation reads exactly like the recommendation.
    return [part for part in parts if part]


def _kind_of(kinds: list[PassageKind]) -> PassageKind:
    unique = set(kinds)
    if unique == {"table"}:
        return "table"
    if unique == {"prose"}:
        return "prose"
    return "mixed"


def passages(text: str, *, max_chars: int = MAX_CHARS, min_chars: int = MIN_CHARS) -> list[Passage]:
    """One section's text, cut into passages.

    A trailing passage below `min_chars` is folded back into the one before it rather than
    emitted: a 30-character orphan retrieves for nothing and dilutes whatever it does match. It
    is only emitted alone when it is the whole section, because a short section is a real section.
    """
    body = text.strip()
    if not body:
        return []

    pieces = atoms(body)
    out: list[Passage] = []
    current: list[str] = []
    kinds: list[PassageKind] = []
    size = 0

    def flush() -> None:
        nonlocal current, kinds, size
        joined = "\n".join(current).strip()
        if joined:
            out.append(Passage(text=joined, kind=_kind_of(kinds), limit=max_chars))
        current, kinds, size = [], [], 0

    for atom in pieces:
        if atom.kind == "references":
            # Stands alone, so the release can exclude it whole and name a reason.
            flush()
            out.append(Passage(text=atom.text, kind="references", limit=max_chars))
            continue

        if atom.kind == "table":
            # Placed whole, whatever it costs. Oversized beats halved.
            if current and size + len(atom.text) + 1 > max_chars:
                flush()
            current.append(atom.text)
            kinds.append("table")
            size += len(atom.text) + 1
            continue

        for part in _split_prose(atom.text, max_chars):
            if current and size + len(part) + 1 > max_chars:
                flush()
            current.append(part)
            kinds.append("prose")
            size += len(part) + 1

    flush()

    if len(out) > 1 and len(out[-1].text) < min_chars:
        tail = out.pop()
        previous = out[-1]
        out[-1] = Passage(
            text=f"{previous.text}\n{tail.text}",
            kind=_kind_of([previous.kind, tail.kind]),
            limit=max_chars,
        )
    return out
