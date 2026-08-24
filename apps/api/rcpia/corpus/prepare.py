"""Stage 1: a guideline's own structure, recovered from its markdown.

**This stage carries no clinical judgment.** It answers "what section is this passage in?" and
nothing else; which patient the section is about is stage 2's question, and keeping the two apart
is what stopped the corpus routing passages by the words in their bodies.

The problem it exists to solve is that a heading hierarchy does not survive PDF conversion.
Measured on `EAU_2026_Prostate_Cancer.md`: 449 headings, but the `#` level saturates — 243 of
them are `###` — while the guideline's own numbering runs seven deep. A stack keyed on the `#`
level therefore treats `3.2.1.a` as a *sibling* of `3.2.1` rather than its child, and the parent
disappears from the path:

    level-keyed    3. EPIDEMIOLOGY > 3.2 Aetiology > 3.2.1.a Ethnicity and Family history
    number-keyed   3. EPIDEMIOLOGY > 3.2 Aetiology > 3.2.1 Hereditary risk factors
                                                   > 3.2.1.a Ethnicity and Family history

Guidelines number their headings, and the number states the depth that the `#` level lost. Keying
the stack on the number instead recovers it: 449 of 449 EAU paths become internally coherent,
against 266 with the level, and the 22 passages that arrived with a deep subsection and no
chapter above it drop to none.

**The strategy is chosen from the document and reported, not guessed.** A document whose
headings are numbered is parsed by number; one whose headings are not is parsed by level, which
is correct for a document that never had numbers to lose. `HeadingStrategy` says which was used
so a surprising path can be traced to a decision rather than to a mystery.

**A document with no headings at all is refused.** The AFU guidelines are Urofrance web captures
whose structure lives in `document.json` blocks rather than in markdown, and silently returning
one enormous section for them would produce a document that chunks, loads, and routes everything
to its default -- which reads exactly like success.
"""

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

# A heading number as the guideline writes it: 6, 6.4, 6.4.5, 6.4.5.b, 6.4.5.b.3.b.2. Digit and
# letter segments alternate freely -- an earlier grammar allowed letters only once and silently
# truncated `6.4.5.b.3.b.2` to `6.4.5.b.3`, which put a passage four levels above where it belongs.
#
# Two guards against reading a number that is not one. A lone segment is capped at two digits, so
# `2026 Update` is a title and not chapter 2026 -- guidelines number their chapters 1 to 12, and a
# four-digit root would become the ancestor of everything after it. And the number must be
# followed by a separator, so `6.4mm margins` is prose.
_NUMBER = re.compile(r"^(\d{1,2}(?:\.(?:\d+|[a-z]))*|\d+(?:\.(?:\d+|[a-z]))+)(?=[\s.:)–—-]|$)")
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")

# Below this share of numbered headings a document is treated as unnumbered. Well clear of both
# populations measured: EAU prostate 98%, EAU NMIBC 97%, SIOG bladder 90%, against 0% for every
# document that carries no numbering at all. Nothing observed sits near the line.
NUMBERED_THRESHOLD = 0.6

# PDF extraction artefacts that must never reach the corpus. `/H11021` is a glyph code that a
# converter emitted instead of `<`, and corpus v4 had to repair 27 of them by hand. A passage
# reading "eGFR /H11021 30" is not wrong in a way retrieval can detect -- it simply never matches.
_PDF_ARTEFACT = re.compile(r"/H\d{4,5}\b")


class PrepareError(Exception):
    """A source document cannot be turned into sections, and guessing would hide it."""


class HeadingStrategy(StrEnum):
    numbered = "numbered"
    level = "level"


@dataclass(frozen=True)
class Heading:
    level: int
    label: str
    line: int
    number: tuple[str, ...] | None


@dataclass(frozen=True)
class Section:
    """One heading and the text beneath it, with the full path from the document's root."""

    section_path: tuple[str, ...]
    text: str
    line_start: int
    line_end: int


