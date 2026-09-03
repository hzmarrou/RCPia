"""The driver that turns committed decisions into a release.

It owns no clinical judgement -- the setting comes from a rule file, the provenance from
`specs.json`, the scope from `coverage.json`. What it can get wrong is which reader a document
gets, whether a page it could not find is reported as unknown or invented, and whether a
document in scope with nothing behind it is noticed or skipped.
"""

import json
from pathlib import Path

import pytest

from rcpia.corpus.build import (
    BuildError,
    SourceRecord,
    _page_spans,
    read_curation,
    read_sections,
    route_document,
)
from rcpia.corpus.rules import parse_rules

MARKDOWN = """# Traitement

Le traitement de reference.

## Tumeurs de haut risque

BCG est indique.

# References

1. Someone A, Other B. A paper. Journal 2024.
"""


def rules_for(document_id: str, **overrides):
    body = {
        "document_id": document_id,
        "default": {"setting": "bladder_nmibc_unspecified_risk"},
        "rules": [],
    }
    body.update(overrides)
    return parse_rules(body, source=f"{document_id}.json")


def library_with_curation(tmp_path: Path, records: list[dict]) -> Path:
    prepared = tmp_path / "_prepared"
    prepared.mkdir(parents=True, exist_ok=True)
    (prepared / "poc_document_manifest.json").write_text(
        json.dumps({"documents": records}), encoding="utf-8"
    )
    return tmp_path


def test_the_curation_is_required_and_says_so(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="not in the repository"):
        read_curation(tmp_path)


def test_a_source_records_only_files_inside_the_library(tmp_path: Path) -> None:
    # `source_files` are claimed in the manifest and checked against the folder list, so a path
    # from outside the library would claim a folder that does not exist.
    library = library_with_curation(
        tmp_path,
        [
            {
                "document_id": "x.y.2026",
                "ref_id": "ref-01",
                "title": "A guideline",
                "source_path": str(tmp_path / "ref-01" / "g.pdf"),
                "markdown_path": str(tmp_path / "ref-01" / "g.md"),
                "raw_html_path": "C:/somewhere/else/raw.html",
            }
        ],
    )
    record = read_curation(library)["x.y.2026"]

    assert record.source_files == ("ref-01/g.pdf", "ref-01/g.md")
    assert record.pdf_path is not None and record.pdf_path.name == "g.pdf"


def test_a_document_with_no_pdf_has_no_pdf_path(tmp_path: Path) -> None:
    library = library_with_curation(
        tmp_path,
        [
            {
                "document_id": "afu.x",
                "ref_id": "ref-03",
                "title": "A capture",
                "source_path": str(tmp_path / "ref-03" / "raw.html"),
            }
        ],
    )
    assert read_curation(library)["afu.x"].pdf_path is None


def source_for(tmp_path: Path, markdown: str = MARKDOWN) -> SourceRecord:
    path = tmp_path / "g.md"
    path.write_text(markdown, encoding="utf-8")
    return SourceRecord(
        document_id="x.y.2026",
        ref_id="ref-01",
        title="A guideline",
        markdown_path=path,
        blocks_path=None,
        pdf_path=None,
        source_files=("ref-01/g.md",),
    )


def test_a_markdown_document_is_read_by_prepare(tmp_path: Path) -> None:
    sections = read_sections(source_for(tmp_path), rules_for("x.y.2026"))
    assert [s.section_path[-1] for s in sections][:2] == ["Traitement", "Tumeurs de haut risque"]


def test_a_missing_markdown_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    source = SourceRecord("x.y.2026", "ref-01", "A guideline", tmp_path / "absent.md", None,
                          None, ())
    with pytest.raises(BuildError, match="no markdown to read"):
        read_sections(source, rules_for("x.y.2026"))


def test_every_passage_carries_what_its_rule_file_decided(tmp_path: Path) -> None:
    routed = route_document(source_for(tmp_path), rules_for("x.y.2026"), locate_pages=False)

    assert routed
    treatment = [r for r in routed if r.section_path[-1] == "Tumeurs de haut risque"]
    assert treatment and all(
        r.disease_setting == "bladder_nmibc_unspecified_risk" for r in treatment
    )


def test_a_rule_that_excludes_carries_its_reason_onto_the_passage(tmp_path: Path) -> None:
    rules = rules_for(
        "x.y.2026",
        rules=[
            {
                "match_section": "References",
                "setting": "bladder_nmibc_unspecified_risk",
                "exclude": True,
                "exclusion_reason": "bibliography_entry",
                "match_leaf": True,
            }
        ],
    )
    routed = route_document(source_for(tmp_path), rules, locate_pages=False)

    refs = [r for r in routed if r.section_path[-1] == "References"]
    assert refs and all(r.exclude and r.exclusion_reason == "bibliography_entry" for r in refs)


def test_a_page_nobody_could_find_is_reported_as_unknown(tmp_path: Path) -> None:
    # A citation carrying a page nobody located is worse than one carrying none, so this
    # degrades rather than raising: the AFU captures have no PDF, and pdftotext may be absent.
    source = source_for(tmp_path)
    spans = _page_spans(["some text", "more text"], source, locate_pages=True)

    assert [(s.first, s.last, s.confidence) for s in spans] == [(0, 0, 0.0), (0, 0, 0.0)]


def test_page_location_can_be_skipped_entirely(tmp_path: Path) -> None:
    source = source_for(tmp_path)
    assert all(s.first == 0 for s in _page_spans(["x"], source, locate_pages=False))


def test_a_capture_declaring_a_structure_without_its_blocks_is_refused(tmp_path: Path) -> None:
    # The declaration and the capture have to travel together; one without the other would
    # otherwise fall through to the markdown reader and silently produce different sections.
    source = SourceRecord(
        "afu.prostate.2024-2026", "ref-03", "AFU", tmp_path / "g.md", tmp_path / "absent.json",
        None, (),
    )
    with pytest.raises(BuildError, match="declaration have to travel together"):
        read_sections(source, rules_for("afu.prostate.2024-2026"))
