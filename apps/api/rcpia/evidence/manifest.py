"""The release's coverage proof, and our refusal to take it on trust.

**Every other guard in this loader checks what a release *contains*.** An unknown chunk field
is refused, an unconstrained enum is refused, a changed text-search configuration is refused.
Nothing checked what a release *failed* to contain, and a missing document makes no noise.

That cost four releases. In the v2 review we asked the corpus agent for a small four-document
slice to validate a new schema before migrating all 38 documents. Nobody wrote down the step
after it, so the slice became the corpus: five releases named `rcpia-corpus-vN` while holding
ten documents of thirty-eight. The two EAU bladder guidelines sat parsed, in the same folder as
the EAU prostate file that *was* loaded, same publisher and same edition, and never arrived.
Nothing anywhere complained, because nothing had ever declared what a release should hold.

`manifest.json` is that declaration: every logical reference in the library, what happened to
it, and — for anything entering the corpus — the document id and chunk count it should produce.
This module refuses a release whose manifest does not add up.

**The manifest's own `validations.passed` is not evidence.** It is the corpus agent's
self-check and it is useful, but every check is recomputed here. Accepting a stated result
instead of verifying the thing it states is precisely the failure this project keeps
relearning — `not_specified_reason`, the v3 metadata corrections, `retrieval_eligible`,
`clinical_purpose`, and now whole documents.

**A release with no manifest is refused outright.** Not warned about. A warning is what the
last four releases would have produced, and nobody would have read it.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"

# What a reference's disposition may be, and the field each kind must carry. `corpus` is the
# only kind this loader verifies against delivered content; the rest are declarations we
# record and do not check, because the artefacts they describe live outside this repository.
_DISPOSITION_FIELDS = {
    "corpus": "document_id",
    "tool": "tool_id",
    "calculator": "calculator_id",
    "excluded": "reason",
}

# A folder is either accounted for or declared empty. "Nothing here" and "nobody looked" must
# not render identically — the whole point of the file.
_FOLDER_STATUSES = frozenset({"accounted", "empty"})


class ManifestError(Exception):
    """The release's manifest does not account for what the release delivers."""


@dataclass(frozen=True)
class CorpusEntry:
    """One document a manifest says should be in the corpus."""

    reference_id: str
    document_id: str
    chunks: int
    source_family: str | None
    source_role: str | None
    position_bearing: bool | None


@dataclass
class Manifest:
    """A release's declared coverage, already checked for internal consistency.

    Loading validates the manifest against itself — every folder accounted for, every file
    named exactly once, every disposition carrying its required field, `position_bearing`
    agreeing with the declared `source_roles` mapping. Checking it against what the release
    actually delivers is `verify_against_release`, which needs the parsed documents.
    """

    release_id: str | None
    schema_version: str | None
    corpus_entries: dict[str, CorpusEntry]
    folders: dict[str, str]
    declared_gaps: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    checksums: dict[str, str] = field(default_factory=dict)

    @property
    def declared_chunks(self) -> int:
        return sum(entry.chunks for entry in self.corpus_entries.values())


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {what} must be an object, got {type(value).__name__}"
        )
    return value


def _require_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {what} must be a list, got {type(value).__name__}"
        )
    return value


