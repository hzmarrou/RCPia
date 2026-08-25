"""What the curation claims about every document, in a form the repository can check.

The curation manifest lives in OneDrive, outside the repository, and is the only record of why
each of 112 documents is in the corpus or out of it. That makes it unreviewable here and
untestable in CI: a document quietly flipped into scope, or excluded with no reason given,
would be visible to nobody until a release was built on it.

So the claims are exported into `coverage.json` and committed, the same way `sections/` records
the headings a rule file was checked against and `baselines/` records what it does. The export
carries no clinical content -- an id, a folder, whether it is in scope, and why not if it is
not -- so it is small, diffable, and says in a pull request exactly what changed about the
shape of the corpus.

**Three properties are worth failing a build over**, and the tests assert them:

- every included document has a rule file, or it will be loaded unrouted and answer nothing
- every rule file has a curation record, or a document is being routed that nobody curated
- every excluded document states a reason, because a passage removed silently and a passage
  removed for a stated reason look identical from the outside

The fourth check -- files on disk that no record mentions -- needs the reference library and
cannot run here. `export` reports it when it runs, which is where it belongs: at the moment a
human is looking at the folders.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

COVERAGE_FILENAME = "coverage.json"


class CoverageError(Exception):
    """The coverage export says something that cannot be true."""


@dataclass(frozen=True)
class DocumentClaim:
    """One document's place in the corpus, as the curation records it."""

    document_id: str
    ref_id: str
    included: bool
    exclusion_reason: str | None
    exclusion_note: str | None
    duplicate_of: str | None

    @property
    def states_a_reason(self) -> bool:
        return bool(self.exclusion_reason or self.exclusion_note)


@dataclass(frozen=True)
class Coverage:
    """Every document the curation knows about."""

    documents: tuple[DocumentClaim, ...]

    @property
    def included(self) -> tuple[DocumentClaim, ...]:
        return tuple(d for d in self.documents if d.included)

    @property
    def excluded(self) -> tuple[DocumentClaim, ...]:
        return tuple(d for d in self.documents if not d.included)


def parse_coverage(body: object, *, source: str = COVERAGE_FILENAME) -> Coverage:
    if not isinstance(body, dict):
        raise CoverageError(f"{source} must be a JSON object")
    records = body.get("documents")
    if not isinstance(records, list):
        raise CoverageError(f"{source} needs `documents` as a list")

    claims: list[DocumentClaim] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CoverageError(f"{source}: documents[{index}] is not an object")
        document_id = record.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise CoverageError(f"{source}: documents[{index}] needs a non-empty document_id")
        included = record.get("included")
        if not isinstance(included, bool):
            raise CoverageError(f"{document_id}: `included` must be true or false")
        claims.append(
            DocumentClaim(
                document_id=document_id,
                ref_id=str(record.get("ref_id") or ""),
                included=included,
                exclusion_reason=record.get("exclusion_reason") or None,
                exclusion_note=record.get("exclusion_note") or None,
                duplicate_of=record.get("duplicate_of") or None,
            )
        )

    counts = Counter(claim.document_id for claim in claims)
    if len(counts) != len(claims):
        repeated = sorted(name for name, count in counts.items() if count > 1)
        raise CoverageError(
            f"{source} lists these document ids more than once: {', '.join(repeated)}. "
            "Two records for one document mean two different answers about its scope."
        )
    return Coverage(documents=tuple(claims))


def load_coverage(path: Path) -> Coverage:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoverageError(f"{path.name} is not valid JSON: {exc}") from exc
    return parse_coverage(body, source=path.name)


def export(manifest_path: Path, destination: Path, *, library: Path | None = None) -> Coverage:
    """Write the curation's scope claims into the repository.

    Only the fields that decide scope are copied. Titles, checksums, paths and licence evidence
    stay in the curation: this file exists to be diffed, and a diff full of unrelated churn is a
    diff nobody reads.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("documents")
    if not isinstance(records, list):
        raise CoverageError(f"{manifest_path.name} has no `documents` list")

    documents = [
        {
            "document_id": record.get("document_id"),
            "ref_id": record.get("ref_id"),
            "included": bool(record.get("include_in_poc")),
            "exclusion_reason": record.get("exclusion_reason"),
            "exclusion_note": record.get("exclusion_note"),
            "duplicate_of": record.get("duplicate_of_document_id"),
        }
        for record in records
    ]
    documents.sort(key=lambda d: (d["ref_id"] or "", d["document_id"] or ""))

    body = {
        "note": (
            "Exported from the curation manifest by `python -m rcpia.corpus.coverage`. What the "
            "corpus claims about scope, committed so it can be reviewed in a diff and checked in "
            "CI. Not the curation itself: titles, checksums, paths and licence evidence stay "
            "there."
        ),
        "source": manifest_path.name,
        "amended_at": manifest.get("amended_at"),
        "documents": documents,
    }
    destination.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    coverage = parse_coverage(body, source=destination.name)
    if library is not None:
        for line in orphan_report(coverage, library):
            print(line)
    return coverage


SOURCE_SUFFIXES = {".pdf", ".md", ".html"}
IGNORED_NAMES = {"README.md"}
IGNORED_DIRECTORIES = {
    "_source_captures",
    "legacy_homepage_capture",
    "legacy_failed_radiopaedia_capture",
}


def orphan_report(coverage: Coverage, library: Path) -> list[str]:
    """Files in the reference library that no curation record mentions.

    Cannot be a test: the library is not in the repository. It is the check that catches what
    the others cannot -- a document nobody ever looked at, which is invisible to every count
    taken from the manifest, because those counts start from the manifest.
    """
    manifest_path = library / "_prepared" / "poc_document_manifest.json"
    if not manifest_path.exists():
        return [f"no curation manifest at {manifest_path}, skipping the orphan report"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    claimed: set[str] = set()
    for record in manifest.get("documents", []):
        for key in ("source_path", "markdown_path", "raw_html_path", "metadata_json_path"):
            value = record.get(key)
            if value:
                claimed.add(str(Path(value)).lower())

    lines: list[str] = []
    for folder in sorted(p for p in library.glob("ref-*") if p.is_dir()):
        for path in sorted(folder.rglob("*")):
            if (
                path.is_file()
                and path.suffix.lower() in SOURCE_SUFFIXES
                and path.name not in IGNORED_NAMES
                and not path.name.endswith(".conversion.json")
                and not (IGNORED_DIRECTORIES & set(path.parts))
                and str(path).lower() not in claimed
            ):
                lines.append(f"  orphan  {path.relative_to(library)}")
    lines.append(f"{len(lines)} files on disk that no curation record mentions")
    return lines


def main(argv: list[str] | None = None) -> int:
    import argparse

    cli = argparse.ArgumentParser(
        prog="python -m rcpia.corpus.coverage",
        description="Export the curation's scope claims into the repository, and report files "
        "the curation has never seen.",
    )
    cli.add_argument("--manifest", type=Path, required=True, help="the curation manifest")
    cli.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).with_name(COVERAGE_FILENAME),
        help="where to write coverage.json",
    )
    cli.add_argument("--library", type=Path, help="the reference library, for the orphan report")
    args = cli.parse_args(argv)

    coverage = export(args.manifest, args.out, library=args.library)
    print(
        f"{len(coverage.documents)} documents: {len(coverage.included)} in scope, "
        f"{len(coverage.excluded)} excluded"
    )
    silent = [d.document_id for d in coverage.excluded if not d.states_a_reason]
    if silent:
        print(f"excluded with no stated reason: {', '.join(silent)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
