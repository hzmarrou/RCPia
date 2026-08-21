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

**What this module cannot check, stated plainly.** The reference library lives outside the
release. We receive a directory of JSONL files and this manifest; we cannot enumerate folders
we never see. So a file the manifest simply never lists is invisible here — the founding
failure, one level down. `_check_folder_coverage` catches the largest version of it, a wholly
omitted or mislabelled folder. Closing the rest needs the corpus agent to ship a file
inventory with digests for `verify_checksums` to check, which is requested in
`docs/reviews/corpus-v5-manifest-contract.md` and not yet delivered.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from rcpia.evidence.domain_approvals import PROVISIONAL, approval_state
from rcpia.evidence.release_files import RELEASE_FILES

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

# Every key each object may carry. **This module repeated the failure it was written to
# prevent**: it read the keys it knew and ignored the rest, so a manifest could carry
# `processed_from`, a folder relationship, or a misspelled `dispositons` and be accepted
# without a word — exactly how `not_specified_reason`, the v3 metadata, `retrieval_eligible`
# and `clinical_purpose` were each lost.
_MANIFEST_FIELDS = frozenset(
    {
        "artifacts",
        "checksums",
        "gaps",
        "generated_at",
        "reference_folders",
        "references",
        "release_id",
        "schema_version",
        "source_roles_version",
    }
)
_REFERENCE_FIELDS = frozenset(
    {
        "concerns",
        "dispositions",
        "files",
        "folders",
        "position_domains",
        "processed_from",
        "reference_id",
        "source_family",
        "source_role",
        "title",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "belongs_to", "files", "kind", "note"}
)
_FOLDER_FIELDS = frozenset({"folder", "note", "status"})
_DISPOSITION_FIELDS_ALLOWED = frozenset(
    {"calculator_id", "chunks", "covers", "document_id", "kind", "reason", "tool_id"}
)
_GAP_FIELDS = frozenset({"gap_id", "note", "status", "topic"})

# The manifest shape this loader implements. A release built against another version is
# refused rather than read under the wrong rules.
MANIFEST_SCHEMA_VERSION = "1.0"

# **RCPia owns this vocabulary, and the release selects from it.**
#
# The first version read the mapping from the release, on the argument that a role we did not
# recognise should be a question rather than a silent default. That is circular: a release
# could invent `expert_opinion`, declare it position-bearing, and validate itself. The meaning
# of a source_role is a decision about how the comparison step behaves, so it belongs here,
# versioned with the code that acts on it.
# **A role names a kind of source. It does not decide what that source may hold a position on.**
#
# The first version mapped each role to a set of permitted domains — `treatment_guideline`
# could cover therapy indication, modality selection and treatment delivery, and nothing else.
# That mapping was invented here, by an engineer, and no clinician has seen it. It is also
# wrong on its face: AFU, EAU and NCCN are comprehensive guidelines and hold positions on risk
# stratification, follow-up, safety, diagnosis and staging as well.
#
# Using an unreviewed role as a *clinical* ceiling is the kind of quiet scope decision this
# project exists not to make. So the ceiling is gone. A reference declares the domains it
# covers, and the only check is that each one names a real `clinical_purpose` — a domain no
# chunk can carry is meaningless whoever declared it.
#
# Whether roles should constrain domains at all is a question for the clinician, recorded in
# `docs/reviews/` and deliberately unanswered here.
SOURCE_ROLES: frozenset[str] = frozenset(
    {
        "treatment_guideline",      # AFU, EAU, NCCN, ESMO
        "treatment_consensus",      # a joint position between two authorities
        "specialist_guidance",      # SIOG, ESPEN, renal, psycho-oncology
        "classification_standard",  # WHO/IARC, PI-RADS, TNM
        "measurement_standard",     # RECIST
        "assessment_instrument",    # G8, MNA, IPSS, IIEF-5, ADL
        "predictive_model",         # Briganti, MSKCC
    }
)

