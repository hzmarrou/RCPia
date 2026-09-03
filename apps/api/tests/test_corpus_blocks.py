"""Reading a guideline that was captured from a web page instead of converted from a PDF.

The unit tests below run anywhere. The reproduction test at the end needs the AFU sources, which
live in OneDrive and which CI cannot reach, so it skips rather than failing there — the same
constraint that put the section fixtures in the repository in the first place.
"""

import json
from pathlib import Path

import pytest

from rcpia.corpus.blocks import (
    BlockError,
    block_text,
    load_structure,
    one_cell_label,
    outline_blocks,
    parse_structure,
    read_blocks,
    structure_path,
)

CORPUS = Path(__file__).resolve().parents[1] / "rcpia" / "corpus"
STRUCTURE = CORPUS / "structure"
SECTIONS = CORPUS / "sections"
AFU = Path(r"C:\Users\hzmarrou\OneDrive\python\learning\RCPia\data\ref-03")

SOURCES = {
    "afu.prostate.2024-2026": AFU / "Prostate" / "document.json",
    "afu.bladder-tvim.2024-2026": AFU / "Vessie_TVIM" / "document.json",
    "afu.bladder-tvnim.2024-2026": AFU / "Vessie_TVNIM" / "document.json",
}

# Sections this reader produces that corpus v4.0 does not. Each is a real passage the previous
# builder dropped, checked by reading it — one of them carries a PSA threshold. Pinned rather
# than tolerated: if the number changes, someone has to look at what changed and why.
RECOVERED = {
    "afu.prostate.2024-2026": {
        ("Stadification", "Métastatique"),
        ("Moyens thérapeutiques", "Radiothérapie (RT)", "Curiethérapie"),
    },
    "afu.bladder-tvim.2024-2026": set(),
    "afu.bladder-tvnim.2024-2026": {
        ("Traitements endovésicaux adjuvants", "Contre-indications"),
    },
}


def table(*cells: str) -> dict:
    return {"type": "table", "rows": [list(cells)]}


def paragraph(text: str) -> dict:
    return {"type": "paragraph", "text": text}


def test_a_single_cell_table_is_a_heading() -> None:
    assert one_cell_label(table("Épidémiologie")) == "Épidémiologie"


def test_a_multi_cell_table_is_not_a_heading() -> None:
    assert one_cell_label({"type": "table", "rows": [["Grade", "Definition"]]}) is None


def test_a_paragraph_is_not_a_heading() -> None:
    assert one_cell_label(paragraph("Le cancer de la prostate...")) is None


def test_a_long_single_cell_is_prose_not_a_heading() -> None:
    # A callout that happens to sit alone in a table would otherwise become a chapter title.
    assert one_cell_label(table("x" * 200)) is None


def test_an_exhibit_caption_may_run_long() -> None:
    # AFU writes the whole caption into the cell.
    label = "Tableau III - " + "y" * 300
    assert one_cell_label(table(label)) == label


def test_a_table_keeps_its_shape_as_text() -> None:
    # A table flattened into a sentence is a table lost; downstream treats them differently.
    block = {"type": "table", "rows": [["PSAd", "< 0.10"], ["Risque", "faible"]]}
    assert block_text(block) == "| PSAd | < 0.10 |\n| Risque | faible |"


def test_a_pipe_inside_a_cell_is_escaped() -> None:
    assert block_text({"type": "table", "rows": [["a|b", "c"]]}) == r"| a\|b | c |"


STRUCTURE_DOC = {
    "document_id": "test.doc",
    "majors": ["Chapitre", "Annexes et tableaux"],
    "topics_by_major": {"Chapitre": ["Section"]},
    "excluded": ["Plan"],
    "appendix_major": "Annexes et tableaux",
}


def test_a_declaration_with_no_chapters_is_refused() -> None:
    with pytest.raises(BlockError, match="declares no chapters"):
        parse_structure({"document_id": "test.doc", "majors": []})


def test_a_topic_under_an_undeclared_chapter_is_refused() -> None:
    with pytest.raises(BlockError, match="not a declared chapter"):
        parse_structure(
            {"document_id": "test.doc", "majors": ["A"], "topics_by_major": {"B": ["x"]}}
        )


