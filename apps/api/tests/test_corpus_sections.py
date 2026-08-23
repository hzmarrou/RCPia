"""Section fixtures — a document's headings, committed so CI can check rules without sources.

The sources are 18.7 MB of markdown in OneDrive that CI cannot reach. Without a fixture, a rule
naming a heading renamed two guideline revisions ago passes review and fails silently at build
time. With one, the check runs on every pull request.
"""

import json
from pathlib import Path

import pytest

from rcpia.corpus.rules import parse_rules
from rcpia.corpus.sections import (
    SectionError,
    check,
    fixture_path,
    read_chunks,
    read_fixture,
    section_paths,
    write_fixture,
)

PATHS = [
    ["Traitement", "TVNIM à très haut risque"],
    ["Traitement", "TVNIM à haut risque"],
    ["Traitement", "TVNIM à haut risque"],
]

RULES = {
    "document_id": "afu.bladder-tvnim.2024-2026",
    "default": {"setting": "bladder_nmibc_unspecified_risk"},
    "rules": [
        {"match_section": "tres haut risque", "setting": "bladder_nmibc_very_high_risk"},
        {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
    ],
}


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------


def test_repetitions_are_preserved_rather_than_deduplicated() -> None:
    """**The repetitions are the point.** A set of distinct paths would be smaller and would
    destroy the one thing `occurrence` exists for — the same heading appearing twice."""
    chunks: list[dict[str, object]] = [{"section_path": path} for path in PATHS]

    assert section_paths(chunks) == PATHS


def test_order_is_preserved() -> None:
    """Ambiguity is detected from how far apart two occurrences sit, so order carries meaning
    and cannot be normalised away."""
    chunks: list[dict[str, object]] = [
        {"section_path": ["B"]},
        {"section_path": ["A"]},
    ]

    assert section_paths(chunks) == [["B"], ["A"]]


def test_a_chunk_without_a_section_path_is_refused() -> None:
    """It cannot be routed, and silently skipping it would shrink the document the rules are
    checked against — making a dead rule look live."""
    with pytest.raises(SectionError, match="no section_path"):
        section_paths([{"text": "..."}])


def test_a_malformed_section_path_is_refused() -> None:
    with pytest.raises(SectionError, match="list of strings"):
        section_paths([{"section_path": "Traitement > Cystectomie"}])


# --------------------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------------------


def test_a_fixture_round_trips_including_punctuation(tmp_path: Path) -> None:
    """Headings contain every character a delimiter might use. EAU numbers its sections
    `5.3.5`, NCCN uses `|` in tables, and French headings carry colons and accents — which is
    why the format is JSON per line rather than a delimited file."""
    awkward = [
        ["5.3 Pathology > diagnosis", "5.3.5 Histopathology: grading | staging"],
        ["Traitement", "Chimiothérapie néoadjuvante à base de cisplatine"],
    ]
    destination = tmp_path / "x.sections.jsonl"

    write_fixture(awkward, destination)

    assert read_fixture(destination) == awkward


def test_a_missing_fixture_says_what_to_do(tmp_path: Path) -> None:
    """Silence here would mean the rules were checked against nothing, which reads as a clean
    audit."""
    with pytest.raises(SectionError, match="is missing"):
        read_fixture(tmp_path / "absent.sections.jsonl")


def test_an_empty_fixture_is_refused(tmp_path: Path) -> None:
    """**Every rule would be dead and every audit vacuous** — and an audit with nothing to say
    looks exactly like an audit with nothing wrong."""
    destination = tmp_path / "x.sections.jsonl"
    destination.write_text("", encoding="utf-8")

    with pytest.raises(SectionError, match="holds no headings"):
        read_fixture(destination)


def test_a_malformed_fixture_line_names_its_line_number(tmp_path: Path) -> None:
    destination = tmp_path / "x.sections.jsonl"
    destination.write_text('["A"]\nnot json\n', encoding="utf-8")

    with pytest.raises(SectionError, match="line 2"):
        read_fixture(destination)


def test_blank_lines_are_tolerated(tmp_path: Path) -> None:
    """A file edited by hand or written on Windows should not fail for a trailing newline."""
    destination = tmp_path / "x.sections.jsonl"
    destination.write_text('["A"]\n\n["B"]\n', encoding="utf-8")

    assert read_fixture(destination) == [["A"], ["B"]]


# --------------------------------------------------------------------------------------
# Reading a release
# --------------------------------------------------------------------------------------


def test_chunks_stream_from_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "chunks.jsonl"
    source.write_text(
        "\n".join(json.dumps({"section_path": path}, ensure_ascii=False) for path in PATHS),
        encoding="utf-8",
    )

    assert section_paths(read_chunks(source)) == PATHS


def test_a_malformed_release_line_names_its_line_number(tmp_path: Path) -> None:
    source = tmp_path / "chunks.jsonl"
    source.write_text('{"section_path": ["A"]}\n{oops\n', encoding="utf-8")

    with pytest.raises(SectionError, match="line 2"):
        list(read_chunks(source))


# --------------------------------------------------------------------------------------
# What CI actually runs
# --------------------------------------------------------------------------------------


def test_rules_are_checked_against_the_committed_headings(tmp_path: Path) -> None:
    """No database, no corpus release, no source PDF — the rule file and the fixture, both in
    the repository."""
    write_fixture(PATHS, fixture_path(tmp_path, "afu.bladder-tvnim.2024-2026"))

    report = check(parse_rules(RULES), sections=tmp_path)

    assert report.problems == []
    assert report.routed == 3
    assert report.defaulted == 0


def test_a_renamed_heading_surfaces_as_a_dead_rule(tmp_path: Path) -> None:
    """**The failure this whole module exists to catch.** The guideline is revised, a heading
    changes, and the rule keeps parsing perfectly while routing nothing."""
    renamed = [["Traitement", "TVNIM de très haut risque"]]  # "à" became "de"
    write_fixture(renamed, fixture_path(tmp_path, "afu.bladder-tvnim.2024-2026"))

    rules = parse_rules(
        {
            **RULES,
            "rules": [
                {
                    "match_section": "TVNIM à très haut risque",
                    "setting": "bladder_nmibc_very_high_risk",
                }
            ],
        }
    )
    report = check(rules, sections=tmp_path)

    assert [r.match_section for r in report.dead] == ["TVNIM à très haut risque"]
    assert "matches no heading" in report.problems[0]
