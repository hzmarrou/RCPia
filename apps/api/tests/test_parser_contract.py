"""Parser contract tests.

Both providers are exercised through the same interface. LlamaParse runs against a mock
transport that mimics its upload/poll/result protocol, so the mapping this code owns —
page numbers, sections, block types, warnings, failure translation — is covered without
sending a dossier to the cloud or needing an API key.
"""

import json
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.core.config import Settings
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    CancerType,
    Case,
    CaseStatus,
    DocumentChunk,
    Organization,
    ParseStatus,
    UploadedDocument,
)
from rcpia.parsing.base import (
    ParsedBlock,
    ParsedDocument,
    ParseRequest,
    ParserFailed,
    ParserNotConfigured,
)
from rcpia.parsing.factory import build_parser
from rcpia.parsing.liteparse import LiteParseParser
from rcpia.parsing.llamaparse import LlamaParseParser
from rcpia.parsing.quality import quality_warnings
from rcpia.parsing.service import parse_document
from rcpia.storage.local import LocalBlobStore

PDF_BYTES = b"%PDF-1.7\nfake dossier\n%%EOF\n"

RESULT_PAYLOAD = {
    "pages": [
        {
            "page": 1,
            "text": "Antécédents médicaux : HTA sous Losartan 50mg/j depuis 8 ans.",
            "items": [
                {
                    "type": "heading",
                    "value": "Antécédents",
                    "bBox": {"x": 1, "y": 2, "w": 3, "h": 4},
                },
                {"type": "text", "value": "HTA sous Losartan 50mg/j depuis 8 ans."},
                {"type": "table", "rows": [["Hb", "11.6"], ["Créatinine", "17.33"]]},
                {"type": "text", "value": "   "},
            ],
        },
        {
            "page": 2,
            "text": "pTNM : p T2, haut grade, infiltration du muscle.",
            "items": [{"type": "text", "value": "pTNM : p T2, haut grade."}],
        },
    ],
    "job_metadata": {"job_version": "2026.05.01"},
    "warnings": ["Page 2 partially rasterized"],
}


def llamaparse_with(handler) -> LlamaParseParser:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://parser.test")
    return LlamaParseParser(
        "test-key", client=client, poll_interval=0.001, timeout_seconds=0.05
    )


