"""Stage 1 recovers a heading hierarchy that PDF conversion flattened.

The cases below are the real failures, reduced: a `#` level that saturates while the numbering
keeps going, a number grammar that alternates digits and letters, a page artefact sitting among
real headings, and a document that has no headings at all.
"""

import pytest

from rcpia.corpus.prepare import (
    HeadingStrategy,
    PrepareError,
    find_pdf_artefacts,
    heading_number,
    is_descendant,
    outline,
    path_is_coherent,
)


def test_a_number_alternates_digits_and_letters() -> None:
    # An earlier grammar allowed a letter segment only once and truncated this to 6.4.5.b.3,
    # which put the passage four levels above where the guideline puts it.
    assert heading_number("6.4.5.b.3.b.2 Morbidity") == ("6", "4", "5", "b", "3", "b", "2")


def test_a_year_in_a_title_is_not_a_chapter_number() -> None:
    # "2026 Update" would otherwise parse as chapter 2026 and become the root of everything
    # beneath it.
    assert heading_number("2026 Update") is None
    assert heading_number("6. TREATMENT") == ("6",)


def test_an_unnumbered_heading_has_no_number() -> None:
    assert heading_number("Management of bladder cancer") is None


def test_descendancy_is_by_segment_not_by_string() -> None:
    assert is_descendant(("6", "4", "5"), ("6", "4"))
    assert not is_descendant(("6", "4"), ("6", "4"))
    # 6.41 starts with the same characters as 6.4 and is not beneath it.
    assert not is_descendant(("6", "41"), ("6", "4"))


SATURATED = """# 3. EPIDEMIOLOGY AND AETIOLOGY

Chapter body.

## 3.2 Aetiology and risk factors

Section body.

### 3.2.1 Hereditary risk factors

Subsection body.

### 3.2.1.a Ethnicity and Family history

The passage that matters.
"""


def test_a_saturated_heading_level_does_not_lose_the_parent() -> None:
    # Both 3.2.1 and 3.2.1.a are `###`, so a level-keyed stack pops the parent and makes them
    # siblings. The number says otherwise.
    report = outline(SATURATED)
    assert report.strategy is HeadingStrategy.numbered
    deepest = report.sections[-1]
    assert deepest.section_path == (
        "3. EPIDEMIOLOGY AND AETIOLOGY",
        "3.2 Aetiology and risk factors",
        "3.2.1 Hereditary risk factors",
        "3.2.1.a Ethnicity and Family history",
    )
    assert all(path_is_coherent(section.section_path) for section in report.sections)


def test_a_section_carries_the_text_beneath_its_own_heading_only() -> None:
    report = outline(SATURATED)
    first = report.sections[0]
    assert first.text == "Chapter body."
    assert report.sections[-1].text == "The passage that matters."


WITH_ARTEFACT = """# 1. INTRODUCTION

Body.

## 1.1 Scope

Body.

# CLINICAL FRAILTY SCALE

A page header the converter promoted to a heading.

## 1.2 Method

Body.
"""


def test_an_unnumbered_heading_never_becomes_an_ancestor() -> None:
    # If the page header pushed onto the stack, 1.2 would be recorded as sitting beneath
    # "CLINICAL FRAILTY SCALE" -- and a rule naming chapter 1 would miss it.
    report = outline(WITH_ARTEFACT)
    by_label = {section.section_path[-1]: section.section_path for section in report.sections}

    # It is recorded where it appears, beneath the section that was open at the time -- that is
    # true of the document and costs nothing.
    assert by_label["CLINICAL FRAILTY SCALE"] == (
        "1. INTRODUCTION",
        "1.1 Scope",
        "CLINICAL FRAILTY SCALE",
    )
    # What matters is that it did not become anybody's parent.
    assert by_label["1.2 Method"] == ("1. INTRODUCTION", "1.2 Method")


UNNUMBERED = """# Management of bladder cancer

Body.

## Diagnosis

Body.

### Cystoscopy

Body.
"""


def test_a_document_without_numbering_is_parsed_by_level() -> None:
    report = outline(UNNUMBERED)
    assert report.strategy is HeadingStrategy.level
    assert report.numbered_share == 0.0
    assert report.sections[-1].section_path == (
        "Management of bladder cancer",
        "Diagnosis",
        "Cystoscopy",
    )


def test_a_document_with_no_headings_is_refused() -> None:
    # The AFU guidelines are web captures with no markdown headings. Returning one enormous
    # section for them would route everything to the document's default, which reads as success.
    with pytest.raises(PrepareError, match="no markdown headings"):
        outline("Some prose with no headings at all.\n", source="afu.prostate")


def test_the_refusal_names_the_document() -> None:
    with pytest.raises(PrepareError, match="afu.prostate"):
        outline("prose\n", source="afu.prostate")


def test_pdf_glyph_artefacts_are_reported_together() -> None:
    # Corpus v4 repaired 27 of these by hand. "eGFR /H11021 30" retrieves for nothing and reads
    # as ordinary text, so the build has to say so rather than embed it.
    found = find_pdf_artefacts("eGFR /H11021 30 and PSA /H11022 20 and /H11021 again")
    assert found == ["/H11021", "/H11022"]


def test_clean_text_reports_no_artefacts() -> None:
    assert find_pdf_artefacts("eGFR < 30 and PSA > 20") == []


def test_an_incoherent_path_is_detected() -> None:
    # What the level-keyed stack produced: 3.2.1.a recorded directly beneath 3.2, with the
    # parent it actually sits under missing.
    assert path_is_coherent(("3. EPIDEMIOLOGY", "3.2 Aetiology", "3.2.1 Hereditary"))
    assert not path_is_coherent(("6.4 Recurrence", "6.2 Modalities"))
