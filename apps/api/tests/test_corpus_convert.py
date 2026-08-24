"""Stage 0 converts a PDF and then checks its own output.

The cases below are the two failures that motivated the module -- a conversion with no
headings, which stage 1 refuses, and a re-parse that should not have happened -- plus the
provider protocol, exercised against a mock transport so no PDF leaves the machine and no
API key is needed.
"""

import json

import httpx
import pytest

from rcpia.corpus.convert import (
    ConversionError,
    adopt,
    convert,
    convert_all,
    digest,
    inspect,
    is_current,
    markdown_path_for,
    read_sidecar,
    sidecar_path_for,
)
from rcpia.parsing.base import (
    MarkdownParser,
    ParsedMarkdown,
    ParseRequest,
    ParserFailed,
)
from rcpia.parsing.llamaparse import LlamaParseParser

GOOD = "# Chapter\n\nBody.\n\n## Section\n\nMore body.\n"
# What the first ASPEN conversion looked like: real words, no structure at all.
HEADINGLESS = "Nutrition Considerations for the Oncology Patient    PRACTICE TOOL\n\nBody.\n"


class FakeParser:
    """Records what it was asked to convert, and returns whatever it was given."""

    name = "fake"

    def __init__(self, markdown: str = GOOD) -> None:
        self.markdown = markdown
        self.calls: list[str] = []

    async def parse_markdown(self, request: ParseRequest) -> ParsedMarkdown:
        self.calls.append(request.filename)
        return ParsedMarkdown(provider=self.name, markdown=self.markdown, job_id="job-1")


