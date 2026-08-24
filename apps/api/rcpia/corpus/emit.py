"""Stage 3: write a release, and refuse it here rather than at the loader.

**The point of this stage is where it fails.** Every check the loader performs already exists
and is good; what it could not do was run ten minutes earlier. So this module imports the
loader's own validators and runs them against what it has just written. A release that would be
refused on load is refused on build, by the same code, with the same message.

It imports the loader's *validators* and never its writers. Conforming to what the loader
happens to accept would defeat the check -- the release has to satisfy the contract, not mirror
the implementation.

The schema is written into the release from `rcpia.corpus.vocabulary` rather than copied from
the previous release. That is what makes the vocabulary owned here: a release states its own
enums, the loader validates against those, and a value nobody implements cannot enter by being
inherited.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rcpia.corpus.split import Passage
from rcpia.corpus.vocabulary import (
    AGENT_ROLES,
    CANCER_TYPES,
    CLINICAL_PURPOSES,
    CLINICAL_STATUSES,
    DISEASE_SETTINGS,
    LANGUAGES,
    NOT_SPECIFIED_REASONS,
    THERAPY_LINES,
    UNIT_TYPES,
)
from rcpia.evidence.manifest import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    SOURCE_ROLES_VERSION,
    load_manifest,
    verify_against_release,
    verify_checksums,
    verify_source_metadata,
)
from rcpia.evidence.release_files import RELEASE_FILES

SCHEMA_FILENAME = "chunk.schema.json"
DOCUMENTS_FILENAME = "documents.jsonl"
CHUNKS_FILENAME = "chunks.jsonl"

# What `unit_type` a passage's kind implies. The split stage says what a passage *is*; this is
# the only place that opinion turns into a value the retrieval reads.
_UNIT_TYPE_BY_KIND = {
    "table": "decision_table",
    "mixed": "decision_table",
    "references": "reference_list",
    "prose": "general_principle",
}

# A reference list is kept and never retrieved. The reason is one the schema already carries and
# that corpus v4.0 already uses for 179 passages.
_REFERENCE_EXCLUSION = "bibliography_entry"


class EmitError(Exception):
    """A release cannot be written, or cannot be written correctly."""


@dataclass(frozen=True)
class DocumentSpec:
    """Everything about a document that is not in its text.

    Publisher and licence are here rather than derived, because both have been wrong in the
    corpus by being inferred: four SIOG documents record the society that convened the task
    force where the publisher belongs, and their licence says `open_access_copy` on evidence
    that does not support it.
    """

    document_id: str
    ref_id: str
    title: str
    publisher: str
    authority: str
    document_type: str
    language: str
    version_label: str
    source_url: str | None = None
    source_path: str | None = None
    # The source files this document was built from, relative to the reference library root
    # (`ref-03/Vessie_TVNIM/document.json`). The manifest requires them and claims each exactly
    # once across the release: a passage whose source nobody recorded cannot be traced back to a
    # guideline, and a file claimed twice means two documents believe they own it.
    source_files: tuple[str, ...] = ()
    short_title: str | None = None
    source_family: str | None = None
    source_role: str | None = None
    licence_status: str | None = None
    # Which clinical questions this source can hold a position on, for the step that compares
    # AFU against EAU against NCCN. An empty tuple is a statement -- this source holds positions
    # nowhere -- and is how SIOG is kept out of a treatment comparison it is not party to.
    # `None` is not allowed by the loader: an unstated kind is routed as a treatment guideline.
    position_domains: tuple[str, ...] = ()
    cancer_types: tuple[str, ...] = ()
    agent_roles: tuple[str, ...] = ()
    clinical_status: str = "poc_unreviewed"


@dataclass(frozen=True)
class RoutedPassage:
    """One passage, with what stage 2 decided about it."""

    passage: Passage
    section_path: tuple[str, ...]
    disease_setting: str
    not_specified_reason: str | None
    therapy_line: str = "not_applicable"
    clinical_purpose: tuple[str, ...] = ()
    page_first: int = 0
    page_last: int = 0
    page_confidence: float = 0.0


@dataclass
class EmitReport:
    release_id: str
    directory: Path
    documents: int = 0
    chunks: int = 0
    excluded: int = 0
    warnings: list[str] = field(default_factory=list)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", stripped)).strip("-")[:60] or "section"


def contextual_text(section_path: tuple[str, ...], text: str) -> str:
    """The passage with the path it sits under, which is what gets embedded.

    A passage that reads "BCG is contraindicated" means nothing on its own; the same words under
    "Tumeurs de très haut risque" are a recommendation about a specific patient.
    """
    return " > ".join(section_path) + "\n\n" + text if section_path else text


def chunk_id(document_id: str, section_path: tuple[str, ...], within: int, running: int) -> str:
    leaf = section_path[-1] if section_path else "unsectioned"
    return f"{document_id}.{_slug(leaf)}.{within:02d}.{running:04d}"


def build_chunk(
    spec: DocumentSpec, routed: RoutedPassage, *, within: int, running: int
) -> dict[str, Any]:
    kind = routed.passage.kind
    excluded = kind == "references"
    body = routed.passage.text
    return {
        "chunk_id": chunk_id(spec.document_id, routed.section_path, within, running),
        "document_id": spec.document_id,
        "ref_id": spec.ref_id,
        "title": spec.short_title or spec.title,
        "text": body,
        "contextual_text": contextual_text(routed.section_path, body),
        "section_path": list(routed.section_path),
        "source_location": {
            "page_start": routed.page_first or None,
            "page_end": routed.page_last or None,
            "locator_method": "pdf_token_overlap" if routed.page_first else "unavailable",
            "locator_confidence": round(routed.page_confidence, 3),
        },
        "citation": _citation(spec, routed),
        "language": spec.language,
        "cancer_types": list(spec.cancer_types),
        "disease_setting": routed.disease_setting,
        "therapy_line": routed.therapy_line,
        "agent_roles": list(spec.agent_roles),
        "clinical_purpose": list(routed.clinical_purpose),
        "unit_type": _UNIT_TYPE_BY_KIND[kind],
        "recommendation_number": None,
        "retrieval": {"eligible": not excluded},
        "content_sha256": sha256_text(body),
        "clinical_status": spec.clinical_status,
        "not_specified_reason": routed.not_specified_reason,
        "embedding_sha256": None,
        "retrieval_eligible": not excluded,
        "retrieval_exclusion_reason": _REFERENCE_EXCLUSION if excluded else None,
    }


def _citation(spec: DocumentSpec, routed: RoutedPassage) -> str:
    """What a reviewer is given to go and check.

    Names the section rather than the page when the page is unknown, and never both when only
    one is true -- a citation carrying a page nobody located is worse than one carrying none.
    """
    where = " > ".join(routed.section_path) if routed.section_path else "sans section"
    if routed.page_first:
        pages = (
            f"p. {routed.page_first}"
            if routed.page_first == routed.page_last
            else f"pp. {routed.page_first}-{routed.page_last}"
        )
        return f"{spec.short_title or spec.title} ({spec.version_label}), {where}, {pages}"
    return f"{spec.short_title or spec.title} ({spec.version_label}), {where}"


def build_document(spec: DocumentSpec) -> dict[str, Any]:
    return {
        "document_id": spec.document_id,
        "ref_id": spec.ref_id,
        "title": spec.title,
        "short_title": spec.short_title,
        "publisher": spec.publisher,
        "authority": spec.authority,
        "document_type": spec.document_type,
        "language": spec.language,
        "version": {"label": spec.version_label},
        "source": {"source_url": spec.source_url, "source_path": spec.source_path},
        "licence": {"status": spec.licence_status},
        "clinical_status": spec.clinical_status,
        "source_family": spec.source_family,
        "source_role": spec.source_role,
        # Stated here as well as in the manifest, and the loader checks the two agree. Retrieval
        # reads this record while the comparison step rests on the manifest, so a disagreement
        # would be invisible to both.
        "position_domains": list(spec.position_domains) if spec.source_role else None,
    }


def build_schema() -> dict[str, Any]:
    """The release's own schema, written from the vocabulary this repository owns."""

    def enum(values: frozenset[str]) -> dict[str, Any]:
        return {"type": "string", "enum": sorted(values)}

    def enum_array(values: frozenset[str]) -> dict[str, Any]:
        return {"type": "array", "items": {"type": "string", "enum": sorted(values)}}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://rcpia.local/chunk.schema.json",
        "title": "RCPia evidence chunk",
        "type": "object",
        "additionalProperties": False,
        "required": ["chunk_id", "document_id", "text", "contextual_text", "section_path"],
        "properties": {
            "chunk_id": {"type": "string"},
            "document_id": {"type": "string"},
            "ref_id": {"type": "string"},
            "title": {"type": "string"},
            "text": {"type": "string"},
            "contextual_text": {"type": "string"},
            "section_path": {"type": "array", "items": {"type": "string"}},
            "source_location": {"type": "object"},
            "citation": {"type": "string"},
            "language": enum(LANGUAGES),
            "cancer_types": enum_array(CANCER_TYPES),
            "disease_setting": enum(DISEASE_SETTINGS),
            "therapy_line": enum(THERAPY_LINES),
            "agent_roles": enum_array(AGENT_ROLES),
            "clinical_purpose": enum_array(CLINICAL_PURPOSES),
            "unit_type": enum(UNIT_TYPES),
            "recommendation_number": {"type": ["integer", "null"]},
            "retrieval": {"type": "object"},
            "content_sha256": {"type": "string"},
            "clinical_status": enum(CLINICAL_STATUSES),
            "not_specified_reason": {
                "type": ["string", "null"],
                "enum": [*sorted(NOT_SPECIFIED_REASONS), None],
            },
            "embedding_sha256": {"type": ["string", "null"]},
            "retrieval_eligible": {"type": "boolean"},
            "retrieval_exclusion_reason": {"type": ["string", "null"]},
        },
    }


