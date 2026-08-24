"""Stage 0: turn a source PDF into markdown, and check that the markdown is usable.

Every other stage in this package starts from markdown. Until now that markdown arrived by
hand: the operator uploaded each PDF to LlamaParse, downloaded the result, and dropped it
next to the source. That worked, and it failed twice in ways worth designing against.

**A bad conversion is silent.** The three ASPEN practice tools are two-column PDFs, and the
first conversion read straight across the columns -- sentences interleaved between columns
and not one markdown heading in the file. Nothing said so. It was found much later, when
stage 1 refused the documents, and by then curation records had been written against them.
The AFU 2019 chapters failed the same way. So this module does not only convert: it runs
stage 1 against its own output and reports what stage 1 will make of it, at the moment the
file is written.

**Re-converting costs money and time.** LlamaParse bills per page and a 245-page guideline
takes minutes. A conversion therefore records the digest of the PDF it came from and is
reused when the PDF has not changed. `force=True` is how a re-parse is asked for -- after a
provider upgrade, say -- rather than by deleting files and hoping.

This is deliberately not wired into the corpus build. The build reads markdown from disk and
must stay runnable with no network, no API key and no spend; conversion happens once per
document, when the document is acquired.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rcpia.corpus.prepare import PrepareError, find_pdf_artefacts, outline
from rcpia.parsing.base import MarkdownParser, ParseRequest, ParserNotConfigured

SIDECAR_SUFFIX = ".conversion.json"
PDF_CONTENT_TYPE = "application/pdf"


class ConversionError(Exception):
    """The document could not be converted at all."""


@dataclass(frozen=True)
class Conversion:
    """What happened to one document, including what stage 1 will make of it."""

    source: Path
    markdown_path: Path
    source_sha256: str
    markdown_bytes: int
    provider: str
    reused: bool
    # `usable` false means the markdown was written and stage 1 will refuse it. The file is
    # kept so the failure can be inspected rather than re-run blind.
    usable: bool
    refusal: str | None
    heading_count: int
    sections: int
    artefacts: tuple[str, ...]

    @property
    def summary(self) -> str:
        if not self.usable:
            return f"UNUSABLE: {self.refusal}"
        artefacts = f", {len(self.artefacts)} glyph artefacts" if self.artefacts else ""
        prefix = "reused, " if self.reused else ""
        return f"{prefix}{self.sections} sections, {self.heading_count} headings{artefacts}"


def digest(path: Path) -> str:
    """SHA-256 of a file, read in blocks because guidelines run to hundreds of megabytes."""
    running = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            running.update(block)
    return running.hexdigest()


def markdown_path_for(pdf: Path) -> Path:
    """Beside the PDF, same stem.

    A corpus document folder holds a source PDF and the markdown converted from it, and
    nothing else; `ref-16/_source_captures/README.md` records why. Keeping the pair together
    is what makes that rule checkable.
    """
    return pdf.with_suffix(".md")


def sidecar_path_for(markdown: Path) -> Path:
    return markdown.with_name(markdown.name + SIDECAR_SUFFIX)


def read_sidecar(markdown: Path) -> dict[str, object] | None:
    path = sidecar_path_for(markdown)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def is_current(pdf: Path, markdown: Path) -> bool:
    """True when `markdown` was converted from exactly this PDF.

    Compares digests rather than modification times: OneDrive rewrites mtimes when it syncs,
    and re-parsing a 245-page guideline is not something to trigger by accident.
    """
    if not markdown.exists():
        return False
    sidecar = read_sidecar(markdown)
    if sidecar is None:
        return False
    return sidecar.get("source_sha256") == digest(pdf)


def inspect(
    markdown: str, *, source: str
) -> tuple[bool, str | None, int, int, tuple[str, ...]]:
    """Ask stage 1 what it makes of this markdown, without raising.

    Artefacts are surveyed but do not make a conversion unusable on their own: `outline` is
    called with `allow_artefacts=True` so the report can say "converted, and it carries 27
    glyph codes" rather than refusing and leaving the operator nothing to look at.
    """
    heading_count = sum(1 for line in markdown.splitlines() if line.startswith("#"))
    artefacts = tuple(find_pdf_artefacts(markdown))
    try:
        report = outline(markdown, source=source, allow_artefacts=True)
    except PrepareError as exc:
        return False, str(exc), heading_count, 0, artefacts
    return True, None, heading_count, len(report.sections), artefacts


async def convert(
    pdf: Path,
    *,
    parser: MarkdownParser | None,
    destination: Path | None = None,
    force: bool = False,
    language: str = "en",
) -> Conversion:
    """Convert one PDF, reusing an existing conversion unless the PDF changed."""
    if not pdf.exists():
        raise ConversionError(f"{pdf} does not exist")

    markdown_path = destination or markdown_path_for(pdf)
    source_sha256 = digest(pdf)

    if not force and is_current(pdf, markdown_path):
        existing = markdown_path.read_text(encoding="utf-8")
        usable, refusal, headings, sections, artefacts = inspect(existing, source=pdf.name)
        sidecar = read_sidecar(markdown_path) or {}
        return Conversion(
            source=pdf,
            markdown_path=markdown_path,
            source_sha256=source_sha256,
            markdown_bytes=markdown_path.stat().st_size,
            provider=str(sidecar.get("provider", "unknown")),
            reused=True,
            usable=usable,
            refusal=refusal,
            heading_count=headings,
            sections=sections,
            artefacts=artefacts,
        )

    if parser is None:
        raise ConversionError(
            f"{pdf.name} needs converting and no parser was supplied. "
            "Set LLAMAPARSE_API_KEY, or pass a parser."
        )

    parsed = await parser.parse_markdown(
        ParseRequest(
            filename=pdf.name,
            content=pdf.read_bytes(),
            content_type=PDF_CONTENT_TYPE,
            language=language,
        )
    )

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(parsed.markdown, encoding="utf-8")
    usable, refusal, headings, sections, artefacts = inspect(parsed.markdown, source=pdf.name)

    sidecar_path_for(markdown_path).write_text(
        json.dumps(
            {
                "source_path": str(pdf),
                "source_sha256": source_sha256,
                "provider": parsed.provider,
                "provider_version": parsed.provider_version,
                "job_id": parsed.job_id,
                "converted_at": datetime.now(UTC).isoformat(),
                "language": language,
                # Recorded so a conversion stage 1 refuses is visible in the folder, not only
                # in whatever terminal happened to run it.
                "usable": usable,
                "refusal": refusal,
                "heading_count": headings,
                "sections": sections,
                "artefacts": list(artefacts),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return Conversion(
        source=pdf,
        markdown_path=markdown_path,
        source_sha256=source_sha256,
        markdown_bytes=markdown_path.stat().st_size,
        provider=parsed.provider,
        reused=False,
        usable=usable,
        refusal=refusal,
        heading_count=headings,
        sections=sections,
        artefacts=artefacts,
    )


def adopt(
    pdf: Path, *, provider: str = "llamaparse", destination: Path | None = None
) -> Conversion:
    """Record provenance for a markdown file that was converted by hand.

    Twenty-odd documents were converted before this module existed. They have no sidecar, so
    `is_current` cannot tell they are done, and the first run of the converter over the corpus
    would re-parse every one of them and bill for it.

    Adoption asserts something weaker than a conversion does, and the sidecar says so: the
    markdown is *claimed* to have come from this PDF, on the operator's word, rather than
    observed to. That is why it records no job id -- there is nothing to look up -- and why it
    is a separate verb rather than a flag on `convert`.
    """
    markdown_path = destination or markdown_path_for(pdf)
    if not markdown_path.exists():
        raise ConversionError(f"{markdown_path.name} does not exist; nothing to adopt")

    markdown = markdown_path.read_text(encoding="utf-8")
    usable, refusal, headings, sections, artefacts = inspect(markdown, source=pdf.name)
    source_sha256 = digest(pdf)

    sidecar_path_for(markdown_path).write_text(
        json.dumps(
            {
                "source_path": str(pdf),
                "source_sha256": source_sha256,
                "provider": provider,
                "provider_version": None,
                "job_id": None,
                "converted_at": None,
                "adopted_at": datetime.now(UTC).isoformat(),
                "adopted": True,
                "usable": usable,
                "refusal": refusal,
                "heading_count": headings,
                "sections": sections,
                "artefacts": list(artefacts),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return Conversion(
        source=pdf,
        markdown_path=markdown_path,
        source_sha256=source_sha256,
        markdown_bytes=markdown_path.stat().st_size,
        provider=provider,
        reused=True,
        usable=usable,
        refusal=refusal,
        heading_count=headings,
        sections=sections,
        artefacts=artefacts,
    )


async def convert_all(
    pdfs: list[Path],
    *,
    parser: MarkdownParser | None,
    force: bool = False,
    language: str = "en",
) -> list[Conversion]:
    """Convert documents one at a time.

    Sequential on purpose. LlamaParse rate-limits, and a failure halfway through a parallel
    batch leaves a mix of converted and unconverted files with no obvious resume point.
    """
    return [
        await convert(pdf, parser=parser, force=force, language=language) for pdf in pdfs
    ]


def build_markdown_parser() -> MarkdownParser:
    """The configured provider, refusing early if it cannot produce markdown."""
    from rcpia.core.config import Settings
    from rcpia.parsing.factory import build_parser

    parser = build_parser(Settings())
    if not isinstance(parser, MarkdownParser):
        raise ConversionError(
            f"PARSER_PROVIDER={parser.name} cannot produce markdown. "
            "Corpus conversion needs llamaparse."
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    import argparse

    cli = argparse.ArgumentParser(
        prog="python -m rcpia.corpus.convert",
        description=(
            "Convert source PDFs to markdown for the corpus build, and report what stage 1 "
            "makes of the result."
        ),
    )
    cli.add_argument("paths", nargs="+", type=Path, help="PDF files, or folders to search")
    cli.add_argument("--force", action="store_true", help="re-convert even if unchanged")
    cli.add_argument("--language", default="en", help="language hint for the provider")
    cli.add_argument(
        "--adopt",
        action="store_true",
        help="record provenance for markdown that already exists, without calling the "
        "provider; use once for documents converted by hand",
    )
    args = cli.parse_args(argv)

    pdfs: list[Path] = []
    for path in args.paths:
        pdfs.extend(sorted(path.rglob("*.pdf")) if path.is_dir() else [path])
    if not pdfs:
        print("no PDFs found")
        return 1

    if args.adopt:
        adopted, skipped = [], 0
        for pdf in pdfs:
            if not markdown_path_for(pdf).exists():
                skipped += 1
                continue
            adopted.append(adopt(pdf))
        for conversion in adopted:
            print(f"{conversion.source.name}\n    adopted: {conversion.summary}")
        unusable = sum(not c.usable for c in adopted)
        print(
            f"\n{len(adopted)} adopted, {skipped} without markdown, {unusable} unusable"
        )
        return 1 if unusable else 0

    # Decide what actually needs the provider before asking for one. Re-running this over a
    # folder that is already converted is the common case; it should not need an API key, and
    # a missing key should not hide the report of what is already there.
    pending = [
        pdf for pdf in pdfs if args.force or not is_current(pdf, markdown_path_for(pdf))
    ]

    parser: MarkdownParser | None = None
    if pending:
        try:
            parser = build_markdown_parser()
        except (ConversionError, ParserNotConfigured) as exc:
            print(f"{len(pending)} of {len(pdfs)} documents need converting, and: {exc}")
            return 1

    conversions = asyncio.run(
        convert_all(pdfs, parser=parser, force=args.force, language=args.language)
    )

    unusable = 0
    for conversion in conversions:
        print(f"{conversion.source.name}\n    {conversion.summary}")
        unusable += not conversion.usable
    print(f"\n{len(conversions)} documents, {unusable} unusable")
    return 1 if unusable else 0


if __name__ == "__main__":
    raise SystemExit(main())