def write_pdf(tmp_path, name="guideline.pdf", body=b"%PDF-1.4 fake"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


@pytest.mark.asyncio
async def test_a_conversion_writes_markdown_beside_the_pdf(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    parser = FakeParser()

    result = await convert(pdf, parser=parser)

    assert result.markdown_path == markdown_path_for(pdf) == tmp_path / "guideline.md"
    assert result.markdown_path.read_text(encoding="utf-8") == GOOD
    assert result.usable and result.reused is False
    assert result.sections == 2


@pytest.mark.asyncio
async def test_a_conversion_records_the_digest_of_the_pdf_it_came_from(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    result = await convert(pdf, parser=FakeParser())

    sidecar = read_sidecar(result.markdown_path)
    assert sidecar is not None
    assert sidecar["source_sha256"] == digest(pdf)
    assert sidecar["provider"] == "fake"
    assert sidecar["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_an_unchanged_pdf_is_not_sent_again(tmp_path) -> None:
    # LlamaParse bills per page. Re-running the converter over a folder must not re-parse
    # every document in it.
    pdf = write_pdf(tmp_path)
    parser = FakeParser()

    first = await convert(pdf, parser=parser)
    second = await convert(pdf, parser=parser)

    assert parser.calls == ["guideline.pdf"]
    assert first.reused is False and second.reused is True
    assert second.sections == 2


@pytest.mark.asyncio
async def test_a_changed_pdf_is_converted_again(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    parser = FakeParser()
    await convert(pdf, parser=parser)

    pdf.write_bytes(b"%PDF-1.4 a new edition")
    again = await convert(pdf, parser=parser)

    assert parser.calls == ["guideline.pdf", "guideline.pdf"]
    assert again.reused is False


@pytest.mark.asyncio
async def test_force_re_parses_an_unchanged_pdf(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    parser = FakeParser()
    await convert(pdf, parser=parser)

    forced = await convert(pdf, parser=parser, force=True)

    assert parser.calls == ["guideline.pdf", "guideline.pdf"]
    assert forced.reused is False


@pytest.mark.asyncio
async def test_a_headingless_conversion_is_reported_unusable(tmp_path) -> None:
    # The ASPEN failure. Stage 1 refuses a document with no headings, and before this module
    # existed nothing said so until routing -- weeks later, after curation had been written.
    pdf = write_pdf(tmp_path)

    result = await convert(pdf, parser=FakeParser(HEADINGLESS))

    assert result.usable is False
    assert result.refusal is not None and "no markdown headings" in result.refusal
    assert result.heading_count == 0


@pytest.mark.asyncio
async def test_an_unusable_conversion_is_still_written_down(tmp_path) -> None:
    # Kept on disk, and the sidecar says it failed: the operator can look at what came back
    # instead of re-running the parse blind to find out.
    pdf = write_pdf(tmp_path)
    result = await convert(pdf, parser=FakeParser(HEADINGLESS))

    assert result.markdown_path.exists()
    sidecar = read_sidecar(result.markdown_path)
    assert sidecar is not None and sidecar["usable"] is False


@pytest.mark.asyncio
async def test_glyph_artefacts_are_reported_but_do_not_condemn_the_file(tmp_path) -> None:
    # "eGFR /H11021 30" retrieves for nothing, but the conversion is otherwise structured and
    # is worth looking at rather than refusing outright.
    pdf = write_pdf(tmp_path)
    markdown = "# Chapter\n\neGFR /H11021 30 and PSA /H11022 20.\n"

    result = await convert(pdf, parser=FakeParser(markdown))

    assert result.usable is True
    assert result.artefacts == ("/H11021", "/H11022")


@pytest.mark.asyncio
async def test_a_missing_pdf_is_refused_before_any_upload(tmp_path) -> None:
    parser = FakeParser()
    with pytest.raises(ConversionError, match="does not exist"):
        await convert(tmp_path / "absent.pdf", parser=parser)
    assert parser.calls == []


@pytest.mark.asyncio
async def test_a_batch_converts_every_document(tmp_path) -> None:
    pdfs = [write_pdf(tmp_path, f"g{n}.pdf", f"%PDF {n}".encode()) for n in range(3)]
    parser = FakeParser()

    results = await convert_all(pdfs, parser=parser)

    assert len(results) == 3
    assert parser.calls == ["g0.pdf", "g1.pdf", "g2.pdf"]


def test_a_markdown_without_a_sidecar_is_not_current(tmp_path) -> None:
    # A hand-dropped markdown file has no provenance, so it cannot be claimed to match.
    pdf = write_pdf(tmp_path)
    markdown = markdown_path_for(pdf)
    markdown.write_text(GOOD, encoding="utf-8")

    assert is_current(pdf, markdown) is False


def test_a_corrupt_sidecar_is_not_current(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    markdown = markdown_path_for(pdf)
    markdown.write_text(GOOD, encoding="utf-8")
    sidecar_path_for(markdown).write_text("{not json", encoding="utf-8")

    assert is_current(pdf, markdown) is False


@pytest.mark.asyncio
async def test_an_already_converted_pdf_needs_no_parser_at_all(tmp_path) -> None:
    # Re-running the converter over a folder that is already done is the common case, and it
    # must not require an API key. The CLI builds a parser only if something is pending; this
    # is the library half of that promise.
    pdf = write_pdf(tmp_path)
    await convert(pdf, parser=FakeParser())

    again = await convert(pdf, parser=None)

    assert again.reused is True and again.usable is True


@pytest.mark.asyncio
async def test_a_pdf_that_needs_converting_without_a_parser_says_so(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    with pytest.raises(ConversionError, match="needs converting"):
        await convert(pdf, parser=None)


@pytest.mark.asyncio
async def test_adopting_a_hand_conversion_stops_it_being_re_parsed(tmp_path) -> None:
    # Twenty-odd documents were converted by hand before this module existed. Without
    # provenance the first run over the corpus would re-parse every one of them and bill for
    # it, which is the mistake adoption exists to prevent.
    pdf = write_pdf(tmp_path)
    markdown_path_for(pdf).write_text(GOOD, encoding="utf-8")

    adopted = adopt(pdf)

    assert adopted.usable and adopted.sections == 2
    assert is_current(pdf, markdown_path_for(pdf)) is True
    # And now no parser is needed.
    again = await convert(pdf, parser=None)
    assert again.reused is True


def test_adoption_says_it_is_a_claim_not_an_observation(tmp_path) -> None:
    # There is no job to look up, and the sidecar must not imply there is.
    pdf = write_pdf(tmp_path)
    markdown_path_for(pdf).write_text(GOOD, encoding="utf-8")
    adopt(pdf)

    sidecar = read_sidecar(markdown_path_for(pdf))
    assert sidecar is not None
    assert sidecar["adopted"] is True
    assert sidecar["job_id"] is None and sidecar["converted_at"] is None


def test_adopting_a_headingless_conversion_reports_it(tmp_path) -> None:
    # Adoption is not a way to bless a broken conversion; it checks the same way.
    pdf = write_pdf(tmp_path)
    markdown_path_for(pdf).write_text(HEADINGLESS, encoding="utf-8")

    adopted = adopt(pdf)

    assert adopted.usable is False


def test_adopting_without_markdown_is_refused(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    with pytest.raises(ConversionError, match="nothing to adopt"):
        adopt(pdf)


def test_inspect_counts_headings_and_sections() -> None:
    usable, refusal, headings, sections, artefacts = inspect(GOOD, source="x")
    assert usable and refusal is None
    assert headings == 2 and sections == 2 and artefacts == ()


# --- the provider protocol ------------------------------------------------------------


def markdown_transport(markdown: str = GOOD, *, result_status: int = 200) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-1"})
        if request.url.path.endswith("/job/job-1"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        if request.url.path.endswith("/result/markdown"):
            return httpx.Response(result_status, json={"markdown": markdown})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_llamaparse_returns_markdown() -> None:
    async with httpx.AsyncClient(transport=markdown_transport()) as client:
        parser = LlamaParseParser(api_key="llx-test", client=client, poll_interval=0)
        parsed = await parser.parse_markdown(
            ParseRequest(filename="g.pdf", content=b"%PDF", content_type="application/pdf")
        )

    assert parsed.markdown == GOOD
    assert parsed.provider == "llamaparse"
    assert parsed.job_id == "job-1"


@pytest.mark.asyncio
async def test_llamaparse_satisfies_the_markdown_protocol() -> None:
    assert isinstance(LlamaParseParser(api_key="llx-test"), MarkdownParser)


@pytest.mark.asyncio
async def test_an_empty_markdown_result_is_a_failure() -> None:
    # A scanned PDF with no text layer parses "successfully" and returns nothing. Writing
    # that to disk would create an empty document that stage 1 refuses for the wrong reason.
    async with httpx.AsyncClient(transport=markdown_transport("   \n")) as client:
        parser = LlamaParseParser(api_key="llx-test", client=client, poll_interval=0)
        with pytest.raises(ParserFailed, match="markdown vide"):
            await parser.parse_markdown(
                ParseRequest(filename="g.pdf", content=b"%PDF", content_type="application/pdf")
            )


@pytest.mark.asyncio
async def test_a_failed_markdown_fetch_is_translated() -> None:
    async with httpx.AsyncClient(transport=markdown_transport(result_status=500)) as client:
        parser = LlamaParseParser(api_key="llx-test", client=client, poll_interval=0)
        with pytest.raises(ParserFailed, match="indisponible"):
            await parser.parse_markdown(
                ParseRequest(filename="g.pdf", content=b"%PDF", content_type="application/pdf")
            )


@pytest.mark.asyncio
async def test_the_json_path_still_works(tmp_path) -> None:
    # The dossier path fetches JSON for page provenance. Adding markdown must not disturb it.
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-1"})
        if request.url.path.endswith("/job/job-1"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        if request.url.path.endswith("/result/json"):
            return httpx.Response(
                200, json={"pages": [{"page": 1, "text": "Body", "items": []}]}
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        parser = LlamaParseParser(api_key="llx-test", client=client, poll_interval=0)
        parsed = await parser.parse(
            ParseRequest(filename="g.pdf", content=b"%PDF", content_type="application/pdf")
        )

    assert parsed.provider == "llamaparse"
    assert parsed.raw_text.strip() == "Body"


def test_the_sidecar_is_json_and_readable(tmp_path) -> None:
    pdf = write_pdf(tmp_path)
    markdown = markdown_path_for(pdf)
    markdown.write_text(GOOD, encoding="utf-8")
    sidecar_path_for(markdown).write_text(
        json.dumps({"source_sha256": digest(pdf)}), encoding="utf-8"
    )

    assert is_current(pdf, markdown) is True