def successful_handler(*, statuses: list[str] | None = None):
    """Mimic upload -> poll -> result, optionally with intermediate PENDING polls."""
    remaining = list(statuses or ["SUCCESS"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-123"})
        if request.url.path.endswith("/result/json"):
            return httpx.Response(200, json=RESULT_PAYLOAD)
        status = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return httpx.Response(200, json={"status": status})

    return handler


# --------------------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------------------


def test_provider_is_chosen_by_configuration() -> None:
    llama = build_parser(Settings(parser_provider="llamaparse", llamaparse_api_key="k"))
    lite = build_parser(Settings(parser_provider="liteparse"))

    assert isinstance(llama, LlamaParseParser)
    assert isinstance(lite, LiteParseParser)


def test_llamaparse_without_an_api_key_refuses_to_start() -> None:
    with pytest.raises(ParserNotConfigured, match="LLAMAPARSE_API_KEY"):
        build_parser(Settings(parser_provider="llamaparse", llamaparse_api_key=""))


async def test_liteparse_says_it_is_not_available_yet() -> None:
    """It must not quietly fall back to the cloud provider."""
    with pytest.raises(ParserNotConfigured, match="liteparse"):
        await LiteParseParser().parse(
            ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
        )


# --------------------------------------------------------------------------------------
# LlamaParse protocol
# --------------------------------------------------------------------------------------


async def test_parse_maps_pages_sections_and_block_types() -> None:
    parser = llamaparse_with(successful_handler())

    document = await parser.parse(
        ParseRequest(filename="dossier.pdf", content=PDF_BYTES, content_type="application/pdf")
    )

    assert document.provider == "llamaparse"
    assert document.provider_version == "2026.05.01"
    assert [block.block_type for block in document.blocks] == [
        "heading",
        "paragraph",
        "table",
        "paragraph",
    ]
    assert [block.page for block in document.blocks] == [1, 1, 1, 2]
    # The heading becomes the section for everything that follows it on the page.
    assert document.blocks[1].section == "Antécédents"
    assert document.blocks[0].bbox == [1.0, 2.0, 3.0, 4.0]
    assert "Créatinine | 17.33" in document.blocks[2].text
    assert document.warnings == ["Page 2 partially rasterized"]
    assert "pTNM" in document.raw_text


async def test_blank_items_are_dropped() -> None:
    parser = llamaparse_with(successful_handler())

    document = await parser.parse(
        ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
    )

    assert all(block.text.strip() for block in document.blocks)


async def test_parse_polls_until_the_job_finishes() -> None:
    parser = llamaparse_with(successful_handler(statuses=["PENDING", "PENDING", "SUCCESS"]))

    document = await parser.parse(
        ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
    )

    assert document.blocks


async def test_a_rejected_api_key_is_a_configuration_error() -> None:
    """Not retryable: retrying a wrong key three times only delays telling a human."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid key"})

    with pytest.raises(ParserNotConfigured, match="clé d'API"):
        await llamaparse_with(handler).parse(
            ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
        )


async def test_a_failed_parsing_job_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-1"})
        return httpx.Response(200, json={"status": "ERROR"})

    with pytest.raises(ParserFailed, match="ERROR"):
        await llamaparse_with(handler).parse(
            ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
        )


async def test_a_job_that_never_finishes_times_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-1"})
        return httpx.Response(200, json={"status": "PENDING"})

    with pytest.raises(ParserFailed, match="délai"):
        await llamaparse_with(handler).parse(
            ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
        )


async def test_network_failure_is_translated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(ParserFailed, match="injoignable"):
        await llamaparse_with(handler).parse(
            ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
        )


async def test_the_language_hint_is_sent() -> None:
    """Dossiers are French; the provider parses them better when told."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            seen["body"] = request.content.decode("utf-8", errors="replace")
            return httpx.Response(200, json={"id": "job-1"})
        if request.url.path.endswith("/result/json"):
            return httpx.Response(200, json=RESULT_PAYLOAD)
        return httpx.Response(200, json={"status": "SUCCESS"})

    await llamaparse_with(handler).parse(
        ParseRequest(filename="d.pdf", content=PDF_BYTES, content_type="application/pdf")
    )

    assert "fr" in seen["body"]


# --------------------------------------------------------------------------------------
# Quality warnings
# --------------------------------------------------------------------------------------


def test_an_empty_parse_is_flagged() -> None:
    warnings = quality_warnings(ParsedDocument(provider="llamaparse"))

    assert warnings == ["Aucun contenu n'a pu être extrait de ce document."]


def test_a_thin_parse_is_flagged_as_possibly_scanned() -> None:
    document = ParsedDocument(
        provider="llamaparse",
        blocks=[ParsedBlock(text="PSA", page=1)],
        raw_text="PSA 12.4",
    )

    warnings = quality_warnings(document)

    assert any("Très peu de texte" in warning for warning in warnings)


def test_mostly_empty_blocks_are_flagged() -> None:
    document = ParsedDocument(
        provider="llamaparse",
        blocks=[ParsedBlock(text="x" * 2000, page=1)] + [ParsedBlock(text=" ", page=1)] * 9,
        raw_text="x" * 2000,
    )

    warnings = quality_warnings(document)

    assert any("scanné" in warning for warning in warnings)


def test_missing_pages_are_named() -> None:
    document = ParsedDocument(
        provider="llamaparse",
        blocks=[ParsedBlock(text="x" * 1200, page=1), ParsedBlock(text="y" * 1200, page=3)],
        raw_text="x" * 2400,
    )

    warnings = quality_warnings(document)

    assert any("2" in warning and "page" in warning.lower() for warning in warnings)


def test_a_healthy_parse_produces_no_warnings() -> None:
    document = ParsedDocument(
        provider="llamaparse",
        blocks=[ParsedBlock(text="x" * 600, page=1), ParsedBlock(text="y" * 600, page=2)],
        raw_text="x" * 1200,
    )

    assert quality_warnings(document) == []


# --------------------------------------------------------------------------------------
# The parsing job, end to end against the database
# --------------------------------------------------------------------------------------


async def seed_document(
    factory: async_sessionmaker[AsyncSession], blobs: LocalBlobStore
) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
        session.add(organization)
        await session.flush()
        case = Case(
            organization_id=organization.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
            status=CaseStatus.parsing,
        )
        session.add(case)
        await session.flush()

        document_id = uuid.uuid4()
        key = f"cases/{case.id}/dossiers/{document_id}.pdf"
        await blobs.put_bytes(key, PDF_BYTES, "application/pdf")
        session.add(
            UploadedDocument(
                id=document_id,
                case_id=case.id,
                organization_id=organization.id,
                blob_key=key,
                original_filename="dossier.pdf",
                content_type="application/pdf",
                checksum_sha256="a" * 64,
                size_bytes=len(PDF_BYTES),
                parse_status=ParseStatus.pending,
            )
        )
        await session.commit()
        return case.id, document_id


def parse_job(case_id: uuid.UUID, document_id: uuid.UUID, organization_id: uuid.UUID):
    return AnalysisJob(
        case_id=case_id,
        organization_id=organization_id,
        job_type="parse_dossier",
        payload={"document_id": str(document_id)},
    )


async def test_parsing_stores_chunks_with_their_provenance(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    blobs = LocalBlobStore(tmp_path, signing_secret="s")
    case_id, document_id = await seed_document(session_factory, blobs)
    parser = llamaparse_with(successful_handler())

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        job = parse_job(case_id, document_id, document.organization_id)
        session.add(job)
        await session.flush()

        outcome = await parse_document(session, job, parser=parser, blobs=blobs)
        await session.commit()

    assert outcome is None, "success"

    async with session_factory() as session:
        chunks = (
            (
                await session.execute(
                    sa.select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.ordinal)
                )
            )
            .scalars()
            .all()
        )
        document = await session.get(UploadedDocument, document_id)
        case = await session.get(Case, case_id)

        assert len(chunks) == 4
        assert chunks[0].block_type.value == "heading"
        assert chunks[1].section == "Antécédents"
        assert chunks[3].page == 2
        assert document is not None
        assert document.parse_status is ParseStatus.parsed
        assert document.parser_provider == "llamaparse"
        assert document.parser_provider_version == "2026.05.01"
        assert "Page 2 partially rasterized" in document.parse_warnings
        assert case is not None
        assert case.status is CaseStatus.ready_for_review, "all documents parsed"


async def test_reparsing_replaces_chunks_rather_than_duplicating_them(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    """Job delivery is at-least-once, so the handler must be idempotent."""
    blobs = LocalBlobStore(tmp_path, signing_secret="s")
    case_id, document_id = await seed_document(session_factory, blobs)
    parser = llamaparse_with(successful_handler())

    for _ in range(2):
        async with session_factory() as session:
            document = await session.get(UploadedDocument, document_id)
            assert document is not None
            job = parse_job(case_id, document_id, document.organization_id)
            session.add(job)
            await session.flush()
            await parse_document(session, job, parser=parser, blobs=blobs)
            await session.commit()

    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
        )
        assert count == 4


async def test_a_thin_parse_warns_the_uploader(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    blobs = LocalBlobStore(tmp_path, signing_secret="s")
    case_id, document_id = await seed_document(session_factory, blobs)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-1"})
        if request.url.path.endswith("/result/json"):
            return httpx.Response(
                200,
                json={
                    "pages": [
                        {"page": 1, "text": "PSA", "items": [{"type": "text", "value": "PSA"}]}
                    ]
                },
            )
        return httpx.Response(200, json={"status": "SUCCESS"})

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        job = parse_job(case_id, document_id, document.organization_id)
        session.add(job)
        await session.flush()
        await parse_document(session, job, parser=llamaparse_with(handler), blobs=blobs)
        await session.commit()

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        assert any("Très peu de texte" in warning for warning in document.parse_warnings)


async def test_a_misconfigured_parser_fails_the_document_without_retrying(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    blobs = LocalBlobStore(tmp_path, signing_secret="s")
    case_id, document_id = await seed_document(session_factory, blobs)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        job = parse_job(case_id, document_id, document.organization_id)
        session.add(job)
        await session.flush()

        outcome = await parse_document(session, job, parser=llamaparse_with(handler), blobs=blobs)
        await session.commit()

    assert outcome is not None and outcome.status.value == "failed"

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        assert document.parse_status is ParseStatus.failed
        assert any("clé d'API" in warning for warning in document.parse_warnings)

        event = await session.scalar(
            sa.select(AuditEvent).where(AuditEvent.event_type == "document.parse_failed")
        )
        assert event is not None
        assert event.payload["retryable"] is False


async def test_a_transient_parser_failure_is_raised_for_retry(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    """The worker turns a raised error into a retry with backoff."""
    blobs = LocalBlobStore(tmp_path, signing_secret="s")
    case_id, document_id = await seed_document(session_factory, blobs)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "job-1"})
        return httpx.Response(200, json={"status": "ERROR"})

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        job = parse_job(case_id, document_id, document.organization_id)
        session.add(job)
        await session.flush()

        with pytest.raises(RuntimeError):
            await parse_document(session, job, parser=llamaparse_with(handler), blobs=blobs)


async def test_a_missing_blob_fails_the_document(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    blobs = LocalBlobStore(tmp_path, signing_secret="s")
    case_id, document_id = await seed_document(session_factory, blobs)
    await blobs.delete(f"cases/{case_id}/dossiers/{document_id}.pdf")

    async with session_factory() as session:
        document = await session.get(UploadedDocument, document_id)
        assert document is not None
        job = parse_job(case_id, document_id, document.organization_id)
        session.add(job)
        await session.flush()

        with pytest.raises(RuntimeError, match="illisible"):
            await parse_document(
                session, job, parser=llamaparse_with(successful_handler()), blobs=blobs
            )


def test_parsed_document_serializes_to_json() -> None:
    """Blocks cross process boundaries as JSON before they become chunks."""
    document = ParsedDocument(
        provider="llamaparse",
        blocks=[ParsedBlock(text="PSA 12.4", page=1, section="Biologie", bbox=[1, 2, 3, 4])],
        raw_text="PSA 12.4",
    )

    restored = ParsedDocument.model_validate(json.loads(document.model_dump_json()))

    assert restored == document
