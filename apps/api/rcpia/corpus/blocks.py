"""Stage 1, second reader: a guideline captured from a web page rather than a PDF.

The three AFU guidelines are Urofrance captures. Their `document.json` holds 487 blocks of two
kinds -- `paragraph` and `table` -- and **no headings at all**. What serves as a heading is a
table with exactly one cell in it: `Épidémiologie`, `À qui proposer une détection précoce ?`.
Those single-cell tables are the section names that the current corpus is routed by, so the
structure is real; it simply does not announce itself the way markdown does.

**And nothing in the file says which heading outranks which.** `Épidémiologie, facteurs de
risque, prévention` is a chapter and `Epidémiologie` is a section inside it, and the two blocks
are indistinguishable in shape, length or position. The document's first label is `Plan`, which
raises hopes of a table of contents and is immediately followed by body text.

So the ranking is declared, per document, in `corpus/structure/`. That is the same decision the
routing rules made one stage later: a fact about a guideline that a person has to supply belongs
in a file a person can review, not in a branch inside a build script. The declarations here were
derived from the section paths that corpus v4.0 already carries, so they record what the
hand-written builder did rather than a fresh opinion about it.

A label that is neither a declared chapter nor a declared section becomes a third level beneath
whatever is open. That is the common case -- most labels are leaves -- and it means a declaration
lists only the ranks that outrank something, not every heading in the document.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rcpia.corpus.prepare import PrepareError, Section

# Above this a single cell is a paragraph that happens to sit alone in a table, not a heading.
# Labels that name a numbered exhibit are allowed to run longer, because AFU writes the whole
# caption into the cell: "Tableau III - Stratification et primo traitement des TVNIM incidentes."
MAX_LABEL_CHARS = 180
MAX_EXHIBIT_LABEL_CHARS = 500
EXHIBIT_PREFIXES = ("Tableau ", "Figure ", "Recommandation ", "Légende :")


class BlockError(PrepareError):
    """A block document or its structure declaration cannot be read."""


@dataclass(frozen=True)
class Structure:
    """Which labels of one document are chapters, and which are sections within them."""

    document_id: str
    majors: tuple[str, ...]
    topics_by_major: dict[str, tuple[str, ...]]
    excluded: frozenset[str]
    # The chapter that collects numbered exhibits. AFU prints its tables where they are
    # discussed but files them at the back, so `Tableau III` appears mid-chapter and belongs to
    # the appendix. Verified across all three documents: every one of the 25 exhibit paths in
    # corpus v4.0 sits under this chapter and none sits anywhere else.
    appendix_major: str | None = None
    note: str = ""

    def rank_of(self, label: str, *, major: str | None) -> int:
        """1 for a chapter, 2 for a section of the open chapter, 3 for anything below."""
        if label in self.majors:
            return 1
        if major is not None and label in self.topics_by_major.get(major, ()):
            return 2
        return 3


def one_cell_label(block: dict[str, Any]) -> str | None:
    """The heading a block carries, if it is one.

    A heading here is a table of one row and one non-empty cell. The length caps are what keep a
    one-sentence callout in a single-cell table from being read as a chapter title.
    """
    if block.get("type") != "table":
        return None
    rows = block.get("rows") or []
    if len(rows) != 1:
        return None
    cells = [str(cell).strip() for cell in rows[0] if str(cell).strip()]
    if len(cells) != 1:
        return None
    label = cells[0]
    limit = MAX_EXHIBIT_LABEL_CHARS if label.startswith(EXHIBIT_PREFIXES) else MAX_LABEL_CHARS
    return label if len(label) <= limit else None


def block_text(block: dict[str, Any]) -> str:
    """A block as text. Tables keep their shape; a table flattened to a sentence is a table lost."""
    if block.get("type") == "paragraph":
        return str(block.get("text") or "").strip()
    rows = block.get("rows") or []
    lines = []
    for row in rows:
        cells = [str(cell).replace("|", r"\|").replace("\n", " ").strip() for cell in row]
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def parse_structure(document: Any, *, source: str = "structure") -> Structure:
    if not isinstance(document, dict):
        raise BlockError(f"{source} must be an object")
    known = {"document_id", "majors", "topics_by_major", "excluded", "appendix_major", "note"}
    unknown = sorted(set(document) - known)
    if unknown:
        raise BlockError(
            f"{source} carries fields this reader does not implement: {', '.join(unknown)}"
        )

    document_id = document.get("document_id")
    if not isinstance(document_id, str) or not document_id.strip():
        raise BlockError(f"{source} needs a non-empty `document_id`")

    majors = document.get("majors")
    if not isinstance(majors, list) or not all(isinstance(m, str) for m in majors):
        raise BlockError(f"{document_id}: `majors` must be a list of strings")
    if not majors:
        raise BlockError(
            f"{document_id} declares no chapters. Every label would then rank the same and the "
            "document would come out flat, which is what this reader exists to avoid."
        )

    topics = document.get("topics_by_major") or {}
    if not isinstance(topics, dict):
        raise BlockError(f"{document_id}: `topics_by_major` must be an object")
    unplaced = sorted(set(topics) - set(majors))
    if unplaced:
        raise BlockError(
            f"{document_id}: topics are listed under {', '.join(unplaced)}, which is not a "
            "declared chapter. A section beneath a chapter that does not exist can never fire."
        )

    excluded = document.get("excluded") or []
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise BlockError(f"{document_id}: `excluded` must be a list of strings")

    appendix = document.get("appendix_major")
    if appendix is not None and appendix not in majors:
        raise BlockError(
            f"{document_id}: `appendix_major` is {appendix!r}, which is not a declared chapter. "
            "Exhibits would be filed under a chapter that never opens."
        )

    return Structure(
        document_id=document_id,
        majors=tuple(majors),
        topics_by_major={k: tuple(v) for k, v in topics.items()},
        excluded=frozenset(excluded),
        appendix_major=appendix,
        note=document.get("note") or "",
    )


def load_structure(path: Path) -> Structure:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BlockError(f"{path.name} is not valid JSON: {exc}") from exc
    return parse_structure(document, source=path.name)


def structure_path(directory: Path, document_id: str) -> Path:
    return directory / f"{document_id}.structure.json"


def read_blocks(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BlockError(f"{path.name} is not valid JSON: {exc}") from exc
    blocks = document.get("blocks")
    if not isinstance(blocks, list):
        raise BlockError(f"{path.name} has no `blocks` array, so it is not a captured document")
    return [b for b in blocks if isinstance(b, dict)]


def outline_blocks(blocks: list[dict[str, Any]], structure: Structure) -> list[Section]:
    """Every section of a captured document, with the path each one sits at.

    Line numbers are block indices here rather than lines of a file: a captured document has no
    lines, and the block index is what a reader can actually go and look at.
    """
    sections: list[Section] = []
    major: str | None = None
    topic: str | None = None
    leaf: str | None = None
    open_path: tuple[str, ...] | None = None
    body: list[str] = []
    start = 0

    def close(end: int) -> None:
        nonlocal body, open_path
        text = "\n\n".join(part for part in body if part).strip()
        # A body that is nothing but an exhibit label -- "Recommandation 4" and no more -- is the
        # caption of a table printed elsewhere, not a passage. Judged by what the text is rather
        # than by a minimum length: a guessed character floor would also drop the three real
        # one-sentence passages this reader recovers, one of which carries a PSA threshold.
        if text.startswith(EXHIBIT_PREFIXES) and "\n" not in text:
            text = ""
        if open_path is not None and text:
            sections.append(
                Section(section_path=open_path, text=text, line_start=start, line_end=end)
            )
        body = []

    for index, block in enumerate(blocks):
        label = one_cell_label(block)
        if label is None:
            body.append(block_text(block))
            continue
        if label in structure.excluded:
            continue

        close(index - 1)
        if structure.appendix_major is not None and label.startswith(EXHIBIT_PREFIXES):
            # An exhibit is filed at the back whatever chapter it was printed in.
            major, topic, leaf = structure.appendix_major, None, label
            open_path = (major, label)
            start = index
            continue

        rank = structure.rank_of(label, major=major)
        if rank == 1:
            major, topic, leaf = label, None, None
        elif rank == 2:
            topic, leaf = label, None
        else:
            leaf = label

        open_path = tuple(
            part for part in (major, topic, leaf) if part is not None
        ) or (label,)
        start = index

    close(len(blocks) - 1)
    return sections
