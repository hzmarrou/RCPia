"""A document's headings, extracted once so rules can be checked without the sources.

**The problem this solves is where the check runs.** A routing rule is only meaningful against
the headings of the document it routes, and those live in OneDrive — 18.7 MB of markdown that
CI cannot reach and should not carry. So a rule file could be committed with a heading that was
renamed two guideline revisions ago, and nothing in the pipeline would say so until someone
rebuilt the corpus by hand.

Extracting the headings once, into a small file committed beside the rules, moves that check
into CI. Measured on the ten documents currently loaded: 1,031 distinct section paths, from 32
for SIOG prostate to 377 for EAU prostate — plain text, small enough to commit, and enough for
`rules.audit` to answer every question it asks.

**Order and duplicates are preserved deliberately.** A compressed set of distinct paths would be
smaller and would silently destroy the one thing `occurrence` exists for: the same heading
appearing twice with different content beneath it. `FOCUS` under two separate fiches is not one
heading, and a fixture that says it is would make an ambiguous rule look clean.

The format is JSONL — one path array per line — rather than a delimited text file, because
headings contain every character a delimiter might use, including `>`, `|` and `:`.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from rcpia.corpus.rules import Audit, RoutingRules, audit

SECTION_FILE_SUFFIX = ".sections.jsonl"


class SectionError(Exception):
    """A section fixture cannot be read, or does not describe headings."""


def section_paths(chunks: Iterable[dict[str, object]]) -> list[list[str]]:
    """Every chunk's `section_path`, in the order the chunks appear.

    Not deduplicated. See the module docstring: the repetitions are load-bearing.
    """
    out: list[list[str]] = []
    for index, chunk in enumerate(chunks):
        path = chunk.get("section_path")
        if path is None:
            raise SectionError(f"chunk {index} has no section_path; it cannot be routed")
        if not isinstance(path, list) or not all(isinstance(part, str) for part in path):
            raise SectionError(
                f"chunk {index} has section_path {path!r}; it must be a list of strings"
            )
        out.append(list(path))
    return out


def read_chunks(path: Path) -> Iterator[dict[str, object]]:
    """Stream `chunks.jsonl`. Streamed because a release is tens of megabytes."""
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SectionError(f"{path.name} line {number} is not valid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise SectionError(f"{path.name} line {number} is not an object")
            yield record


def write_fixture(paths: list[list[str]], destination: Path) -> None:
    """Write the headings a rule file will be checked against.

    One JSON array per line, so a heading containing any punctuation survives round-tripping,
    and so a diff on the file reads as a diff on the document's structure — which is exactly
    what a guideline revision produces.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for path in paths:
            handle.write(json.dumps(path, ensure_ascii=False) + "\n")


def read_fixture(source: Path) -> list[list[str]]:
    if not source.exists():
        raise SectionError(
            f"{source.name} is missing. Rules for this document cannot be checked against "
            "anything, so a renamed heading would pass review — extract it from the release "
            "with `write_fixture` and commit it beside the rules."
        )
    out: list[list[str]] = []
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            path = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SectionError(f"{source.name} line {number} is not valid JSON: {exc}") from exc
        if not isinstance(path, list) or not all(isinstance(part, str) for part in path):
            raise SectionError(
                f"{source.name} line {number} is {path!r}; each line must be a list of strings"
            )
        out.append(path)
    if not out:
        raise SectionError(
            f"{source.name} holds no headings. An empty fixture makes every rule dead and every "
            "audit vacuous, which reads as a clean file."
        )
    return out


def fixture_path(directory: Path, document_id: str) -> Path:
    return directory / f"{document_id}{SECTION_FILE_SUFFIX}"


def check(rules: RoutingRules, *, sections: Path) -> Audit:
    """Audit one document's rules against its committed headings.

    This is what CI runs. It needs no database, no corpus release and no source PDF — only the
    rule file and the fixture, both of which are in the repository.
    """
    return audit(rules, read_fixture(fixture_path(sections, rules.document_id)))