# Above this many repetitions a heading cannot be identifying anything, so it is dropped from
# the path rather than allowed to shape it.
#
# **Measured, and the gap is wide.** Repetition counts for the most-repeated heading in each
# document: NCCN prostate 176, 175, 44 then 12; NCCN bladder 122, 107, 16 then 6; ESPEN surgery
# 23 then 1; EAU and SIOG 1 throughout. Nothing observed sits between 12 and 16.
#
# It catches two different things, both correctly. NCCN's are page furniture -- `NCCN Guidelines
# Version 6.2026` printed on every page and promoted to a heading, 175 times, each one resetting
# the hierarchy under it. ESPEN's is `Commentary`, a real sub-heading that says nothing about
# which passage it introduces. Neither can identify a section, which is the only thing a path
# element is for.
RECURRING_HEADING_LIMIT = 15


@dataclass(frozen=True)
class Outline:
    """A document's sections, and how they were derived."""

    strategy: HeadingStrategy
    sections: tuple[Section, ...]
    headings_seen: int
    numbered_headings: int
    # Reported rather than silently dropped: a heading this stage discarded is a heading a rule
    # can never match, and an author who wrote one deserves to be told why it is dead.
    ignored_headings: tuple[str, ...] = ()

    @property
    def numbered_share(self) -> float:
        return self.numbered_headings / self.headings_seen if self.headings_seen else 0.0


def heading_number(label: str) -> tuple[str, ...] | None:
    """The heading's own number, as segments. `6.4.5.b` -> `("6", "4", "5", "b")`."""
    match = _NUMBER.match(label)
    return tuple(match.group(1).split(".")) if match else None


def is_descendant(child: tuple[str, ...], parent: tuple[str, ...]) -> bool:
    return len(child) > len(parent) and child[: len(parent)] == parent


def read_headings(markdown: str) -> list[Heading]:
    out: list[Heading] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = _HEADING.match(line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(2)).strip()
        out.append(
            Heading(
                level=len(match.group(1)),
                label=label,
                line=line_number,
                number=heading_number(label),
            )
        )
    return out


def find_pdf_artefacts(text: str) -> list[str]:
    """Glyph codes a converter left behind. Returned rather than raised, so a caller can report
    every one at once instead of one per run."""
    return sorted(set(_PDF_ARTEFACT.findall(text)))


# A repository deposit — HAL, and the like — puts its own cover page in front of the paper: the
# archive's logo, a citation block, the deposit id, the licence, and a link to the version of
# record. Chunked, that becomes several passages of archive boilerplate carrying the paper's
# title, which is precisely the kind of passage that retrieves well and answers nothing.
_COVER_TRIGGER = re.compile(r"^#{0,6}\s*HAL Id:\s*\S", re.MULTILINE)
# Where the cover stops. Both markers are deposit artefacts, appear once, and sit on the last
# line of the cover.
_COVER_END = re.compile(r"^\s*(?:Version of Record:|Manuscript_[0-9a-f]{8,})", re.IGNORECASE)
# A cover is one page. Beyond this the terminator found is something else and cutting to it would
# take real content with it, so the document is refused instead.
COVER_SEARCH_LINES = 80


def strip_repository_cover(markdown: str, *, source: str = "document") -> str:
    """Remove an archive's cover page, or say why it cannot be removed safely.

    Does nothing to a document that has no cover, so it is safe to call on everything.
    """
    if not _COVER_TRIGGER.search(markdown):
        return markdown

    lines = markdown.splitlines()
    end = None
    for index, line in enumerate(lines[:COVER_SEARCH_LINES]):
        if _COVER_END.match(line):
            end = index
    if end is None:
        raise PrepareError(
            f"{source} carries a repository cover page but none of the markers that end one "
            f"appears in its first {COVER_SEARCH_LINES} lines. Cutting to a guess would take the "
            "start of the paper with it, so the document is refused rather than trimmed."
        )
    return "\n".join(lines[end + 1 :]).lstrip("\n")