def load_manifest(directory: Path) -> Manifest:
    """Read and self-check `manifest.json`, or refuse the release."""
    path = directory / MANIFEST_FILENAME
    if not path.exists():
        raise ManifestError(
            f"{MANIFEST_FILENAME} is missing. A release without one declares nothing about "
            "what it should contain, so a document that never arrives cannot be detected — "
            "which is how a four-document validation slice became the corpus for five "
            "releases."
        )

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{MANIFEST_FILENAME} is not valid JSON: {exc}") from exc

    document = _require_mapping(document, "the manifest")

    # `source_roles` maps a role to whether it can hold a position on treatment choice. It is
    # declared in the release rather than hardcoded here so both sides read the same table:
    # a role we do not recognise is a question for the corpus agent, not a silent default.
    role_map: dict[str, bool] = {}
    for role, spec in _require_mapping(document.get("source_roles") or {}, "source_roles").items():
        bearing = _require_mapping(spec, f"source_roles.{role}").get("position_bearing")
        if not isinstance(bearing, bool):
            raise ManifestError(
                f"{MANIFEST_FILENAME}: source_roles.{role} must declare position_bearing "
                "as a boolean"
            )
        role_map[role] = bearing

    folders: dict[str, str] = {}
    for entry in _require_list(document.get("reference_folders") or [], "reference_folders"):
        entry = _require_mapping(entry, "a reference_folders entry")
        name = entry.get("folder")
        status = entry.get("status")
        if not name:
            raise ManifestError(f"{MANIFEST_FILENAME}: a reference_folders entry has no folder")
        if status not in _FOLDER_STATUSES:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: folder {name} has status {status!r}; expected one of "
                f"{', '.join(sorted(_FOLDER_STATUSES))}"
            )
        folders[name] = status

    corpus_entries: dict[str, CorpusEntry] = {}
    concerns: list[str] = []
    # Every physical file must appear exactly once across references and artifacts. Without
    # it the manifest proves nothing: a file listed twice is a document ingested twice under
    # two ids, and a file listed nowhere is the omission this file exists to catch.
    seen_files: dict[str, str] = {}

    for entry in _require_list(document.get("references") or [], "references"):
        entry = _require_mapping(entry, "a references entry")
        reference_id = entry.get("reference_id")
        if not reference_id:
            raise ManifestError(f"{MANIFEST_FILENAME}: a references entry has no reference_id")

        _claim_files(entry, reference_id, seen_files)
        _check_position_bearing(entry, reference_id, role_map)

        for note in entry.get("concerns") or []:
            concerns.append(f"{reference_id}: {note}")

        dispositions = _require_list(
            entry.get("dispositions"), f"references[{reference_id}].dispositions"
        )
        if not dispositions:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: reference {reference_id} declares no disposition. "
                "A reference nobody decided about is indistinguishable from one nobody saw."
            )
        for disposition in dispositions:
            corpus_entry = _read_disposition(disposition, entry, reference_id)
            if corpus_entry is not None:
                if corpus_entry.document_id in corpus_entries:
                    raise ManifestError(
                        f"{MANIFEST_FILENAME}: document {corpus_entry.document_id} is claimed "
                        f"by more than one reference"
                    )
                corpus_entries[corpus_entry.document_id] = corpus_entry

    for entry in _require_list(document.get("artifacts") or [], "artifacts"):
        entry = _require_mapping(entry, "an artifacts entry")
        _claim_files(entry, entry.get("artifact_id") or "an artifact", seen_files)

    gaps = [
        gap.get("gap_id") or gap.get("topic") or "unnamed gap"
        for gap in _require_list(document.get("gaps") or [], "gaps")
        if isinstance(gap, dict)
    ]

    checksums = _require_mapping(document.get("checksums") or {}, "checksums")
    files = _require_mapping(checksums.get("files") or {}, "checksums.files")

    return Manifest(
        release_id=document.get("release_id"),
        schema_version=document.get("schema_version"),
        corpus_entries=corpus_entries,
        folders=folders,
        declared_gaps=gaps,
        concerns=concerns,
        checksums={name: str(value) for name, value in files.items()},
    )


def _claim_files(entry: dict[str, Any], owner: str, seen: dict[str, str]) -> None:
    for name in entry.get("files") or []:
        if name in seen:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: file {name} is claimed by both {seen[name]} and "
                f"{owner}. A file claimed twice is a document ingested twice under two ids."
            )
        seen[name] = owner


def _check_position_bearing(
    entry: dict[str, Any], reference_id: str, role_map: dict[str, bool]
) -> None:
    """`source_role` is authoritative; `position_bearing` must agree with it.

    Two fields that must agree are two fields that can disagree, and the release that first
    disagrees is the one nobody reads carefully. The consequence is not cosmetic: a
    specialist reference treated as a peer of AFU, EAU and NCCN makes the comparison step
    report a divergence that does not exist — SIOG is not disagreeing about treatment, it is
    answering a different question — and a false conflict shown to a clinician is the failure
    most likely to end their trust in the mechanism.
    """
    role = entry.get("source_role")
    bearing = entry.get("position_bearing")
    if role is None and bearing is None:
        return
    if role is None:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} declares position_bearing "
            "without a source_role to justify it"
        )
    if role not in role_map:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} has source_role {role!r}, which "
            "the manifest's source_roles mapping does not define"
        )
    if bearing is not None and bearing != role_map[role]:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} declares position_bearing="
            f"{bearing} but source_role {role!r} maps to {role_map[role]}"
        )