SOURCE_ROLES_VERSION = "2.0"

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
    position_domains: tuple[str, ...] | None
    # `provisional` unless a clinician has signed these exact domains off for this release.
    # Never self-asserted: only `rcpia.evidence.domain_approvals` sets it, and a manifest
    # carrying a `domain_approval` field is refused as unknown.
    domain_approval: str = PROVISIONAL


@dataclass
class Manifest:
    """A release's declared coverage, already checked for internal consistency.

    Loading validates the manifest against itself — every folder accounted for, every file
    named exactly once, every disposition carrying its required field, `position_domains`
    within what its `source_role` permits. Checking it against what the release
    actually delivers is `verify_against_release`, which needs the parsed documents.
    """

    release_id: str
    schema_version: str
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


def _reject_unknown(value: dict[str, Any], allowed: frozenset[str], what: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {what} carries fields this loader does not implement: "
            f"{', '.join(unknown)}. A field nobody reads is a decision nobody made."
        )


def _require_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {what} must be a list, got {type(value).__name__}"
        )
    return value


def load_manifest(
    directory: Path, *, purposes: frozenset[str] | None = None
) -> Manifest:
    """Read and self-check `manifest.json`, or refuse the release.

    `purposes` is the `clinical_purpose` vocabulary the release's own `chunk.schema.json`
    declares, used to check that every `position_domains` entry names a purpose that exists.
    """
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

    # A release states which vocabulary it was built against; it does not get to define one.
    # **Required, not optional.** Every one of these was read with a `.get` and checked only
    # when present, so omitting a field read as compliance — the same shape as the omissions
    # this whole module exists to catch, built into the code written to catch them.
    #
    # `release_id` is the worst of the three. `ingest_release` reconciles the caller's name
    # against the manifest's only `if manifest.release_id is not None`, so a manifest that
    # simply leaves it out silently restores the behaviour that reconciliation replaced.
    for name in ("release_id", "schema_version", "source_roles_version"):
        value = document.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError(
                f"{MANIFEST_FILENAME} declares {name} as {value!r}. All three of release_id, "
                "schema_version and source_roles_version are required: a release that does "
                "not say what it is, or which rules it was built under, cannot be checked "
                "against either."
            )

    schema_version = document["schema_version"]
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"{MANIFEST_FILENAME} declares schema_version {schema_version!r}; this loader "
            f"implements {MANIFEST_SCHEMA_VERSION!r}. Reading a manifest under the wrong "
            "rules is how a coverage proof stops proving anything."
        )

    declared_version = document["source_roles_version"]
    if declared_version != SOURCE_ROLES_VERSION:
        raise ManifestError(
            f"{MANIFEST_FILENAME} was built against source_roles_version "
            f"{declared_version!r}; this loader implements {SOURCE_ROLES_VERSION!r}. The "
            "meaning of a source_role decides how the comparison step behaves, so a "
            "release cannot be interpreted under a version we do not implement."
        )
    if "source_roles" in document:
        raise ManifestError(
            f"{MANIFEST_FILENAME} declares its own source_roles mapping. RCPia owns that "
            "vocabulary and versions it -- a release that defines both a role and its "
            "meaning validates itself. State source_roles_version and select from "
            f"{', '.join(sorted(SOURCE_ROLES))}."
        )
    # After the two version checks, so a release built against a vocabulary or shape we do not
    # implement is told which, rather than being handed a list of fields it did not expect to
    # be wrong about.
    _reject_unknown(document, _MANIFEST_FIELDS, "the manifest")

    folders: dict[str, str] = {}
    for entry in _require_list(document.get("reference_folders") or [], "reference_folders"):
        entry = _require_mapping(entry, "a reference_folders entry")
        _reject_unknown(entry, _FOLDER_FIELDS, f"folder {entry.get('folder')}")
        folder_name = entry.get("folder")
        status = entry.get("status")
        if not isinstance(folder_name, str) or not folder_name.strip():
            raise ManifestError(
                f"{MANIFEST_FILENAME}: a reference_folders entry names no folder"
            )
        if status not in _FOLDER_STATUSES:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: folder {folder_name} has status {status!r}; expected "
                f"one of "
                f"{', '.join(sorted(_FOLDER_STATUSES))}"
            )
        folders[folder_name] = status

    corpus_entries: dict[str, CorpusEntry] = {}
    concerns: list[str] = []
    # Every physical file must appear exactly once across references and artifacts. Without
    # it the manifest proves nothing: a file listed twice is a document ingested twice under
    # two ids, and a file listed nowhere is the omission this file exists to catch.
    seen_files: dict[str, str] = {}
    # Which folders each reference says it draws from. Read from the declared `folders` field
    # rather than inferred from slashes in file names -- the contract allows bare filenames
    # beside a separate folder list, and a reference spanning two folders (the GGPO and
    # Mini-GDS duplicates do) cannot be expressed by a path prefix at all.
    claimed_folders: set[str] = set()

    for entry in _require_list(document.get("references") or [], "references"):
        entry = _require_mapping(entry, "a references entry")
        _reject_unknown(
            entry, _REFERENCE_FIELDS, f"reference {entry.get('reference_id')}"
        )
        reference_id = entry.get("reference_id")
        if not reference_id:
            raise ManifestError(f"{MANIFEST_FILENAME}: a references entry has no reference_id")

        _claim_files(entry, reference_id, seen_files, claimed_folders)
        _check_position_domains(entry, reference_id, purposes)
        _require_source_metadata(entry, reference_id)

        for note in entry.get("concerns") or []:
            concerns.append(f"{reference_id}: {note}")

        # A reference that delivers a document must name the file it came from. An empty
        # `files` list satisfied every per-entry check by having no entries to check, so a
        # corpus document could arrive with no stated source at all.
        if _delivers_corpus(entry) and not entry.get("files"):
            raise ManifestError(
                f"{MANIFEST_FILENAME}: reference {reference_id} delivers a corpus document "
                "and names no file. A passage whose source nobody recorded cannot be traced "
                "back to a guideline."
            )

        dispositions = _require_list(
            entry.get("dispositions"), f"references[{reference_id}].dispositions"
        )
        if not dispositions:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: reference {reference_id} declares no disposition. "
                "A reference nobody decided about is indistinguishable from one nobody saw."
            )
        for disposition in dispositions:
            corpus_entry = _read_disposition(
                disposition, entry, reference_id, document["release_id"]
            )
            if corpus_entry is not None:
                if corpus_entry.document_id in corpus_entries:
                    raise ManifestError(
                        f"{MANIFEST_FILENAME}: document {corpus_entry.document_id} is claimed "
                        f"by more than one reference"
                    )
                corpus_entries[corpus_entry.document_id] = corpus_entry

    for entry in _require_list(document.get("artifacts") or [], "artifacts"):
        entry = _require_mapping(entry, "an artifacts entry")
        _reject_unknown(entry, _ARTIFACT_FIELDS, f"artifact {entry.get('artifact_id')}")
        _claim_files(
            entry, entry.get("artifact_id") or "an artifact", seen_files, claimed_folders
        )

    _check_folder_coverage(folders, claimed_folders)

    gaps: list[str] = []
    for gap in _require_list(document.get("gaps") or [], "gaps"):
        gap = _require_mapping(gap, "a gaps entry")
        _reject_unknown(gap, _GAP_FIELDS, f"gap {gap.get('gap_id')}")
        gaps.append(gap.get("gap_id") or gap.get("topic") or "unnamed gap")

    files = _read_checksums(document.get("checksums"))

    return Manifest(
        release_id=document["release_id"],
        schema_version=document["schema_version"],
        corpus_entries=corpus_entries,
        folders=folders,
        declared_gaps=gaps,
        concerns=concerns,
        checksums=files,
    )


