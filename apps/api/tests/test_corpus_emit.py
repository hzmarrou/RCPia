"""Writing a release, and refusing it here rather than at the loader.

Every check below is the loader's own code running against freshly written files. That is the
whole point of the stage: the same authority, ten minutes earlier.
"""

import json
from pathlib import Path

import pytest

from rcpia.corpus.emit import (
    CHUNKS_FILENAME,
    DOCUMENTS_FILENAME,
    SCHEMA_FILENAME,
    DocumentSpec,
    EmitError,
    RoutedPassage,
    build_schema,
    emit_release,
    self_check,
)
from rcpia.corpus.split import Passage
from rcpia.corpus.vocabulary import AGENT_ROLES, DISEASE_SETTINGS
from rcpia.evidence.manifest import MANIFEST_FILENAME, ManifestError

SPEC = DocumentSpec(
    document_id="afu.bladder-tvnim.2024-2026",
    ref_id="ref-03",
    title="AFU Vessie TVNIM 2024-2026",
    short_title="AFU TVNIM",
    publisher="Association française d'urologie",
    authority="professional_society",
    document_type="guideline",
    language="fr",
    version_label="2024-2026",
    source_files=("ref-03/Vessie_TVNIM/document.json",),
    source_family="afu",
    source_role="treatment_guideline",
    position_domains=("therapy_indication", "risk_stratification"),
    cancer_types=("bladder",),
    agent_roles=("urology_oncology",),
    licence_status="permission_user_attested",
)

PROSE = RoutedPassage(
    Passage("Le BCG est indiqué dans les tumeurs de haut risque.", "prose"),
    ("Classification pronostique", "Tumeurs de haut risque"),
    "bladder_nmibc_high_risk",
    None,
    ("therapy_indication",),
    page_first=3,
    page_last=4,
    page_confidence=0.91,
)
TABLE = RoutedPassage(
    Passage("| Stade | T1 |\n| Stade | T2 |", "table"),
    ("Annexes et tableaux",),
    "not_specified",
    "general_principle",
)
REFERENCES = RoutedPassage(
    Passage("| [1] | Une référence |\n| [2] | Une autre |", "references"),
    ("Annexes et tableaux",),
    "not_specified",
    "general_principle",
)


def write(tmp_path: Path, routed: list[RoutedPassage] | None = None) -> Path:
    directory = tmp_path / "rcpia-corpus-test"
    emit_release(
        directory,
        release_id="rcpia-corpus-test",
        documents=[(SPEC, routed if routed is not None else [PROSE, TABLE, REFERENCES])],
        folders={"ref-03": "accounted"},
        generated_at="2026-08-24T00:00:00Z",
    )
    return directory


