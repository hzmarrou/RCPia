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


REPEATED = "\n".join(
    line
    for n in range(20)
    for line in ("# NCCN Guidelines Version 6.2026", f"## Real section {n}", f"Body {n}.")
)


def test_a_heading_that_repeats_too_often_cannot_identify_anything() -> None:
    # NCCN prints its version banner on every page and the converter promotes it to a heading:
    # 175 times in the prostate guideline, each one resetting the hierarchy beneath it.
    report = outline(REPEATED)
    assert "NCCN Guidelines Version 6.2026" in report.ignored_headings
    # The real sections survive, and none of them sits under the banner.
    assert all(len(s.section_path) == 1 for s in report.sections)
    assert len(report.sections) == 20


def test_the_text_under_an_ignored_heading_is_not_lost() -> None:
    markdown = "\n".join(
        ["# Real chapter", "Chapter body."]
        + ["# BANNER\nBanner-adjacent text." for _ in range(20)]
    )
    report = outline(markdown)
    assert "BANNER" in report.ignored_headings
    kept = "\n".join(s.text for s in report.sections)
    assert "Banner-adjacent text." in kept


def test_a_heading_repeated_a_few_times_is_kept() -> None:
    # Real headings do repeat: NCCN's own PRINCIPLES OF RADIATION THERAPY appears 12 times, and
    # the threshold sits above that in a measured gap.
    markdown = "\n".join(["# FOLLOW-UP", "Body."] * 6)
    report = outline(markdown)
    assert report.ignored_headings == ()


def test_a_document_that_is_all_furniture_is_refused() -> None:
    with pytest.raises(PrepareError, match="page furniture and no structure"):
        outline("\n".join(["# BANNER", "Body."] * 20))


CITATIONS = """# Discussion

Body of the discussion.

# References

# 1. Extermann M, Aapro M, Bernabei R, et al. Use of comprehensive geriatric assessment.

Journal, year, pages.

# 2. Puts MT, Hardt J, Monette J, et al. Use of geriatric assessment for older adults.

Journal, year, pages.
"""


def test_a_bibliography_converted_into_headings_is_marked() -> None:
    # SIOG's screening paper turns its whole bibliography into numbered headings, so the numbered
    # strategy reads 64 citations as chapters -- 71 of its 109 passages. A routing rule cannot
    # fix that: a rule matches a heading it can name, and there is no naming sixty-four citations.
    report = outline(CITATIONS)
    marked = [s for s in report.sections if s.is_reference]
    assert len(marked) == 2
    assert all(s.section_path[-1].startswith(("1.", "2.")) for s in marked)


def test_a_chapter_title_is_not_mistaken_for_a_citation() -> None:
    # `3. EPIDEMIOLOGY AND AETIOLOGY` matched a looser version of this pattern: `EPIDEMIOLOGY` and
    # `AND` have a citation's shape and the wrong case. Requiring lower case in the surname is
    # what separates them, and it was found by testing against the whole library rather than by
    # reasoning about it.
    report = outline("# 3. EPIDEMIOLOGY AND AETIOLOGY\n\nBody.\n")
    assert not any(s.is_reference for s in report.sections)


def test_an_ordinary_section_is_not_a_reference() -> None:
    assert not any(s.is_reference for s in outline(SATURATED).sections)


# NCCN's discussion pages: the only `#` is the banner, and every real chapter is `##`.
BANNER_REPLACES_CHAPTER = "\n".join(
    ["# Table of Contents", "Overview .... MS-1"]
    + [
        line
        for n in range(20)
        for line in ("# NCCN Guidelines Version 6.2026", f"## Real chapter {n}", f"Body {n}.")
    ]
)


def test_page_furniture_does_not_end_a_section_unless_declared() -> None:
    # AUA's banners are page turns *inside* a chapter, and the subsection after one belongs to
    # the chapter above. Dropping the banner already achieves that, so it stays the default:
    # turning this on globally cost the six AUA guidelines most of their routing precision.
    report = outline(BANNER_REPLACES_CHAPTER)
    assert "NCCN Guidelines Version 6.2026" in report.ignored_headings
    chapters = [s for s in report.sections if s.section_path[-1].startswith("Real chapter")]
    assert chapters
    assert all(s.section_path[0] == "Table of Contents" for s in chapters)


def test_declared_page_furniture_ends_the_section_it_interrupts() -> None:
    # NCCN's banner replaces the chapter level rather than interrupting it, so the chapters
    # after it are not children of whatever `#` last survived. That was the table of contents,
    # which took 234 of the prostate guideline's 394 passages with it -- and in the bladder
    # guideline, `Primary Carcinoma of the Urethra` took 156 of 282, a heading that is out of
    # scope, so excluding it correctly would have deleted more than half the document.
    report = outline(BANNER_REPLACES_CHAPTER, furniture_ends_section=True)
    chapters = [s for s in report.sections if s.section_path[-1].startswith("Real chapter")]
    assert chapters, "the real chapters should survive"
    assert all(len(s.section_path) == 1 for s in chapters)
    assert not any("Table of Contents" in s.section_path for s in chapters)


def test_the_declaration_changes_nothing_in_a_document_without_furniture() -> None:
    assert outline(SATURATED).sections == outline(SATURATED, furniture_ends_section=True).sections