# What `checksums` may hold, and the only digest we verify.
_CHECKSUM_FIELDS = frozenset({"algorithm", "files", "release"})

# The files a release cannot be checked without: whatever the loader actually reads. Their
# digests are required, because a checksum block that names nothing verifies nothing while
# looking as though it verified everything — the same shape as an absent block, one level in.
#
# Imported rather than repeated. A second copy of this list was correct the day it was written
# and would have gone stale the first time the release format grew a file — a list standing in
# for a rule, which is the finding that removed the domain ceiling.
_MUST_BE_CHECKSUMMED = RELEASE_FILES

# Key under which the release-level digest is recorded. Not a path, so `verify_checksums`
# skips it rather than looking for a file of that name.
_RELEASE_DIGEST = "<release>"
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _read_checksums(checksums: object) -> dict[str, str]:
    """The per-file digests, or refuse the block.

    It was read with two `.get`s and no checks at all, which meant a misspelled `file`, an
    unknown or absent `algorithm`, and a digest of the wrong shape all passed — and a manifest
    carrying no usable checksum then looked exactly like one that had been verified. That is
    the same silent-acceptance shape the rest of this module refuses, in the field whose whole
    job is to make tampering and truncation loud.

    Only SHA-256 is accepted. Naming another algorithm is refused rather than ignored, because
    ignoring it would verify nothing while appearing to.
    """
    if checksums is None:
        raise ManifestError(
            f"{MANIFEST_FILENAME} carries no checksums. v3 and v3.1 shipped one and v4 did "
            "not; a release nobody can verify is exactly the one that needs verifying."
        )
    block = _require_mapping(checksums, "checksums")
    _reject_unknown(block, _CHECKSUM_FIELDS, "checksums")

    algorithm = block.get("algorithm")
    if algorithm is None:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: checksums declares no algorithm. A digest whose algorithm "
            "is unstated cannot be checked."
        )
    if not isinstance(algorithm, str) or algorithm.lower() not in {"sha256", "sha-256"}:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: checksums names algorithm {algorithm!r}; this loader "
            "verifies sha256 only, and would otherwise appear to verify without doing so."
        )

    files = _require_mapping(block.get("files") or {}, "checksums.files")
    digests: dict[str, str] = {}
    for name, digest in files.items():
        cleaned = str(name).strip().replace("\\", "/")
        if (
            cleaned != name
            or cleaned.startswith("/")
            or PureWindowsPath(cleaned).is_absolute()
            or PurePosixPath(cleaned).is_absolute()
            or ".." in cleaned.split("/")
        ):
            raise ManifestError(
                f"{MANIFEST_FILENAME}: checksums.files names {name!r}; paths must be "
                "relative to the release directory and must not climb out of it"
            )
        if not isinstance(digest, str) or not _SHA256.match(digest):
            raise ManifestError(
                f"{MANIFEST_FILENAME}: checksums.files[{name}] is {digest!r}, which is not a "
                "64-character hexadecimal SHA-256 digest"
            )
        digests[name] = digest

    missing = [name for name in _MUST_BE_CHECKSUMMED if name not in digests]
    if missing:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: checksums.files omits {', '.join(missing)}. A checksum "
            "block naming none of the release's own files succeeds while verifying nothing, "
            "which reads identically to a release that was verified."
        )

    # The release-level digest is over the corpus agent's own bundle, which we never
    # reconstruct. Required in the right shape and recorded, so that a release can be named
    # by content in correspondence even though this loader cannot recompute it.
    release = block.get("release")
    if not isinstance(release, str) or not _SHA256.match(release):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: checksums.release is {release!r}, which is not a "
            "64-character hexadecimal SHA-256 digest"
        )
    digests[_RELEASE_DIGEST] = release
    return digests