def _read_disposition(
    disposition: Any, entry: dict[str, Any], reference_id: str
) -> CorpusEntry | None:
    disposition = _require_mapping(disposition, f"a disposition of {reference_id}")
    kind = disposition.get("kind")
    if kind not in _DISPOSITION_FIELDS:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} has disposition {kind!r}; "
            f"expected one of {', '.join(sorted(_DISPOSITION_FIELDS))}"
        )

    required = _DISPOSITION_FIELDS[kind]
    if required not in disposition:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} has a {kind} disposition without "
            f"{required}"
        )

    if kind != "corpus":
        return None

    document_id = disposition["document_id"]
    # A `corpus` disposition with a null document id declares interpretive guidance that
    # *should* exist and does not — what an abnormal G8 implies, when the questionnaire is in
    # one reference and the guidance in another we have not chosen. A visible gap, not a
    # document to check for.
    if document_id is None:
        return None

    chunks = disposition.get("chunks")
    if not isinstance(chunks, int) or isinstance(chunks, bool) or chunks < 0:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {document_id} declares chunks={chunks!r}; a corpus "
            "disposition must state how many passages it produces, so a document delivered "
            "empty is visible as a defect rather than as an absence"
        )
    return CorpusEntry(
        reference_id=reference_id,
        document_id=document_id,
        chunks=chunks,
        source_family=entry.get("source_family"),
        source_role=entry.get("source_role"),
        position_bearing=entry.get("position_bearing"),
    )


def verify_against_release(
    manifest: Manifest,
    *,
    delivered_documents: set[str],
    chunk_counts: dict[str, int],
) -> None:
    """Refuse a release whose content does not match what its manifest declares.

    `delivered_documents` is every document id in `documents.jsonl` that the release did not
    itself mark excluded; `chunk_counts` is how many passages each of them actually carries.

    Both directions are checked, and they catch different failures. A declared document that
    did not arrive is the EAU bladder case — the whole reason this module exists. A delivered
    document the manifest never mentions is the opposite: content entering the corpus that no
    coverage proof accounts for, which would make the proof a description of something else.
    """
    declared = set(manifest.corpus_entries)

    missing = sorted(declared - delivered_documents)
    if missing:
        raise ManifestError(
            f"{MANIFEST_FILENAME} declares {len(missing)} document(s) the release does not "
            f"deliver: {', '.join(missing)}. This is the failure the manifest exists to "
            "catch — a document that never arrives is otherwise silent."
        )

    undeclared = sorted(delivered_documents - declared)
    if undeclared:
        raise ManifestError(
            f"the release delivers {len(undeclared)} document(s) the manifest does not "
            f"account for: {', '.join(undeclared)}. A coverage proof that omits delivered "
            "content proves nothing about the release it ships with."
        )

    mismatched = [
        f"{document_id} declares {entry.chunks} chunks, delivers {chunk_counts.get(document_id, 0)}"
        for document_id, entry in sorted(manifest.corpus_entries.items())
        if chunk_counts.get(document_id, 0) != entry.chunks
    ]
    if mismatched:
        raise ManifestError(
            f"{MANIFEST_FILENAME} chunk counts do not match the release: "
            f"{'; '.join(mismatched)}"
        )


def verify_checksums(manifest: Manifest, directory: Path) -> None:
    """Check every file the manifest names a digest for.

    v3 and v3.1 carried a checksum and v4 did not. It has caught nothing so far, which is
    what a checksum is for.
    """
    for name, expected in sorted(manifest.checksums.items()):
        path = directory / name
        if not path.exists():
            raise ManifestError(
                f"{MANIFEST_FILENAME} states a checksum for {name}, which the release does "
                "not contain"
            )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual.lower() != expected.lower():
            raise ManifestError(
                f"{name} does not match the checksum in {MANIFEST_FILENAME}: expected "
                f"{expected[:16]}…, got {actual[:16]}…"
            )
