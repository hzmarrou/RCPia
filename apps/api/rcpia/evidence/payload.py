"""Whole stored evidence units, with one bounded preparation policy for both consumers.

Compaction preserves Markdown cells, not original bytes. It never establishes that a
stored unit contains an entire publication table or qualifications from another unit.
"""

import re
from dataclasses import dataclass
from typing import Literal

MAX_EVIDENCE_CHARACTERS = 8000

_DELIMITER = re.compile(r":?-{3,}:?")
_BREAK = re.compile(r"<br */?>", re.IGNORECASE)
_HTML = re.compile(r"<[/!?a-z]", re.IGNORECASE)
_REFERENCE_DEFINITION = re.compile(r"\[[^\[\]]+\]:")


class EvidencePreparationError(ValueError):
    """A stored unit cannot be delivered intact under the evidence contract."""


@dataclass(frozen=True)
class PreparedEvidence:
    text: str
    transformation: Literal["verbatim", "markdown_cell_padding"]


def _line_parts(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[:-len(ending)], ending
    return line, ""


def _compact_padding(text: str) -> str:
    # A partial Markdown/HTML parser cannot safely decide where multiline code ends.
    # Reference titles can also span pipe-shaped lines without creating a table.
    # Refuse whole-unit ambiguity; only inline HTML breaks are explicitly supported.
    if (
        "`" in text
        or "\\" in text
        or "~~~" in text
        or _HTML.search(_BREAK.sub("", text))
        or _REFERENCE_DEFINITION.search(text)
    ):
        return text

    lines = text.splitlines(keepends=True)
    result: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("|"):
            result.append(lines[index])
            index += 1
            continue

        block: list[tuple[list[str], str]] = []
        while index < len(lines) and lines[index].startswith("|"):
            body, ending = _line_parts(lines[index])
            if not body.endswith("|") or "\t" in body:
                return text
            block.append((body.split("|")[1:-1], ending))
            index += 1
        if (
            len(block) < 2
            or not block[0][0]
            or any(len(cells) != len(block[0][0]) for cells, _ in block)
            or not all(_DELIMITER.fullmatch(cell.strip(" ")) for cell in block[1][0])
        ):
            return text

        for cells, ending in block:
            result.append("|" + "|".join(cell.strip(" ") for cell in cells) + "|" + ending)
    return "".join(result)


def prepare_evidence(contextual_text: str) -> PreparedEvidence:
    """Verbatim when it fits; otherwise compact only recognized table-cell padding.

    The limit includes the original context prefix. All candidates must pass this
    check before a reranker request; oversized units are never scored as fragments.
    """
    if len(contextual_text) <= MAX_EVIDENCE_CHARACTERS:
        return PreparedEvidence(contextual_text, "verbatim")
    compacted = _compact_padding(contextual_text)
    if len(compacted) > MAX_EVIDENCE_CHARACTERS:
        raise EvidencePreparationError(
            "Un passage des recommandations depasse la limite de "
            f"{MAX_EVIDENCE_CHARACTERS} caracteres apres compactage autorise "
            f"({len(compacted)} caracteres). Analyse arretee : le passage ne peut pas "
            "etre transmis integralement ; aucune troncature ni omission n'est autorisee."
        )
    return PreparedEvidence(compacted, "markdown_cell_padding")