def _check_folder_coverage(folders: dict[str, str], claimed_folders: set[str]) -> None:
    """Every accounted folder must have at least one file claimed from it, and every claimed
    file must sit in a declared folder.

    **This is a weaker check than the docstring above once claimed, and the difference
    matters.** "Every physical file appears exactly once" was only ever enforced against files
    the manifest *listed* — a file omitted from the manifest was invisible, which is the
    manifest's own founding failure reproduced inside the module written to fix it.

    A loader cannot do better on its own. The reference library lives outside the release; we
    receive `documents.jsonl`, `chunks.jsonl` and this manifest, and we cannot enumerate a
    directory we never see. Closing the gap properly needs the corpus agent to ship a file
    inventory with digests, which `verify_checksums` can then check — that is asked for in
    `docs/reviews/corpus-v5-manifest-contract.md`.

    What is enforceable here is the shape: a folder declared `accounted` that nothing claims a
    file from is either mislabelled or forgotten, and a file claimed from a folder nobody
    declared means the folder list is incomplete. Both are the wholly-omitted-folder case,
    which is the largest version of the failure.
    """
    empty_but_claimed = sorted(
        folder
        for folder, status in folders.items()
        if status == "empty" and folder in claimed_folders
    )
    if empty_but_claimed:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {', '.join(empty_but_claimed)} declared empty, but files "
            "are claimed from them"
        )

    accounted_but_bare = sorted(
        folder
        for folder, status in folders.items()
        if status == "accounted" and folder not in claimed_folders
    )
    if accounted_but_bare:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {', '.join(accounted_but_bare)} declared accounted, but "
            "no reference or artifact claims a file from them. A folder nobody claims from "
            "is either empty and mislabelled, or forgotten."
        )

    undeclared = sorted(claimed_folders - set(folders))
    if undeclared:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: files are claimed from {', '.join(undeclared)}, which "
            "reference_folders does not declare. The folder list is incomplete."
        )