def rows(directory: Path, name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (directory / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_a_release_is_written_and_accepted_by_the_loaders_own_checks(tmp_path: Path) -> None:
    directory = write(tmp_path)
    assert {p.name for p in directory.iterdir()} == {
        DOCUMENTS_FILENAME,
        CHUNKS_FILENAME,
        SCHEMA_FILENAME,
        MANIFEST_FILENAME,
    }


def test_a_release_with_no_documents_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EmitError, match="proves nothing"):
        emit_release(
            tmp_path / "empty",
            release_id="x",
            documents=[],
            folders={},
            generated_at="2026-08-24T00:00:00Z",
        )


def test_a_reference_list_is_kept_and_never_retrieved(tmp_path: Path) -> None:
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    refs = [c for c in chunks if c["unit_type"] == "reference_list"]
    assert len(refs) == 1
    assert refs[0]["retrieval_eligible"] is False
    assert refs[0]["retrieval_exclusion_reason"] == "bibliography_entry"
    # Kept, not dropped -- the text is still there to be traced back to.
    assert refs[0]["text"]


def test_a_table_is_marked_so_retrieval_can_treat_it_differently(tmp_path: Path) -> None:
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    assert any(c["unit_type"] == "decision_table" for c in chunks)


def test_a_decision_table_is_never_embedded(tmp_path: Path) -> None:
    # The vocabulary defines `decision_table` as a unit kept out of the embedding leg: its
    # numbers are exact tokens, and a vector cannot tell `PSA < 10` from `PSA > 20`. Until v5
    # the value was assigned and nothing acted on it.
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    tables = [c for c in chunks if c["unit_type"] == "decision_table"]
    assert tables, "the fixture should contain a table"
    assert all(c["embedding_eligible"] is False for c in tables)


def test_a_decision_table_still_answers_questions(tmp_path: Path) -> None:
    # Keyword-only is not exclusion. `retrieval_eligible` stays true, and the full-text leg is
    # exactly where a table of bands and cutoffs belongs.
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    tables = [c for c in chunks if c["unit_type"] == "decision_table"]
    assert all(c["retrieval_eligible"] is True for c in tables)


def test_prose_is_embedded(tmp_path: Path) -> None:
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    prose = [c for c in chunks if c["unit_type"] == "general_principle"]
    assert prose
    assert all(c["embedding_eligible"] is True for c in prose)


def test_a_citation_names_pages_only_when_they_were_found(tmp_path: Path) -> None:
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    located = next(c for c in chunks if c["section_path"][-1] == "Tumeurs de haut risque")
    assert "pp. 3-4" in located["citation"]

    unlocated = next(c for c in chunks if c["unit_type"] == "decision_table")
    # A citation carrying a page nobody located is worse than one carrying none.
    assert "p." not in unlocated["citation"]
    assert "Annexes et tableaux" in unlocated["citation"]


def test_the_embedded_text_carries_the_path_it_sits_under(tmp_path: Path) -> None:
    # "BCG est indiqué" means nothing alone; under "Tumeurs de haut risque" it is about a patient.
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    located = next(c for c in chunks if c["section_path"][-1] == "Tumeurs de haut risque")
    assert located["contextual_text"].startswith("Classification pronostique > Tumeurs")
    assert located["text"] in located["contextual_text"]


def test_chunk_ids_are_unique(tmp_path: Path) -> None:
    chunks = rows(write(tmp_path), CHUNKS_FILENAME)
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)


def test_the_release_states_its_own_vocabulary(tmp_path: Path) -> None:
    # The loader validates against the release's schema, so a value nobody implements must not
    # be able to enter by being inherited from the previous release.
    schema = json.loads((write(tmp_path) / SCHEMA_FILENAME).read_text(encoding="utf-8"))
    assert set(schema["properties"]["disease_setting"]["enum"]) == DISEASE_SETTINGS
    assert set(schema["properties"]["agent_roles"]["items"]["enum"]) == AGENT_ROLES


def test_the_schema_is_built_from_the_vocabulary_not_a_literal() -> None:
    assert set(build_schema()["properties"]["disease_setting"]["enum"]) == DISEASE_SETTINGS


def test_a_document_the_manifest_never_mentions_is_refused(tmp_path: Path) -> None:
    # Content entering the corpus that no coverage proof accounts for.
    directory = write(tmp_path)
    documents = rows(directory, DOCUMENTS_FILENAME)
    documents.append({**documents[0], "document_id": "smuggled.document.2026"})
    with pytest.raises(ManifestError, match="smuggled.document.2026"):
        self_check(directory, document_rows=documents, chunk_counts={})


def test_a_declared_document_that_did_not_arrive_is_refused(tmp_path: Path) -> None:
    # The EAU bladder case -- the whole reason the manifest exists.
    directory = write(tmp_path)
    with pytest.raises(ManifestError, match="does not deliver"):
        self_check(directory, document_rows=[], chunk_counts={})


def test_tampering_with_a_written_file_is_caught(tmp_path: Path) -> None:
    directory = write(tmp_path)
    path = directory / CHUNKS_FILENAME
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    documents = rows(directory, DOCUMENTS_FILENAME)
    with pytest.raises(ManifestError):
        self_check(
            directory,
            document_rows=documents,
            chunk_counts={SPEC.document_id: 3},
        )
