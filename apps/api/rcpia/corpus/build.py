"""Stage 4: run every document through the pipeline and write a release.

The stages before this each answer one question about one document. This one holds the list,
and it is the first code that needs the reference library: the rule files, fixtures, baselines,
coverage and specs are all committed, but the guideline text is not, and cannot be.

**What it composes, in order.** For each document the corpus says is in scope: read its
sections, cut them into passages, ask its rule file which patient each passage is about, find
the page it came from, and hand the result to `emit_release`, which writes the release and then
refuses it here if the loader would refuse it there.

**Two readers, told apart by a declaration rather than by a guess.** Most documents are markdown
and go through `prepare`. The three AFU guidelines are Urofrance web captures whose structure
lives in `document.json` blocks, and they are recognised by having a file in
`corpus/structure/` -- the same per-document declaration `blocks` already reads. Sniffing the
source type would work today and break the first time a fourth capture arrives without one.

**Nothing here decides anything clinical.** Every judgement was made earlier and committed: the
setting comes from the rule file, the provenance from `specs.json`, the scope from
`coverage.json`. This module's only opinions are about order and file layout, which is why it
can be read quickly and why a mistake in it is loud rather than subtle.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rcpia.corpus.blocks import load_structure, outline_blocks, read_blocks, structure_path
from rcpia.corpus.coverage import COVERAGE_FILENAME, load_coverage
from rcpia.corpus.emit import DocumentSpec, EmitReport, RoutedPassage, emit_release
from rcpia.corpus.locate import LocateError, PageSpan, locate_all
from rcpia.corpus.prepare import Section, outline
from rcpia.corpus.rules import RoutingRules, flatten, load_rules, route
from rcpia.corpus.specs import SPECS_FILENAME, Spec, load_specs
from rcpia.corpus.split import passages
from rcpia.corpus.vocabulary import AGENT_ROLES

CORPUS = Path(__file__).resolve().parent
ROUTING = CORPUS / "routing"
STRUCTURE = CORPUS / "structure"
CURATION = Path("_prepared") / "poc_document_manifest.json"


class BuildError(Exception):
    """A release cannot be assembled from what is on disk."""


@dataclass(frozen=True)
class SourceRecord:
    """Where a document's text lives, from the curation."""

    document_id: str
    ref_id: str
    title: str
    markdown_path: Path | None
    blocks_path: Path | None
    pdf_path: Path | None
    source_files: tuple[str, ...]
    agent_roles: tuple[str, ...] = ()


def read_curation(library: Path) -> dict[str, SourceRecord]:
    """The curation, reduced to what the build needs: a title and some paths."""
    path = library / CURATION
    if not path.exists():
        raise BuildError(
            f"no curation manifest at {path}. The build needs the reference library, which is "
            "the one input that is not in the repository."
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))

    records: dict[str, SourceRecord] = {}
    for record in manifest.get("documents", []):
        document_id = record.get("document_id")
        if not isinstance(document_id, str):
            continue
        files = [
            str(Path(record[key]).relative_to(library).as_posix())
            for key in ("source_path", "markdown_path", "metadata_json_path", "raw_html_path")
            if record.get(key) and Path(record[key]).is_relative_to(library)
        ]
        records[document_id] = SourceRecord(
            document_id=document_id,
            ref_id=str(record.get("ref_id") or ""),
            title=str(record.get("title") or document_id),
            markdown_path=Path(record["markdown_path"]) if record.get("markdown_path") else None,
            blocks_path=(
                Path(record["metadata_json_path"]) if record.get("metadata_json_path") else None
            ),
            pdf_path=(
                Path(record["source_path"])
                if str(record.get("source_path", "")).lower().endswith(".pdf")
                else None
            ),
            source_files=tuple(dict.fromkeys(files)),
            # Only roles retrieval can filter on. The curation also tags `safety_reviewer` and
            # `rcp_synthesis`, which are agents that read outputs rather than passages -- 84% of
            # records carry the first, so keeping it would say nothing about any chunk.
            agent_roles=tuple(
                r for r in (record.get("agent_roles") or []) if r in AGENT_ROLES
            ),
        )
    return records