def _claim_files(
    entry: dict[str, Any], owner: str, seen: dict[str, str], folders: set[str]
) -> None:
    """Record the files this reference or artifact claims, and the folders it draws from.

    **`files` was not validated at all.** A bare string was iterated character by character,
    each character becoming a "file" — and since `"/"` is a character, one of them even
    contributed an empty-string folder to the coverage set. `[null]` was accepted outright, so
    a reference could enter the corpus naming no real source file while its declared `folders`
    satisfied coverage on its own.

    **Explicit `folders` is authoritative; a path prefix is a fallback for entries that give
    none.** They were merged, so `folders: ["ref-01"]` beside `files: ["ref-02/x.md"]` claimed
    both and neither statement was ever tested against the other. Two sources for one fact, in
    the function written to stop exactly that — a disagreement fails now.
    """
    names = entry.get("files")
    if names is None:
        names = []
    if not isinstance(names, list):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: {owner} declares files as {type(names).__name__}; expected "
            "a list. A string here is iterated character by character."
        )
    paths: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(
                f"{MANIFEST_FILENAME}: {owner} claims a file named {name!r}; each must be a "
                "non-empty relative path"
            )
        cleaned = name.strip().replace("\\", "/")
        # `startswith("/")` misses `C:/secret.txt`, which `Path.is_absolute` catches on
        # Windows. A drive-qualified path is absolute wherever the manifest was written, not
        # wherever it is read.
        if (
            cleaned.startswith("/")
            or PureWindowsPath(cleaned).is_absolute()
            or PurePosixPath(cleaned).is_absolute()
            or ".." in cleaned.split("/")
        ):
            raise ManifestError(
                f"{MANIFEST_FILENAME}: {owner} claims {name!r}; paths must be relative and "
                "must not climb out of the library"
            )
        paths.append(cleaned)

    declared = entry.get("folders")
    inferred = {path.split("/", 1)[0] for path in paths if "/" in path}
    if declared is not None:
        if not isinstance(declared, list) or not all(
            isinstance(folder, str) and folder.strip() for folder in declared
        ):
            raise ManifestError(
                f"{MANIFEST_FILENAME}: {owner} declares folders as {declared!r}; expected a "
                "list of folder names"
            )
        stated = {folder.strip() for folder in declared}
        contradicted = sorted(inferred - stated)
        if contradicted:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: {owner} declares folders {', '.join(sorted(stated))} "
                f"but claims files from {', '.join(contradicted)}. The declared list is "
                "authoritative; a disagreement means one of the two is wrong."
            )
        folders.update(stated)
    else:
        # Legacy entries that give paths and no folder list.
        folders.update(inferred)

    for path in paths:
        if path in seen:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: file {path} is claimed by both {seen[path]} and "
                f"{owner}. A file claimed twice is a document ingested twice under two ids."
            )
        seen[path] = owner


