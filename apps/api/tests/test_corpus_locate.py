"""Recovering the page a passage came from, when the converted markdown carries none."""

from pathlib import Path

import pytest

from rcpia.corpus.locate import (
    MAX_SPAN_PAGES,
    LocateError,
    PageSpan,
    locate,
    page_texts,
    tokens_per_page,
)

PAGES = [
    "Introduction to prostate cancer epidemiology and incidence in France",
    "Screening with prostate specific antigen and digital rectal examination",
    "Radical prostatectomy technique lymph node dissection and complications",
    "Androgen deprivation therapy and castration resistant disease management",
]
TOKENS = tokens_per_page(PAGES)


def test_a_passage_is_found_on_its_own_page() -> None:
    span = locate("Radical prostatectomy technique and lymph node dissection", TOKENS)
    assert span.first == 3
    assert span.last == 3
    assert span.confidence > 0.5


def test_a_passage_spanning_a_break_gets_both_pages() -> None:
    # A short probe, because these test pages are one line each.
    text = "Screening prostate specific antigen examination. Radical prostatectomy lymph node."
    span = locate(text, TOKENS, probe_words=4)
    assert (span.first, span.last) == (2, 3)


def test_conversion_differences_do_not_prevent_a_match() -> None:
    # Accents, case and punctuation all change during conversion; the words survive.
    span = locate("ANDROGEN-DEPRIVATION THERAPY, and castration-resistant disease!", TOKENS)
    assert span.first == 4


def test_the_confidence_is_the_weaker_end_not_the_better_one() -> None:
    # An opening that matches confidently and a close that barely matches means the passage was
    # not located. Reporting the better end would hide exactly that.
    text = "Introduction prostate cancer epidemiology France. Zzz qqq xxx yyy."
    span = locate(text, TOKENS, probe_words=4)
    assert span.first == 1
    assert span.confidence == 0.0


def test_ends_that_disagree_wildly_fall_back_to_the_opening() -> None:
    pages = tokens_per_page(["alpha " * 30] + ["filler"] * 10 + ["omega " * 30])
    span = locate("alpha alpha alpha omega omega omega", pages, probe_words=3)
    assert span.last - span.first <= MAX_SPAN_PAGES
    assert span.first == 1


def test_an_empty_passage_locates_nowhere_rather_than_on_page_one() -> None:
    span = locate("", TOKENS)
    assert span == PageSpan(first=0, last=0, confidence=0.0)


def test_a_document_with_no_pages_locates_nowhere() -> None:
    assert locate("anything", []).first == 0


def test_a_missing_pdf_is_refused_by_name() -> None:
    with pytest.raises(LocateError, match="does not exist"):
        page_texts(Path("no-such-file-anywhere.pdf"))


REAL_PDF = Path(
    r"C:\Users\hzmarrou\OneDrive\python\learning\RCPia\data\ref-25\ESMO_Cancer_Cachexia_2021.pdf"
)


def test_pages_come_back_from_a_real_pdf() -> None:
    if not REAL_PDF.exists():
        pytest.skip("the corpus sources are not reachable from here (OneDrive)")
    pages = page_texts(REAL_PDF)
    # pdfinfo reports 18 pages for this file.
    assert len(pages) == 18
    assert "cachexia" in pages[0].casefold()