def read_sections(source: SourceRecord, rules: RoutingRules) -> list[Section]:
    """A document's sections, from whichever reader its structure declares."""
    declared = structure_path(STRUCTURE, source.document_id)
    if declared.exists():
        if source.blocks_path is None or not source.blocks_path.exists():
            raise BuildError(
                f"{source.document_id} declares a block structure but its document.json is "
                "missing; the capture and the declaration have to travel together"
            )
        return list(outline_blocks(read_blocks(source.blocks_path), load_structure(declared)))

    if source.markdown_path is None or not source.markdown_path.exists():
        raise BuildError(f"{source.document_id} has no markdown to read at {source.markdown_path}")
    report = outline(
        source.markdown_path.read_text(encoding="utf-8", errors="replace"),
        source=source.document_id,
        allow_artefacts=True,
        furniture_ends_section=rules.furniture_ends_section,
    )
    return list(report.sections)


def route_document(
    source: SourceRecord, rules: RoutingRules, *, locate_pages: bool = True
) -> list[RoutedPassage]:
    """Every passage of one document, with what its rule file decided about it.

    Pages are located in one pass at the end rather than per passage: `locate_all` reads the
    PDF once, and reading a 245-page guideline once per passage would dominate the build.
    """
    occurrences: dict[str, int] = {}
    staged: list[tuple[Any, tuple[str, ...], Any, bool]] = []
    for section in read_sections(source, rules):
        flat = flatten(list(section.section_path))
        for passage in passages(section.text):
            occurrences[flat] = occurrences.get(flat, 0) + 1
            decision = route(rules, list(section.section_path), occurrence=occurrences[flat])
            staged.append((passage, tuple(section.section_path), decision, section.is_reference))

    spans = _page_spans([p.text for p, _, _, _ in staged], source, locate_pages=locate_pages)

    routed: list[RoutedPassage] = []
    for (passage, section_path, decision, is_reference), span in zip(staged, spans, strict=True):
        routed.append(
            RoutedPassage(
                passage=passage,
                section_path=section_path,
                disease_setting=decision.setting,
                not_specified_reason=decision.rule.reason,
                # A section stage 1 marked as a bibliography is excluded whatever the rule file
                # says, because a rule cannot name sixty-four citations.
                exclude=decision.rule.exclude or is_reference,
                exclusion_reason=(
                    decision.rule.exclusion_reason
                    if decision.rule.exclude
                    else ("bibliography_entry" if is_reference else None)
                ),
                # `general` where no rule narrows it: v4.0's largest bucket by far, and what
                # retrieval's `admit_universal_purpose` exists to admit. A passage claiming
                # every purpose its document covers would match every question and filter none.
                clinical_purpose=decision.rule.purposes or ("general",),
                agent_roles=decision.rule.roles,
                page_first=span.first,
                page_last=span.last,
                page_confidence=span.confidence,
            )
        )
    return routed


def _page_spans(
    texts: list[str], source: SourceRecord, *, locate_pages: bool
) -> list[PageSpan]:
    """Where each passage sits in the source PDF, or nowhere if it cannot be told.

    A citation carrying a page nobody located is worse than one carrying none, so a failure
    here degrades to zero rather than raising: the AFU captures have no PDF at all, and
    `pdftotext` may be absent on the machine doing the build.
    """
    nowhere = [PageSpan(first=0, last=0, confidence=0.0)] * len(texts)
    if not locate_pages or source.pdf_path is None or not source.pdf_path.exists():
        return nowhere
    try:
        return locate_all(texts, source.pdf_path)
    except LocateError:
        return nowhere