def _delivers_corpus(entry: dict[str, Any]) -> bool:
    """Whether this reference actually puts a document into the corpus.

    `document_id: null` is a declared gap — guidance that should exist and does not — so it
    delivers nothing and is held to none of the requirements that describe delivered content.
    """
    return any(
        isinstance(disposition, dict)
        and disposition.get("kind") == "corpus"
        and disposition.get("document_id") is not None
        for disposition in entry.get("dispositions") or []
    )


def _require_source_metadata(entry: dict[str, Any], reference_id: str) -> None:
    """A reference entering the corpus must say what kind of source it is.

    Optional was not good enough. Retrieval stratifies by family and the comparison step
    refuses to treat a non-position-bearing source as a peer; a reference that declares
    neither is admitted to both and behaves as though it were a treatment guideline, which is
    the failure the fields exist to prevent.

    Only required of references that actually enter the corpus. A tool, a calculator or an
    excluded reference has nothing for retrieval to route.
    """
    # `document_id: null` on a corpus disposition is a *declared gap* -- interpretive guidance
    # that should exist and does not, like what an abnormal G8 implies when the questionnaire
    # is in one reference and the guidance in another we have not chosen. Nothing is delivered,
    # so there is nothing for retrieval to route and nothing to describe.
    if not _delivers_corpus(entry):
        return
    absent = [
        name
        for name in ("source_family", "source_role", "position_domains")
        # An empty string satisfies "is not None" and then vanishes from family-stratified
        # retrieval without a word -- the passages load, and no family search ever names them.
        if entry.get(name) is None
        or (isinstance(entry.get(name), str) and not str(entry[name]).strip())
    ]
    if absent:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} enters the corpus without "
            f"{', '.join(absent)}. A source whose kind is unstated is routed as though it "
            "were a treatment guideline."
        )


def _check_position_domains(
    entry: dict[str, Any], reference_id: str, purposes: frozenset[str] | None
) -> None:
    """A reference declares what it holds positions on; we check the names are real.

    **The role does not constrain this.** A previous version had `treatment_guideline` permit
    exactly three treatment domains — a mapping invented by an engineer that no clinician had
    seen, and wrong on its face, since AFU, EAU and NCCN also hold positions on risk
    stratification, follow-up, safety, diagnosis and staging. An unreviewed role acting as a
    clinical ceiling is a scope decision made by omission.

    What remains is checkable without deciding anything clinical: every domain must name a
    `clinical_purpose` the release itself declares, because a domain no chunk can carry is
    meaningless whoever wrote it. The release's `chunk.schema.json` is the source of that
    vocabulary — it already has to constrain `clinical_purpose` for chunks to be validated at
    all, so it cannot be widened here without widening it there too.

    An empty list is a statement: this source holds positions nowhere. Omitting the field
    is an absence, and `_require_source_metadata` refuses it.
    """
    role = entry.get("source_role")
    domains = entry.get("position_domains")
    if role is None and domains is None:
        return
    if role is None:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} declares position_domains "
            "without a source_role to justify them"
        )
    if role not in SOURCE_ROLES:
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} has source_role {role!r}, which "
            f"this loader does not implement. Choose from {', '.join(sorted(SOURCE_ROLES))}."
        )
    if domains is None:
        return
    if not isinstance(domains, list):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} declares position_domains as "
            f"{type(domains).__name__}, expected a list"
        )
    for domain in domains:
        if not isinstance(domain, str) or not domain.strip():
            raise ManifestError(
                f"{MANIFEST_FILENAME}: reference {reference_id} declares a position domain "
                f"of {domain!r}; each must be a non-empty clinical purpose"
            )
    if len(set(domains)) != len(domains):
        raise ManifestError(
            f"{MANIFEST_FILENAME}: reference {reference_id} repeats a position domain"
        )
    if purposes is not None:
        unknown = sorted(set(domains) - purposes)
        if unknown:
            raise ManifestError(
                f"{MANIFEST_FILENAME}: reference {reference_id} claims positions in "
                f"{', '.join(unknown)}, which the release's chunk schema does not list as a "
                "clinical purpose. A domain no chunk can carry is not a claim about anything."
            )