def test_an_appendix_that_is_not_a_chapter_is_refused() -> None:
    with pytest.raises(BlockError, match="appendix_major"):
        parse_structure(
            {"document_id": "test.doc", "majors": ["A"], "appendix_major": "Annexes"}
        )


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(BlockError, match="does not implement"):
        parse_structure({"document_id": "test.doc", "majors": ["A"], "level": 2})


def test_ranks_come_from_the_declaration() -> None:
    blocks = [
        table("Plan"),
        table("Chapitre"),
        paragraph("Chapter body."),
        table("Section"),
        paragraph("Section body."),
        table("Détail"),
        paragraph("Leaf body."),
    ]
    sections = outline_blocks(blocks, parse_structure(STRUCTURE_DOC))
    assert [s.section_path for s in sections] == [
        ("Chapitre",),
        ("Chapitre", "Section"),
        ("Chapitre", "Section", "Détail"),
    ]


def test_an_excluded_label_neither_opens_a_section_nor_closes_one() -> None:
    blocks = [table("Chapitre"), paragraph("First."), table("Plan"), paragraph("Second.")]
    sections = outline_blocks(blocks, parse_structure(STRUCTURE_DOC))
    assert len(sections) == 1
    assert "First." in sections[0].text and "Second." in sections[0].text


def test_an_exhibit_is_filed_under_the_appendix_wherever_it_is_printed() -> None:
    # AFU prints its tables where they are discussed and files them at the back.
    blocks = [
        table("Chapitre"),
        paragraph("Body."),
        table("Tableau I - Classification TNM."),
        paragraph("The table body."),
    ]
    sections = outline_blocks(blocks, parse_structure(STRUCTURE_DOC))
    assert sections[-1].section_path == (
        "Annexes et tableaux",
        "Tableau I - Classification TNM.",
    )


def test_a_section_holding_only_a_caption_is_dropped() -> None:
    blocks = [table("Chapitre"), paragraph("Recommandation 4")]
    assert outline_blocks(blocks, parse_structure(STRUCTURE_DOC)) == []


def test_a_one_sentence_passage_is_kept() -> None:
    # The counterpart of the test above: a short body is not the same as a caption, and a
    # character floor would have discarded a passage carrying a PSA threshold.
    blocks = [
        table("Chapitre"),
        paragraph("Le PSA total est corrélé au risque métastatique, au-delà de 20 ng/mL."),
    ]
    sections = outline_blocks(blocks, parse_structure(STRUCTURE_DOC))
    assert len(sections) == 1


@pytest.mark.parametrize("document_id", sorted(SOURCES))
def test_every_document_has_a_structure_declaration(document_id: str) -> None:
    assert structure_path(STRUCTURE, document_id).exists()
    assert load_structure(structure_path(STRUCTURE, document_id)).document_id == document_id


@pytest.mark.parametrize("document_id", sorted(SOURCES))
def test_the_reader_reproduces_every_section_corpus_v4_carries(document_id: str) -> None:
    source = SOURCES[document_id]
    if not source.exists():
        pytest.skip(f"{source} is not reachable from here (OneDrive)")

    fixture = SECTIONS / f"{document_id}.sections.jsonl"
    expected = []
    for line in fixture.read_text(encoding="utf-8").splitlines():
        if line.strip():
            path = tuple(json.loads(line))
            if path not in expected:
                expected.append(path)

    structure = load_structure(structure_path(STRUCTURE, document_id))
    produced = []
    for section in outline_blocks(read_blocks(source), structure):
        if section.section_path not in produced:
            produced.append(section.section_path)

    missing = [p for p in expected if p not in produced]
    assert not missing, "sections corpus v4.0 has and this reader lost:\n" + "\n".join(
        "  " + " > ".join(p) for p in missing
    )

    extra = {p for p in produced if p not in expected}
    assert extra == RECOVERED[document_id], (
        "the set of sections this reader recovers has changed; read them before updating the "
        f"pinned set.\n  now: {sorted(extra)}\n  pinned: {sorted(RECOVERED[document_id])}"
    )