def build_manifest(
    *,
    release_id: str,
    specs: list[DocumentSpec],
    chunk_counts: dict[str, int],
    folders: dict[str, str],
    generated_at: str,
) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    for spec in specs:
        entry: dict[str, Any] = {
            "reference_id": spec.ref_id,
            "title": spec.title,
            "files": list(spec.source_files),
            "dispositions": [
                {
                    "kind": "corpus",
                    "document_id": spec.document_id,
                    "chunks": chunk_counts.get(spec.document_id, 0),
                }
            ],
        }
        if spec.source_family:
            entry["source_family"] = spec.source_family
        if spec.source_role:
            entry["source_role"] = spec.source_role
            entry["position_domains"] = list(spec.position_domains)
        references.append(entry)

    return {
        "release_id": release_id,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        # Which reading of `source_role` this release was built under. Taken from the loader
        # rather than written as a literal: a release claiming a version the loader does not
        # implement is refused, and hard-coding it here is how that check gets defeated by a
        # copy-paste one day.
        "source_roles_version": SOURCE_ROLES_VERSION,
        "generated_at": generated_at,
        "references": references,
        "reference_folders": [
            {"folder": name, "status": status} for name, status in sorted(folders.items())
        ],
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _checksums(directory: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in RELEASE_FILES:
        digest = hashlib.sha256()
        with (directory / name).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        out[name] = digest.hexdigest()
    return out


def emit_release(
    directory: Path,
    *,
    release_id: str,
    documents: list[tuple[DocumentSpec, list[RoutedPassage]]],
    folders: dict[str, str],
    generated_at: str,
) -> EmitReport:
    """Write a release and refuse it here if the loader would refuse it there."""
    if not documents:
        raise EmitError("a release with no documents proves nothing and loads nothing")

    directory.mkdir(parents=True, exist_ok=True)
    report = EmitReport(release_id=release_id, directory=directory)

    chunk_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    running = 0
    for spec, routed_passages in documents:
        document_rows.append(build_document(spec))
        within: dict[tuple[str, ...], int] = {}
        for routed in routed_passages:
            key = routed.section_path
            within[key] = within.get(key, 0) + 1
            running += 1
            row = build_chunk(spec, routed, within=within[key], running=running)
            chunk_rows.append(row)
            if not row["retrieval_eligible"]:
                report.excluded += 1
        counts[spec.document_id] = len(routed_passages)

    _write_jsonl(directory / DOCUMENTS_FILENAME, document_rows)
    _write_jsonl(directory / CHUNKS_FILENAME, chunk_rows)
    _write_json(directory / SCHEMA_FILENAME, build_schema())

    manifest_document = build_manifest(
        release_id=release_id,
        specs=[spec for spec, _ in documents],
        chunk_counts=counts,
        folders=folders,
        generated_at=generated_at,
    )
    file_digests = _checksums(directory)
    manifest_document["checksums"] = {
        "algorithm": "sha256",
        "files": file_digests,
        # One digest over the file digests, so a release has a single value to quote. Computed
        # from the sorted name:digest lines rather than from the bytes of the files, so it does
        # not depend on the order the files happened to be written in.
        "release": sha256_text(
            "\n".join(f"{name}:{digest}" for name, digest in sorted(file_digests.items()))
        ),
    }
    _write_json(directory / MANIFEST_FILENAME, manifest_document)

    report.documents = len(document_rows)
    report.chunks = len(chunk_rows)
    self_check(directory, document_rows=document_rows, chunk_counts=counts)
    return report


def self_check(
    directory: Path, *, document_rows: list[dict[str, Any]], chunk_counts: dict[str, int]
) -> None:
    """Run the loader's own checks against what was just written.

    Deliberately the loader's code and not a copy of it. A second implementation would drift,
    and the drift would be invisible until a release passed here and failed there.
    """
    manifest = load_manifest(directory)
    verify_against_release(
        manifest,
        delivered_documents={row["document_id"] for row in document_rows},
        chunk_counts=chunk_counts,
    )
    verify_source_metadata(manifest, document_rows)
    verify_checksums(manifest, directory)