def _read_disposition(
    disposition: Any, entry: dict[str, Any], reference_id: str, release_id: str
) -> CorpusEntry | None:
    disposition = _require_mapping(disposition, f"a disposition of {reference_id}")
    _reject_unknown(
        disposition, _DISPOSITION_FIELDS_ALLOWED, f"a disposition of {reference_id}"
    )
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
    domains = (
        tuple(entry["position_domains"])
        if isinstance(entry.get("position_domains"), list)
        else None
    )
    return CorpusEntry(
        reference_id=reference_id,
        document_id=document_id,
        chunks=chunks,
        source_family=entry.get("source_family"),
        source_role=entry.get("source_role"),
        position_domains=domains,
        domain_approval=approval_state(
            reference_id, domains=domains, release_id=release_id
        ),
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


def verify_source_metadata(manifest: Manifest, documents: list[dict[str, Any]]) -> None:
    """The manifest and `documents.jsonl` must agree about family, role and position.

    The same three facts are stated in two files. A manifest that says one thing while the
    document record says another is a coverage proof describing a different release — and the
    disagreement would be invisible, because retrieval reads the document record and the
    comparison step's correctness rests on the manifest's declared `source_roles` mapping.

    Only checked where the manifest states a value: a release may leave any of the three
    unstated, and older ones state none of them.
    """
    by_id = {
        record["document_id"]: record
        for record in documents
        if isinstance(record.get("document_id"), str)
    }
    disagreements: list[str] = []
    for document_id, entry in sorted(manifest.corpus_entries.items()):
        record = by_id.get(document_id)
        if record is None:
            continue
        for field_name, declared in (
            ("source_family", entry.source_family),
            ("source_role", entry.source_role),
            ("position_domains", entry.position_domains),
        ):
            if declared is None:
                continue
            delivered = record.get(field_name)
            # `position_domains` arrives from JSON as a list and is parsed here as a tuple.
            # Comparing them raw reports every well-formed release as a disagreement.
            if isinstance(declared, tuple):
                delivered = tuple(delivered) if isinstance(delivered, list) else delivered
            if delivered != declared:
                disagreements.append(
                    f"{document_id}.{field_name}: manifest says {declared!r}, "
                    f"documents.jsonl says {delivered!r}"
                )
    if disagreements:
        raise ManifestError(
            f"{MANIFEST_FILENAME} and documents.jsonl disagree: " + "; ".join(disagreements)
        )


def verify_checksums(manifest: Manifest, directory: Path) -> None:
    """Check every file the manifest names a digest for.

    v3 and v3.1 carried a checksum and v4 did not. It has caught nothing so far, which is
    what a checksum is for.
    """
    for name, expected in sorted(manifest.checksums.items()):
        if name == _RELEASE_DIGEST:
            # Over the corpus agent's bundle, which we never reconstruct. Recorded, not
            # verified — and said plainly rather than left to look like a check.
            continue
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