def build_spec(spec: Spec, source: SourceRecord) -> DocumentSpec:
    """The release's view of a document: its own facts, plus where its text came from."""
    return DocumentSpec(
        document_id=spec.document_id,
        ref_id=source.ref_id,
        title=source.title,
        publisher=spec.publisher,
        authority=spec.authority,
        document_type=spec.document_type,
        language=spec.language,
        version_label=spec.version_label,
        source_files=source.source_files,
        source_family=spec.source_family,
        source_role=spec.source_role,
        licence_status=spec.licence_status,
        position_domains=spec.position_domains,
        cancer_types=spec.cancer_types,
        agent_roles=source.agent_roles,
        clinical_status=spec.clinical_status,
    )


def declared_folders(library: Path, claimed: set[str]) -> dict[str, str]:
    """Every reference folder, and whether this release takes anything from it.

    `accounted` and `empty` are the loader's words. A folder declared accounted that nothing
    claims from is "either mislabelled or forgotten", so the two are decided from what the
    release actually claims rather than asserted.
    """
    folders = {p.name for p in library.glob("ref-*") if p.is_dir()}
    return {name: ("accounted" if name in claimed else "empty") for name in sorted(folders)}


def build(
    library: Path,
    destination: Path,
    *,
    release_id: str,
    locate_pages: bool = True,
) -> EmitReport:
    """Assemble a release from the committed decisions and the reference library."""
    specs = load_specs(CORPUS / SPECS_FILENAME)
    coverage = load_coverage(CORPUS / COVERAGE_FILENAME)
    curation = read_curation(library)

    in_scope = [claim.document_id for claim in coverage.included]
    missing_spec = sorted(set(in_scope) - set(specs))
    if missing_spec:
        raise BuildError(f"in scope but unspecified: {', '.join(missing_spec)}")
    missing_source = sorted(d for d in in_scope if d not in curation)
    if missing_source:
        raise BuildError(f"in scope but the curation has no record: {', '.join(missing_source)}")

    documents: list[tuple[DocumentSpec, list[RoutedPassage]]] = []
    claimed: set[str] = set()
    for document_id in sorted(in_scope):
        rule_file = ROUTING / f"{document_id}.json"
        if not rule_file.exists():
            raise BuildError(f"{document_id} is in scope and has no rule file")
        rules = load_rules(rule_file)
        source = curation[document_id]

        if not source.agent_roles:
            raise BuildError(
                f"{document_id} names no agent role retrieval can filter on, so no agent would "
                "ever see it"
            )
        routed = route_document(source, rules, locate_pages=locate_pages)
        if not routed:
            raise BuildError(f"{document_id} produced no passages, which cannot be right")
        documents.append((build_spec(specs[document_id], source), routed))
        claimed.update(name.split("/", 1)[0] for name in source.source_files)

    return emit_release(
        destination,
        release_id=release_id,
        documents=documents,
        folders=declared_folders(library, claimed),
        generated_at=datetime.now(UTC).isoformat(),
        # Every document the corpus says is out of scope, named so that any of them a previous
        # release loaded is removed. Most were never loaded and the loader does nothing with
        # them; the one that matters is the kind that was, like `siog.prostate.2010`, dropped
        # as superseded and left answering questions because v5 simply did not mention it.
        withdrawn=[claim.document_id for claim in coverage.excluded],
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    cli = argparse.ArgumentParser(
        prog="python -m rcpia.corpus.build",
        description="Build a corpus release from the committed routing decisions.",
    )
    cli.add_argument("--library", type=Path, required=True, help="the reference library root")
    cli.add_argument("--out", type=Path, required=True, help="where to write the release")
    cli.add_argument("--release-id", required=True, help="for example v5.0")
    cli.add_argument(
        "--no-pages",
        action="store_true",
        help="skip page location, which needs pdftotext and is the slowest step",
    )
    args = cli.parse_args(argv)

    report = build(
        args.library, args.out, release_id=args.release_id, locate_pages=not args.no_pages
    )
    print(f"release   {report.release_id}")
    print(f"directory {report.directory}")
    print(f"documents {report.documents}")
    print(f"passages  {report.chunks}  ({report.excluded} kept but never returned)")
    print(f"omitted   {report.out_of_scope}  (another disease, left out of the release)")
    print(f"withdrawn {report.withdrawn}  (declared out of scope, removed if previously loaded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
