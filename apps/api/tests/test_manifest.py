"""The coverage proof, and what it refuses.

Every other guard in the loader asks whether what arrived is well-formed. These ask whether
everything that should have arrived did — the question nothing asked for five releases, while
two parsed EAU bladder guidelines sat beside the EAU prostate file that was loaded and never
came.
"""

import json
from pathlib import Path

import pytest

from rcpia.evidence.manifest import (
    ManifestError,
    load_manifest,
    verify_against_release,
    verify_checksums,
)

ROLES = {
    "treatment_guideline": {"position_bearing": True},
    "specialist_guidance": {"position_bearing": False},
}


def reference(
    reference_id: str = "eau-2026-prostate",
    *,
    files: list[str] | None = None,
    source_role: str | None = "treatment_guideline",
    position_bearing: bool | None = True,
    dispositions: list[dict] | None = None,
    concerns: list[str] | None = None,
) -> dict:
    entry: dict = {
        "reference_id": reference_id,
        "files": files if files is not None else [f"ref-01/{reference_id}.md"],
        "dispositions": (
            dispositions
            if dispositions is not None
            else [{"kind": "corpus", "document_id": "eau.prostate.2026", "chunks": 443}]
        ),
    }
    if source_role is not None:
        entry["source_role"] = source_role
    if position_bearing is not None:
        entry["position_bearing"] = position_bearing
    if concerns is not None:
        entry["concerns"] = concerns
    return entry


def write(tmp_path: Path, document: dict | str | None) -> Path:
    directory = tmp_path / "release"
    directory.mkdir(exist_ok=True)
    if document is not None:
        text = document if isinstance(document, str) else json.dumps(document)
        (directory / "manifest.json").write_text(text, encoding="utf-8")
    return directory


def manifest(tmp_path: Path, **overrides: object) -> Path:
    document: dict = {
        "schema_version": "1.0",
        "release_id": "rcpia-corpus-v5.0",
        "source_roles": ROLES,
        "reference_folders": [
            {"folder": "ref-01", "status": "accounted"},
            {"folder": "ref-26", "status": "empty", "note": "Confirmed empty"},
        ],
        "references": [reference()],
    }
    document.update(overrides)
    return write(tmp_path, document)


# --------------------------------------------------------------------------------------
# The manifest must exist, and must hold together on its own
# --------------------------------------------------------------------------------------


def test_a_release_without_a_manifest_is_refused(tmp_path: Path) -> None:
    """Refused, not warned about. A warning is what the last five releases would have
    produced, and nobody would have read it."""
    with pytest.raises(ManifestError, match="manifest.json is missing"):
        load_manifest(write(tmp_path, None))


def test_a_manifest_that_is_not_json_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(write(tmp_path, "{ not json"))


def test_a_folder_with_no_status_is_refused(tmp_path: Path) -> None:
    """A folder is accounted for or declared empty. "Nothing here" and "nobody looked" must
    not render identically — the whole point of the file."""
    with pytest.raises(ManifestError, match="status None"):
        load_manifest(manifest(tmp_path, reference_folders=[{"folder": "ref-02"}]))


def test_a_reference_with_no_disposition_is_refused(tmp_path: Path) -> None:
    """A reference nobody decided about is indistinguishable from one nobody saw."""
    with pytest.raises(ManifestError, match="declares no disposition"):
        load_manifest(manifest(tmp_path, references=[reference(dispositions=[])]))


def test_an_unknown_disposition_kind_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="disposition 'maybe'"):
        load_manifest(
            manifest(tmp_path, references=[reference(dispositions=[{"kind": "maybe"}])])
        )


@pytest.mark.parametrize(
    ("kind", "field"),
    [("tool", "tool_id"), ("calculator", "calculator_id"), ("excluded", "reason")],
)
def test_each_disposition_must_carry_its_own_field(
    tmp_path: Path, kind: str, field: str
) -> None:
    with pytest.raises(ManifestError, match=f"{kind} disposition without {field}"):
        load_manifest(
            manifest(tmp_path, references=[reference(dispositions=[{"kind": kind}])])
        )


