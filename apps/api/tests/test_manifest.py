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
    SOURCE_ROLES_VERSION,
    ManifestError,
    load_manifest,
    verify_against_release,
    verify_checksums,
)


def reference(
    reference_id: str = "eau-2026-prostate",
    *,
    files: list[str] | None = None,
    source_role: str | None = "treatment_guideline",
    position_domains: list[str] | None = None,
    dispositions: list[dict] | None = None,
    concerns: list[str] | None = None,
) -> dict:
    entry: dict = {
        "reference_id": reference_id,
        "source_family": "EAU",
        "files": files if files is not None else [f"ref-01/{reference_id}.md"],
        "dispositions": (
            dispositions
            if dispositions is not None
            else [{"kind": "corpus", "document_id": "eau.prostate.2026", "chunks": 443}]
        ),
    }
    if source_role is not None:
        entry["source_role"] = source_role
        # A role implies domains; omitting both is the "unstated kind" case a test asks for
        # deliberately.
        entry["position_domains"] = (
            position_domains if position_domains is not None else ["therapy_indication"]
        )
    elif position_domains is not None:
        entry["position_domains"] = position_domains
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


# A well-formed checksum block. Required now: one that names none of the release's own files
# succeeds while verifying nothing, which reads identically to a release that was verified.
CHECKSUMS = {
    "algorithm": "sha256",
    "release": "a" * 64,
    "files": {
        "documents.jsonl": "b" * 64,
        "chunks.jsonl": "c" * 64,
        "chunk.schema.json": "d" * 64,
    },
}


def manifest(tmp_path: Path, **overrides: object) -> Path:
    document: dict = {
        "schema_version": "1.0",
        "release_id": "rcpia-corpus-v5.0",
        "source_roles_version": SOURCE_ROLES_VERSION,
        "reference_folders": [
            {"folder": "ref-01", "status": "accounted"},
            {"folder": "ref-26", "status": "empty", "note": "Confirmed empty"},
        ],
        "references": [reference()],
        "checksums": CHECKSUMS,
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
            reference_folders=[
                {"folder": "ref-01", "status": "accounted"},
                {"folder": "ref-19", "status": "accounted"},
            ],
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


def test_a_domain_the_release_does_not_recognise_is_refused(tmp_path: Path) -> None:
    """**The role no longer constrains this, and that is deliberate.**

    A previous version had `treatment_guideline` permit exactly three treatment domains — a
    mapping invented here that no clinician had seen, and wrong on its face, since AFU, EAU and
    NCCN also hold positions on risk stratification, follow-up, safety, diagnosis and staging.
    An unreviewed role acting as a clinical ceiling is a scope decision made by omission.

    What is checkable without deciding anything clinical: a domain must name a purpose the
    release's own chunk schema declares, because a domain no chunk can carry is not a claim
    about anything.
    """
    with pytest.raises(ManifestError, match="claims positions in invented_purpose"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference(position_domains=["invented_purpose"])],
            ),
            purposes=frozenset({"therapy_indication", "supportive_care"}),
        )


def test_a_comprehensive_guideline_may_claim_any_real_purpose(tmp_path: Path) -> None:
    """The case the ceiling got wrong. AFU, EAU and NCCN are comprehensive."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(
                    source_role="treatment_guideline",
                    position_domains=["therapy_indication", "follow_up", "staging"],
                )
            ],
        ),
        purposes=frozenset({"therapy_indication", "follow_up", "staging"}),
    )
    assert loaded.corpus_entries["eau.prostate.2026"].position_domains == (
        "therapy_indication",
        "follow_up",
        "staging",
    )


def test_an_empty_domain_string_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="non-empty clinical purpose"):
        load_manifest(
            manifest(tmp_path, references=[reference(position_domains=["  "])])
        )


def test_a_repeated_domain_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="repeats a position domain"):
        load_manifest(
            manifest(
                tmp_path,
                references=[
                    reference(position_domains=["therapy_indication", "therapy_indication"])
                ],
            )
        )


def test_a_role_covering_no_domain_may_claim_none(tmp_path: Path) -> None:
    """WHO/IARC and RECIST hold positions nowhere. An empty list is a statement; omitting the
    field would be an absence."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(source_role="classification_standard", position_domains=[])
            ],
        )
    )
    assert loaded.corpus_entries["eau.prostate.2026"].position_domains == ()


