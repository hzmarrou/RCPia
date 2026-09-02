"""Which page of the source PDF a passage came from.

A citation a clinician cannot turn to is not much of a citation. The converted markdown carries
no page numbers — none of the four documents converted on 24 August has one — so the page is
recovered afterwards, by finding which page of the PDF the passage's own words appear on.

**Overlap, not exact matching.** Conversion rewrites text: ligatures, hyphenation across line
breaks, table cells reflowed into pipes, footnote markers dropped. An exact search finds almost
nothing. Comparing the *set of words* at each end of a passage against the set of words on each
page survives all of that, and is what the previous builder did.

**A confidence comes back with every span, and it is the weaker of the two ends.** A passage
whose opening matches page 7 confidently and whose close matches page 12 barely has not been
located; reporting the better of the two would hide exactly that. The caller decides what floor
to demand — this module does not silently discard a weak answer, it reports one.

Pages come from `pdftotext`, which is present on this machine, rather than from a Python PDF
library, which is not and cannot be installed here. If the binary is missing the module says so
instead of returning no pages, because a document with every page silently unknown looks the same
as one with no PDF at all.
"""

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Words at each end of a passage used to find its page. Enough to be distinctive, few enough that
# a passage spanning a page break still has most of them on one side.
PROBE_WORDS = 45
# A passage cannot really run across more pages than this; a wider span means the two ends matched
# different parts of the document and only the opening should be trusted.
MAX_SPAN_PAGES = 3


class LocateError(Exception):
    """Pages cannot be read, so no passage in this document can be located."""


@dataclass(frozen=True)
class PageSpan:
    first: int
    last: int
    confidence: float


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", without_accents)).strip()


def page_texts(pdf: Path) -> list[str]:
    """Each page of a PDF as text, in order.

    `pdftotext` separates pages with a form feed, so one call gives every page rather than one
    call per page.
    """
    # The argument is checked before the environment, deliberately. A path that does not exist
    # is wrong wherever the call runs, and reporting it as a missing tool sends the reader to
    # install poppler-utils to fix a typo. It also made this function's error depend on the
    # machine: with pdftotext present the caller was told the file was missing, and on a runner
    # without it the same call blamed the tool.
    if not pdf.exists():
        raise LocateError(f"{pdf} does not exist")
    if shutil.which("pdftotext") is None:
        raise LocateError(
            "pdftotext is not on PATH, so page numbers cannot be recovered. Install poppler-utils "
            "or record the pages as unavailable — do not let every passage report an unknown page "
            "as though the document had no PDF."
        )
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, path argument only
            ["pdftotext", str(pdf), "-"],
            capture_output=True,
            check=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise LocateError(f"pdftotext failed on {pdf.name}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LocateError(f"pdftotext timed out on {pdf.name}") from exc

    text = result.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not pages:
        raise LocateError(f"{pdf.name} yielded no pages")
    return pages


def _best_page(probe: list[str], page_tokens: list[set[str]]) -> tuple[int, float]:
    wanted = set(probe)
    if not wanted:
        return 1, 0.0
    scores = [len(wanted & tokens) / len(wanted) for tokens in page_tokens]
    index = max(range(len(scores)), key=scores.__getitem__)
    return index + 1, scores[index]


def locate(
    text: str, page_tokens: list[set[str]], *, probe_words: int = PROBE_WORDS
) -> PageSpan:
    """Where one passage sits, given the tokens of each page.

    A passage shorter than twice `probe_words` has the same words at both ends, so both probes
    return the same page and the span is a single page. That is correct — a short passage does
    not straddle a break — and it is why `probe_words` is adjustable rather than fixed: a test
    with four-word pages needs a probe to match.
    """
    words = _normalise(text).split()
    if not words or not page_tokens:
        return PageSpan(first=0, last=0, confidence=0.0)

    opening, opening_score = _best_page(words[:probe_words], page_tokens)
    closing, closing_score = _best_page(words[-probe_words:], page_tokens)
    first, last = sorted((opening, closing))
    if last - first > MAX_SPAN_PAGES:
        # The two ends disagree about where this passage is. Trust the opening, which is the end
        # a reader turns to, and let the confidence say the answer is poor.
        last = first
    return PageSpan(first=first, last=last, confidence=min(opening_score, closing_score))


def tokens_per_page(pages: list[str]) -> list[set[str]]:
    return [set(_normalise(page).split()) for page in pages]


def locate_all(texts: list[str], pdf: Path) -> list[PageSpan]:
    """Locate every passage of one document. Reads the PDF once."""
    page_tokens = tokens_per_page(page_texts(pdf))
    return [locate(text, page_tokens) for text in texts]