def test_a_corpus_disposition_without_a_chunk_count_is_refused(tmp_path: Path) -> None:
    """So a document delivered empty is visible as a defect rather than as an absence."""
    with pytest.raises(ManifestError, match="declares chunks=None"):
        load_manifest(
            manifest(
                tmp_path,
                references=[
                    reference(dispositions=[{"kind": "corpus", "document_id": "eau.x"}])
                ],
            )
        )


def test_a_file_claimed_twice_is_refused(tmp_path: Path) -> None:
    """A file claimed twice is a document ingested twice under two ids — the opposite
    failure to a missing document, and just as invisible."""
    shared = ["ref-21/GGPO_Psycho_Oncology_Longversion_2.0.md"]
    with pytest.raises(ManifestError, match="claimed by both"):
        load_manifest(
            manifest(
                tmp_path,
                references=[
                    reference("ggpo-21", files=shared),
                    reference("ggpo-22", files=shared),
                ],
            )
        )


def test_a_file_may_be_claimed_by_an_artifact_instead(tmp_path: Path) -> None:
    """Validation JSON and collection reports are physical files that are not clinical
    references. Without the artifacts bucket they would have to be mislabelled."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            artifacts=[
                {
                    "artifact_id": "tnm-validation-cases",
                    "files": ["ref-19/validation_cases.json"],
                    "kind": "validation_data",
                }
            ],
        )
    )
    assert loaded.release_id == "rcpia-corpus-v5.0"


def test_a_file_claimed_by_both_a_reference_and_an_artifact_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(ManifestError, match="claimed by both"):
        load_manifest(
            manifest(
                tmp_path,
                artifacts=[
                    {"artifact_id": "a", "files": ["ref-01/eau-2026-prostate.md"]}
                ],
            )
        )


def test_two_references_claiming_one_document_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="claimed by more than one reference"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference("a"), reference("b", files=["ref-01/b.md"])],
            )
        )


# --------------------------------------------------------------------------------------
# source_role decides position_bearing, and they must agree
# --------------------------------------------------------------------------------------


def test_position_bearing_must_match_the_declared_role(tmp_path: Path) -> None:
    """Two fields that must agree are two fields that can disagree.

    The consequence is not cosmetic. A specialist reference treated as a peer of AFU, EAU and
    NCCN makes the comparison step report a divergence that does not exist — SIOG is not
    disagreeing about treatment, it is answering a different question — and a false conflict
    shown to a clinician is the failure most likely to end their trust in it.
    """
    with pytest.raises(ManifestError, match="position_bearing=True but source_role"):
        load_manifest(
            manifest(
                tmp_path,
                references=[
                    reference(source_role="specialist_guidance", position_bearing=True)
                ],
            )
        )


def test_a_role_the_mapping_does_not_define_is_refused(tmp_path: Path) -> None:
    """A role we do not recognise is a question for the corpus agent, not a silent default."""
    with pytest.raises(ManifestError, match="source_role 'expert_opinion'"):
        load_manifest(
            manifest(
                tmp_path,
                references=[
                    reference(source_role="expert_opinion", position_bearing=True)
                ],
            )
        )


def test_position_bearing_without_a_role_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="without a source_role"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference(source_role=None, position_bearing=True)],
            )
        )


def test_a_role_mapping_without_a_boolean_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="must declare position_bearing"):
        load_manifest(
            manifest(tmp_path, source_roles={"treatment_guideline": {"note": "yes"}})
        )


# --------------------------------------------------------------------------------------
# The manifest against what the release actually delivers
# --------------------------------------------------------------------------------------


def test_a_declared_document_that_did_not_arrive_is_refused(tmp_path: Path) -> None:
    """**This is the EAU bladder case**, and the whole reason the module exists. Two parsed
    guidelines, same folder and same edition as one that was loaded, and they never came."""
    loaded = load_manifest(manifest(tmp_path))
    with pytest.raises(ManifestError, match="does not deliver: eau.prostate.2026"):
        verify_against_release(loaded, delivered_documents=set(), chunk_counts={})


def test_a_delivered_document_the_manifest_omits_is_refused(tmp_path: Path) -> None:
    """A coverage proof that omits delivered content proves nothing about its release."""
    loaded = load_manifest(manifest(tmp_path))
    with pytest.raises(ManifestError, match="does not account for: nccn.prostate.6.2026"):
        verify_against_release(
            loaded,
            delivered_documents={"eau.prostate.2026", "nccn.prostate.6.2026"},
            chunk_counts={"eau.prostate.2026": 443, "nccn.prostate.6.2026": 328},
        )


def test_a_chunk_count_that_does_not_match_is_refused(tmp_path: Path) -> None:
    loaded = load_manifest(manifest(tmp_path))
    with pytest.raises(ManifestError, match="declares 443 chunks, delivers 12"):
        verify_against_release(
            loaded,
            delivered_documents={"eau.prostate.2026"},
            chunk_counts={"eau.prostate.2026": 12},
        )


def test_a_matching_release_passes(tmp_path: Path) -> None:
    loaded = load_manifest(manifest(tmp_path))
    verify_against_release(
        loaded,
        delivered_documents={"eau.prostate.2026"},
        chunk_counts={"eau.prostate.2026": 443},
    )
    assert loaded.declared_chunks == 443


def test_a_null_document_id_declares_a_gap_rather_than_a_document(tmp_path: Path) -> None:
    """The interpretive half of an instrument usually lives in a different reference — what
    an abnormal G8 implies is not in the questionnaire. A null id records that the guidance
    should exist and does not, which is a visible gap rather than a document to look for."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(
                    "g8",
                    dispositions=[
                        {"kind": "tool", "tool_id": "g8"},
                        {
                            "kind": "corpus",
                            "document_id": None,
                            "covers": "what an abnormal G8 implies",
                        },
                    ],
                )
            ],
        )
    )
    assert loaded.corpus_entries == {}
    verify_against_release(loaded, delivered_documents=set(), chunk_counts={})