def test_espen_holds_positions_without_holding_one_on_treatment(tmp_path: Path) -> None:
    """The case the boolean got wrong. `position_bearing: false` reads as "holds no
    position", and ESPEN has plenty — on nutritional support, not on treatment choice."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(
                    source_role="specialist_guidance",
                    position_domains=["supportive_care", "safety"],
                )
            ],
        )
    )
    assert loaded.corpus_entries["eau.prostate.2026"].position_domains == (
        "supportive_care",
        "safety",
    )


def test_a_role_the_mapping_does_not_define_is_refused(tmp_path: Path) -> None:
    """A role we do not recognise is a question for the corpus agent, not a silent default."""
    with pytest.raises(ManifestError, match="source_role 'expert_opinion'"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference(source_role="expert_opinion")],
            )
        )


def test_domains_without_a_role_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="without a source_role"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference(source_role=None, position_domains=["safety"])],
            )
        )


def test_a_release_defining_its_own_roles_is_refused(tmp_path: Path) -> None:
    """A release that defines both a role and its meaning validates itself. The meaning of
    `position_bearing` decides how the comparison step behaves, so RCPia owns the table."""
    with pytest.raises(ManifestError, match="declares its own source_roles"):
        load_manifest(
            manifest(tmp_path, source_roles={"expert_opinion": {"position_bearing": True}})
        )


def test_a_manifest_built_against_another_vocabulary_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="source_roles_version"):
        load_manifest(manifest(tmp_path, source_roles_version="0.9"))


def test_a_corpus_reference_without_source_metadata_is_refused(tmp_path: Path) -> None:
    """A source whose kind is unstated is routed as though it were a treatment guideline."""
    with pytest.raises(ManifestError, match="enters the corpus without"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference(source_role=None)],
            )
        )


def test_a_tool_reference_needs_no_source_metadata(tmp_path: Path) -> None:
    """A tool, a calculator or an excluded reference has nothing for retrieval to route."""
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(
                    "g8",
                    source_role=None,
                    dispositions=[{"kind": "tool", "tool_id": "g8"}],
                )
            ],
        )
    )
    assert loaded.corpus_entries == {}


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
    import hashlib

    empty = hashlib.sha256(b"").hexdigest()
    directory = manifest(
        tmp_path,
        checksums={
            **CHECKSUMS,
            "files": {
                "documents.jsonl": empty,
                "chunk.schema.json": empty,
                "chunks.jsonl": "0" * 64,
            },
        },
    )
    for name in ("documents.jsonl", "chunk.schema.json"):
        (directory / name).write_bytes(b"")
    (directory / "chunks.jsonl").write_text("{}", encoding="utf-8")
    loaded = load_manifest(directory)
    with pytest.raises(ManifestError, match="does not match the checksum"):
        verify_checksums(loaded, directory)


def test_a_checksum_for_a_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    directory = manifest(
        tmp_path,
        checksums={**CHECKSUMS, "files": {**CHECKSUMS["files"], "absent.jsonl": "0" * 64}},
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
            **CHECKSUMS,
            "files": {
                **CHECKSUMS["files"],
                "chunks.jsonl": hashlib.sha256(payload).hexdigest(),
            },
        },
    )
    # The other declared files are absent, so only the one under test is checked here.
    for name in ("documents.jsonl", "chunk.schema.json"):
        (directory / name).write_bytes(b"")
    loaded = load_manifest(directory)
    with pytest.raises(ManifestError, match="does not match"):
        verify_checksums(loaded, directory)
    # And with every digest right, it passes.
    empty = hashlib.sha256(b"").hexdigest()
    directory = manifest(
        tmp_path,
        checksums={
            **CHECKSUMS,
            "files": {
                "documents.jsonl": empty,
                "chunk.schema.json": empty,
                "chunks.jsonl": hashlib.sha256(payload).hexdigest(),
            },
        },
    )
    verify_checksums(load_manifest(directory), directory)


# --------------------------------------------------------------------------------------
# Folder coverage — the largest version of an omitted file
# --------------------------------------------------------------------------------------


def test_an_accounted_folder_nothing_claims_from_is_refused(tmp_path: Path) -> None:
    """The reviewer's blocker: "every physical file appears exactly once" was enforced only
    against files the manifest listed, so an omitted file was invisible — the manifest's own
    founding failure, inside the module written to fix it.

    A loader cannot enumerate a library it never sees. What it can catch is the shape: a
    folder declared accounted that nothing claims a file from is either empty and mislabelled,
    or forgotten. That is the wholly-omitted-folder case, the largest version of the failure.
    """
    with pytest.raises(ManifestError, match="ref-02 declared accounted"):
        load_manifest(
            manifest(
                tmp_path,
                reference_folders=[
                    {"folder": "ref-01", "status": "accounted"},
                    {"folder": "ref-02", "status": "accounted"},
                ],
            )
        )


def test_a_folder_declared_empty_that_files_come_from_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="ref-01 declared empty"):
        load_manifest(
            manifest(
                tmp_path,
                reference_folders=[{"folder": "ref-01", "status": "empty"}],
            )
        )


def test_a_file_from_an_undeclared_folder_is_refused(tmp_path: Path) -> None:
    """The folder list is the coverage claim. A file from outside it means the claim is
    incomplete."""
    with pytest.raises(ManifestError, match="ref-99"):
        load_manifest(
            manifest(
                tmp_path,
                references=[reference(files=["ref-99/stray.md"])],
                reference_folders=[{"folder": "ref-99", "status": "empty"}],
            )
        )


# --------------------------------------------------------------------------------------
# Unknown fields — the pattern this module was written to fix, and repeated
# --------------------------------------------------------------------------------------


def test_an_unknown_top_level_field_is_refused(tmp_path: Path) -> None:
    """The module built to stop silent discard read the keys it knew and ignored the rest."""
    with pytest.raises(ManifestError, match="does not implement: coverage_summary"):
        load_manifest(manifest(tmp_path, coverage_summary={"folders": 27}))


def test_an_unknown_reference_field_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="does not implement: curation_state"):
        load_manifest(
            manifest(
                tmp_path,
                references=[{**reference(), "curation_state": "requires curation"}],
            )
        )


def test_a_misspelled_field_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """`dispositons` would otherwise read as a reference with no disposition at all — a
    different error, pointing somewhere else."""
    entry = reference()
    entry["dispositons"] = entry.pop("dispositions")
    with pytest.raises(ManifestError, match="dispositons"):
        load_manifest(manifest(tmp_path, references=[entry]))


def test_an_unknown_disposition_field_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="does not implement: retrieval_eligible"):
        load_manifest(
            manifest(
                tmp_path,
                references=[
                    reference(
                        dispositions=[
                            {
                                "kind": "corpus",
                                "document_id": "eau.prostate.2026",
                                "chunks": 443,
                                "retrieval_eligible": True,
                            }
                        ]
                    )
                ],
            )
        )


def test_a_manifest_of_another_schema_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="schema_version"):
        load_manifest(manifest(tmp_path, schema_version="2.0"))


# --------------------------------------------------------------------------------------
# What "declared" has to mean
# --------------------------------------------------------------------------------------


def test_a_null_document_id_needs_no_source_metadata(tmp_path: Path) -> None:
    """A corpus disposition with no document id is a *declared gap*, not delivered content.

    The interpretive half of an instrument usually lives in a different reference — what an
    abnormal G8 implies is not in the questionnaire. Nothing is delivered, so there is nothing
    for retrieval to route and nothing to describe, and requiring a family and a role would
    force the corpus agent to invent them.
    """
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(
                    "g8-interpretation",
                    source_role=None,
                    dispositions=[
                        {
                            "kind": "corpus",
                            "document_id": None,
                            "covers": "what an abnormal G8 implies",
                        }
                    ],
                )
            ],
        )
    )
    assert loaded.corpus_entries == {}


def test_an_empty_source_family_is_refused(tmp_path: Path) -> None:
    """`""` satisfies "is not None" and then vanishes from family-stratified retrieval without
    a word — the passages load, and no family search ever names them."""
    entry = reference()
    entry["source_family"] = "   "
    with pytest.raises(ManifestError, match="enters the corpus without source_family"):
        load_manifest(manifest(tmp_path, references=[entry]))


def test_the_declared_folders_field_is_what_counts(tmp_path: Path) -> None:
    """It was allowlisted and ignored, with coverage inferred from slashes in file names.

    The contract allows bare filenames beside a separate folder list — and a reference drawn
    from two folders, which the GGPO and Mini-GDS duplicates are, cannot be expressed by a path
    prefix at all.
    """
    loaded = load_manifest(
        manifest(
            tmp_path,
            references=[
                reference(
                    "ggpo",
                    files=["GGPO_Psycho_Oncology.md", "GGPO_Psycho-oncology.md"],
                )
                | {"folders": ["ref-01", "ref-22"]}
            ],
            reference_folders=[
                {"folder": "ref-01", "status": "accounted"},
                {"folder": "ref-22", "status": "accounted"},
            ],
        )
    )
    assert loaded.folders == {"ref-01": "accounted", "ref-22": "accounted"}


def test_a_folder_list_that_is_not_a_list_is_refused(tmp_path: Path) -> None:
    entry = reference()
    entry["folders"] = "ref-01"
    with pytest.raises(ManifestError, match="declares folders as"):
        load_manifest(manifest(tmp_path, references=[entry]))


# --------------------------------------------------------------------------------------
# Checksums — the field whose whole job is to make tampering loud
# --------------------------------------------------------------------------------------


def test_checksums_without_an_algorithm_are_refused(tmp_path: Path) -> None:
    """A manifest carrying no usable checksum looked exactly like one that had been verified."""
    with pytest.raises(ManifestError, match="declares no algorithm"):
        load_manifest(
            manifest(tmp_path, checksums={**CHECKSUMS, "algorithm": None})
        )


def test_an_algorithm_we_do_not_verify_is_refused(tmp_path: Path) -> None:
    """Ignoring it would verify nothing while appearing to."""
    with pytest.raises(ManifestError, match="verifies sha256 only"):
        load_manifest(
            manifest(
                tmp_path,
                checksums={**CHECKSUMS, "algorithm": "md5"},
            )
        )


def test_a_misspelled_checksum_field_is_refused(tmp_path: Path) -> None:
    """`file` rather than `files` previously meant no digest was checked, silently."""
    with pytest.raises(ManifestError, match="does not implement: file"):
        load_manifest(
            manifest(
                tmp_path,
                checksums={**CHECKSUMS, "file": {"chunks.jsonl": "a" * 64}},
            )
        )


def test_a_digest_of_the_wrong_shape_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not a 64-character hexadecimal"):
        load_manifest(
            manifest(
                tmp_path,
                checksums={
                    **CHECKSUMS,
                    "files": {**CHECKSUMS["files"], "chunks.jsonl": "deadbeef"},
                },
            )
        )


def test_a_malformed_release_digest_is_refused(tmp_path: Path) -> None:
    """Recorded rather than verified -- we never reconstruct their bundle -- but a value we
    cannot check should at least be the right shape."""
    with pytest.raises(ManifestError, match="checksums.release"):
        load_manifest(
            manifest(tmp_path, checksums={**CHECKSUMS, "release": "nope"})
        )


# --------------------------------------------------------------------------------------
# Absence as compliance — the shape this module exists to refuse, found inside it
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["release_id", "schema_version", "source_roles_version"]
)
def test_an_omitted_identity_field_is_refused(tmp_path: Path, field: str) -> None:
    """All three were read with a `.get` and checked only when present, so omitting one read
    as compliance.

    `release_id` is the worst. `ingest_release` reconciles the caller's name against the
    manifest's only when the manifest has one — so leaving it out silently restored the exact
    behaviour that reconciliation had just replaced.
    """
    document = json.loads((manifest(tmp_path) / "manifest.json").read_text(encoding="utf-8"))
    del document[field]
    with pytest.raises(ManifestError, match=f"declares {field}"):
        load_manifest(write(tmp_path, document))


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_identity_field_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(ManifestError, match="declares release_id"):
        load_manifest(manifest(tmp_path, release_id=value))


def test_files_given_as_a_string_is_refused(tmp_path: Path) -> None:
    """A bare string was iterated character by character, each character becoming a "file" --
    and since `"/"` is a character, one of them contributed an empty-string folder."""
    entry = reference()
    entry["files"] = "ref-01/eau.md"
    with pytest.raises(ManifestError, match="declares files as str"):
        load_manifest(manifest(tmp_path, references=[entry]))


def test_a_null_file_name_is_refused(tmp_path: Path) -> None:
    """`[null]` was accepted, so a reference could enter the corpus naming no real source file
    while its declared `folders` satisfied coverage on its own."""
    with pytest.raises(ManifestError, match="claims a file named None"):
        load_manifest(manifest(tmp_path, references=[reference(files=[None])]))


def test_a_path_climbing_out_of_the_library_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="must be relative"):
        load_manifest(
            manifest(tmp_path, references=[reference(files=["../../etc/passwd"])])
        )


def test_folders_disagreeing_with_the_paths_is_refused(tmp_path: Path) -> None:
    """They were merged, so a reference claiming `ref-01` while its files came from `ref-02`
    claimed both and neither statement was ever tested against the other. Two sources for one
    fact, in the function written to stop exactly that."""
    entry = reference(files=["ref-02/eau.md"]) | {"folders": ["ref-01"]}
    with pytest.raises(ManifestError, match="but claims files from ref-02"):
        load_manifest(
            manifest(
                tmp_path,
                references=[entry],
                reference_folders=[
                    {"folder": "ref-01", "status": "accounted"},
                    {"folder": "ref-02", "status": "accounted"},
                ],
            )
        )


def test_a_release_with_no_checksums_is_refused(tmp_path: Path) -> None:
    """v3 and v3.1 shipped one and v4 did not. A release nobody can verify is exactly the one
    that needs verifying."""
    document = json.loads((manifest(tmp_path) / "manifest.json").read_text(encoding="utf-8"))
    del document["checksums"]
    with pytest.raises(ManifestError, match="carries no checksums"):
        load_manifest(write(tmp_path, document))


def test_checksums_naming_none_of_the_release_files_are_refused(tmp_path: Path) -> None:
    """A block naming nothing succeeded while verifying nothing, which reads identically to a
    release that was verified."""
    with pytest.raises(ManifestError, match="omits documents.jsonl"):
        load_manifest(
            manifest(
                tmp_path,
                checksums={**CHECKSUMS, "files": {"chunks.jsonl": "a" * 64}},
            )
        )


def test_a_checksum_path_climbing_out_of_the_release_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="must be relative"):
        load_manifest(
            manifest(
                tmp_path,
                checksums={
                    **CHECKSUMS,
                    "files": {**CHECKSUMS["files"], "../secrets.env": "a" * 64},
                },
            )
        )


def test_declared_domains_are_provisional_until_a_clinician_approves(
    tmp_path: Path,
) -> None:
    """**Unconstrained is not the same as safe.**

    Nothing now limits which purposes a source may claim beyond the purpose being real, which
    is right — the previous limit was a mapping invented by an engineer. But a release could
    declare that RECIST holds positions on therapy indication and we would accept it, and once
    comparison retrieval consumes these declarations a wrong one becomes a false conflict shown
    to a clinician.

    So the approval state travels with the declaration, and `clinician_approved` cannot be
    self-asserted: no field in the manifest can set it.
    """
    loaded = load_manifest(manifest(tmp_path))
    entry = loaded.corpus_entries["eau.prostate.2026"]
    assert entry.domain_approval == "provisional"


def test_a_release_cannot_assert_its_own_approval(tmp_path: Path) -> None:
    reference_entry = reference()
    reference_entry["domain_approval"] = "clinician_approved"
    with pytest.raises(ManifestError, match="does not implement: domain_approval"):
        load_manifest(manifest(tmp_path, references=[reference_entry]))


def test_nothing_is_approved_yet_and_that_is_the_honest_state() -> None:
    """An empty approval record is a statement, not an oversight. Until it is populated the
    divergence comparison has no approved authorities and must say so rather than proceed."""
    from rcpia.evidence.domain_approvals import APPROVALS

    assert APPROVALS == ()


def test_an_approval_covers_the_domains_it_named_and_no_others() -> None:
    """**Approving a source once does not approve whatever it declares later.**

    A flag on a temporary object was the first shape: not stored, not auditable, readable by
    nothing after the load. An approval is a clinical decision with a person behind it, so it
    records who, when, which domains, and which release — and a release that widens the
    domains, or arrives under a new id, falls back to provisional.
    """
    from datetime import date

    from rcpia.evidence import domain_approvals
    from rcpia.evidence.domain_approvals import Approval, approval_state

    approval = Approval(
        reference_id="eau-2026-prostate",
        domains=("therapy_indication",),
        clinician="Dr Example",
        approved_on=date(2026, 8, 21),
        release_id="rcpia-corpus-v5.0",
    )
    original = domain_approvals._BY_REFERENCE
    domain_approvals._BY_REFERENCE = {approval.reference_id: approval}
    try:
        exact = approval_state(
            "eau-2026-prostate",
            domains=("therapy_indication",),
            release_id="rcpia-corpus-v5.0",
        )
        widened = approval_state(
            "eau-2026-prostate",
            domains=("therapy_indication", "follow_up"),
            release_id="rcpia-corpus-v5.0",
        )
        later = approval_state(
            "eau-2026-prostate",
            domains=("therapy_indication",),
            release_id="rcpia-corpus-v6.0",
        )
    finally:
        domain_approvals._BY_REFERENCE = original

    assert exact == "clinician_approved"
    assert widened == "provisional"
    assert later == "provisional"
