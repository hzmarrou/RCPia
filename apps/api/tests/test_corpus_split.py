"""Cutting a section into passages, and the one cut that must never happen.

The table case is not hypothetical: corpus v4.0 splits 20 tables across consecutive passages,
including the TNM staging table between T3b and T4.
"""

from rcpia.corpus.split import MAX_CHARS, MIN_CHARS, Passage, atoms, passages

TNM = """| Stage | Definition |
| T2 | Tumour confined within prostate |
| T3a | Extracapsular extension |
| T3b | Tumour invades seminal vesicle(s) |
| T4 | Tumour is fixed or invades adjacent structures |"""


def test_a_table_is_one_atom_however_many_rows() -> None:
    found = atoms(TNM)
    assert len(found) == 1
    assert found[0].kind == "table"
    assert found[0].text.count("\n") == 4


def test_a_table_is_never_cut_even_when_it_does_not_fit() -> None:
    # The v4.0 failure, reduced: with a small limit the greedy line packer would end one passage
    # at T3b and start the next at T4, leaving stage definitions with no column headings.
    out = passages(TNM, max_chars=80)
    assert len(out) == 1
    assert out[0].kind == "table"
    assert "T3b" in out[0].text and "T4" in out[0].text
    assert out[0].is_oversized


def test_prose_around_a_table_does_not_drag_the_table_along() -> None:
    text = f"Intro paragraph about staging.\n\n{TNM}\n\nClosing paragraph about staging."
    out = passages(text, max_chars=120, min_chars=1)
    tables = [p for p in out if p.kind == "table"]
    assert len(tables) == 1
    assert "T2" in tables[0].text and "T4" in tables[0].text


def test_paragraphs_are_packed_up_to_the_limit() -> None:
    text = "\n\n".join(["A" * 100] * 6)
    out = passages(text, max_chars=350, min_chars=1)
    assert all(len(p.text) <= 350 for p in out)
    assert len(out) == 2


def test_a_paragraph_too_large_is_cut_at_sentence_ends() -> None:
    text = " ".join(f"Sentence number {i} about prostate cancer." for i in range(40))
    out = passages(text, max_chars=300, min_chars=1)
    assert len(out) > 1
    for p in out:
        # No passage may start mid-sentence: a clause of a recommendation reads like the
        # recommendation.
        assert p.text[0].isupper()


def test_a_single_sentence_longer_than_the_limit_is_left_long() -> None:
    # Cutting it would land mid-clause. Better one long passage than two misleading ones.
    text = "The patient " + "very " * 400 + "old."
    out = passages(text, max_chars=200)
    assert len(out) == 1


def test_a_short_tail_is_folded_back_rather_than_orphaned() -> None:
    text = "\n\n".join(["B" * 300, "C" * 300, "tiny"])
    out = passages(text, max_chars=650, min_chars=MIN_CHARS)
    assert "tiny" in out[-1].text
    assert not any(p.text == "tiny" for p in out)


def test_a_section_shorter_than_the_minimum_is_still_a_passage() -> None:
    # A short section is a real section; only an orphaned tail is folded away.
    out = passages("Le PSA total est corrélé au risque métastatique.")
    assert len(out) == 1


def test_an_empty_section_yields_nothing() -> None:
    assert passages("   \n\n  ") == []


def test_kind_is_reported_for_every_passage() -> None:
    mixed = passages(f"Some prose here.\n\n{TNM}", max_chars=MAX_CHARS, min_chars=1)
    assert len(mixed) == 1
    assert mixed[0].kind == "mixed"
    assert passages("Just prose.")[0].kind == "prose"
    assert passages(TNM)[0].kind == "table"


def test_only_a_table_can_be_oversized() -> None:
    long_prose = " ".join(f"Sentence {i}." for i in range(2000))
    for p in passages(long_prose):
        assert not p.is_oversized or p.kind != "prose"


def test_the_limits_match_the_corpus_they_rebuild() -> None:
    # Corpus v4.0 runs 100 to 3,598 characters. Keeping the same envelope keeps the rebuilt
    # corpus comparable to the one the retrieval evaluation will score against.
    assert (MIN_CHARS, MAX_CHARS) == (100, 3600)


REFERENCES = "\n".join(
    f"| [{n}] | Author {n} A., Author {n} B. Some paper title. Journal. 2020;{n}:1-9. |"
    for n in range(1, 12)
)


def test_a_reference_list_is_recognised_by_its_shape() -> None:
    # AFU's captures end with one, rendered as a table and carrying no heading at all -- so the
    # heading-based detector corpus v4.0 used cannot see it. Left unrecognised it became an
    # 86,854-character passage.
    out = passages(REFERENCES)
    assert len(out) == 1
    assert out[0].kind == "references"


def test_a_reference_list_stands_alone() -> None:
    # So the release can exclude it whole and name a reason, rather than losing it silently.
    out = passages(f"Real guidance about staging.\n\n{REFERENCES}", min_chars=1)
    assert [p.kind for p in out] == ["prose", "references"]


def test_a_short_numbered_table_is_not_a_reference_list() -> None:
    # Numbered rows are ordinary in clinical tables; only a long run of them is a bibliography.
    short = "| [1] | first |\n| [2] | second |"
    assert passages(short)[0].kind == "table"


def test_a_data_table_is_never_mistaken_for_references() -> None:
    assert passages(TNM)[0].kind == "table"


def test_a_passage_is_immutable() -> None:
    p = Passage(text="x", kind="prose")
    try:
        p.text = "y"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Passage should be frozen")