# --------------------------------------------------------------------------------------
# Gaps, concerns and checksums
# --------------------------------------------------------------------------------------


def test_declared_gaps_are_carried(tmp_path: Path) -> None:
    """A documented gap is a working outcome. "No renal source" as a line in a file beats
    nobody having noticed for five releases."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            gaps=[
                {"gap_id": "renal-platinum-eligibility", "status": "source_not_selected"}
            ],
        )
    )
    assert loaded.declared_gaps == ["renal-platinum-eligibility"]


def test_concerns_are_carried_with_their_reference(tmp_path: Path) -> None:
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[reference(concerns=["2010 edition; 17 chunks blocked downstream"])],
        )
    )
    assert loaded.concerns == [
        "eau-2026-prostate: 2010 edition; 17 chunks blocked downstream"
    ]


def test_a_wrong_checksum_is_refused(tmp_path: Path) -> None:
    """v3 and v3.1 carried one and v4 did not. It has caught nothing so far, which is what a
    checksum is for."""
    directory = manifest(
        tmp_path, checksums={"algorithm": "sha256", "files": {"chunks.jsonl": "00" * 32}}
    )
    (directory / "chunks.jsonl").write_text("{}", encoding="utf-8")
    loaded = load_manifest(directory)
    with pytest.raises(ManifestError, match="does not match the checksum"):
        verify_checksums(loaded, directory)


def test_a_checksum_for_a_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    directory = manifest(
        tmp_path, checksums={"algorithm": "sha256", "files": {"absent.jsonl": "00" * 32}}
    )
    loaded = load_manifest(directory)
    with pytest.raises(ManifestError, match="which the release does not contain"):
        verify_checksums(loaded, directory)


def test_a_correct_checksum_passes(tmp_path: Path) -> None:
    import hashlib

    directory = write(tmp_path, None)
    payload = b'{"chunk_id": "a"}'
    (directory / "chunks.jsonl").write_bytes(payload)
    directory = manifest(
        tmp_path,
        checksums={
            "algorithm": "sha256",
            "files": {"chunks.jsonl": hashlib.sha256(payload).hexdigest()},
        },
    )
    verify_checksums(load_manifest(directory), directory)