def _paths_by_number(headings: list[Heading]) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    stack: list[Heading] = []
    for heading in headings:
        if heading.number is None:
            # An unnumbered heading inside a numbered document is a page artefact, a running
            # header, or a callout. It takes the current chapter as context but never becomes an
            # ancestor -- letting it push would make every heading after it a child of a page
            # header.
            paths.append(tuple(item.label for item in stack) + (heading.label,))
            continue
        while stack and not is_descendant(heading.number, stack[-1].number or ()):
            stack.pop()
        stack.append(heading)
        paths.append(tuple(item.label for item in stack))
    return paths


def _paths_by_level(headings: list[Heading]) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    stack: list[Heading] = []
    for heading in headings:
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        stack.append(heading)
        paths.append(tuple(item.label for item in stack))
    return paths


def outline(
    markdown: str, *, source: str = "document", allow_artefacts: bool = False
) -> Outline:
    """Every section of a document, with the path each one sits at.

    `source` names the document in errors; it is never read as content.

    A document carrying converter glyph codes is refused. Corpus v4.0 repaired 27 of them by
    hand, in the release rather than in the source, so a rebuild brings every one back — and
    `eGFR /H11021 30` reads as ordinary prose while matching nothing a clinician would search
    for. Repairing them here was considered and rejected: choosing which character was meant is a
    guess, and a wrong guess inside a threshold is worse than a refusal. `allow_artefacts` exists
    so their extent can be measured before deciding what to do about them.
    """
    artefacts = find_pdf_artefacts(markdown)
    if artefacts and not allow_artefacts:
        raise PrepareError(
            f"{source} still contains converter glyph codes: {', '.join(artefacts)}. These read "
            "as text and retrieve for nothing. Repair them in the source markdown, or re-convert "
            "the PDF, rather than loading a passage whose numbers are unreadable."
        )

    markdown = strip_repository_cover(markdown, source=source)
    headings = read_headings(markdown)
    if not headings:
        raise PrepareError(
            f"{source} has no markdown headings, so it has no structure to route by. Web captures "
            "carry their structure in `document.json` blocks instead -- read those rather than "
            "letting the whole document become one section, which routes cleanly and means "
            "nothing."
        )

    seen_before = len(headings)
    repetitions = Counter(heading.label for heading in headings)
    ignored = sorted(
        label for label, count in repetitions.items() if count > RECURRING_HEADING_LIMIT
    )
    if ignored:
        # Dropped entirely rather than kept as leaves. Their text is not lost: it belongs to the
        # section that was open when the page turned, which is where a reader would look for it.
        headings = [heading for heading in headings if heading.label not in set(ignored)]
        if not headings:
            raise PrepareError(
                f"{source}: every heading repeats more than {RECURRING_HEADING_LIMIT} times, so "
                "none of them identifies a section. That is a conversion that produced page "
                "furniture and no structure, not a document with an unusual layout."
            )

    numbered = sum(1 for heading in headings if heading.number is not None)
    strategy = (
        HeadingStrategy.numbered
        if numbered / len(headings) >= NUMBERED_THRESHOLD
        else HeadingStrategy.level
    )
    paths = (
        _paths_by_number(headings)
        if strategy is HeadingStrategy.numbered
        else _paths_by_level(headings)
    )

    lines = markdown.splitlines()
    sections: list[Section] = []
    for index, (heading, path) in enumerate(zip(headings, paths, strict=True)):
        end = headings[index + 1].line - 1 if index + 1 < len(headings) else len(lines)
        body = "\n".join(lines[heading.line : end]).strip()
        sections.append(
            Section(section_path=path, text=body, line_start=heading.line, line_end=end)
        )

    return Outline(
        strategy=strategy,
        sections=tuple(sections),
        headings_seen=seen_before,
        numbered_headings=numbered,
        ignored_headings=tuple(ignored),
    )


def path_is_coherent(section_path: tuple[str, ...]) -> bool:
    """Whether each numbered element of a path is a descendant of the numbered one before it.

    The check the level-keyed stack fails and the number-keyed one passes. Worth asserting per
    document at build time: a path that is not coherent names a parent the passage does not
    actually sit under, and a rule written against that parent claims the wrong passages.
    """
    numbers = [n for label in section_path if (n := heading_number(label)) is not None]
    return all(
        is_descendant(child, parent)
        for parent, child in zip(numbers, numbers[1:], strict=False)
    )
