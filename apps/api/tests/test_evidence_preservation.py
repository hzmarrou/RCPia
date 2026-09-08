"""Synthetic preservation and full-TaskBundle coverage, not provider/clinical evaluation."""

import json
import shutil
import uuid
from dataclasses import asdict, replace
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents import bundle as bundles
from rcpia.agents import evidence
from rcpia.agents.opinions import _passages_of
from rcpia.agents.schemas import (
    MAX_TEXT,
    AgentOutput,
    GuidelinePassage,
    SourceSnippet,
    UncitedEvidence,
    check_citations,
)
from rcpia.agents.serialization import bundle_bytes
from rcpia.core.models import CancerType, Case, CaseLedgerVersion
from rcpia.evaluation.runtime import CheckedReranker, evaluate_snapshot
from rcpia.evidence.hashing import text_identity
from rcpia.evidence.models import EvidenceChunk
from rcpia.evidence.payload import (
    MAX_EVIDENCE_CHARACTERS,
    EvidencePreparationError,
    prepare_evidence,
)
from rcpia.evidence.retrieval import RetrievalReport
from rcpia.pps.generator import build_evidence
from rcpia.query.questions import SUPPORTIVE_QUESTION
from tests.test_agent_supportive_retrieval import chunk
from tests.test_pi_adapter import adapter as adapter
from tests.test_pi_adapter import agents_root as agents_root
from tests.test_rerank_client import call_with
from tests.test_runtime_evidence import FakeEmbedder, FakeReranker, parsed, seed


def padded_table(*, ending: str = "\n", width: int = 240) -> str:
    rows = [
        ["Header A", "Header B", "Header C"],
        ["-" * width, ":" + "-" * (width - 1), "-" * (width - 1) + ":"],
        *[[f"Row {i}", "internal  spaces<br/>kept", "value"] for i in range(13)],
        ["FINAL_ROW", "LAST_CELL", "QUALIFICATION_IN_CELL"],
    ]
    return ending.join("|" + "|".join(cell.ljust(width) for cell in row) + "|" for row in rows)


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_padding_only_preserves_every_cell_line_ending_and_qualification(ending: str) -> None:
    prefix = "Source title > Table 11. Continued" + ending + ending
    suffix = ending + "Qualification outside the table. \u00e9"
    table = padded_table(ending=ending)
    original = prefix + table + suffix
    prepared = prepare_evidence(original)
    assert len(original) > MAX_EVIDENCE_CHARACTERS
    assert prepared.transformation == "markdown_cell_padding"
    assert prepared.text.startswith(prefix) and prepared.text.endswith(suffix)
    assert "|FINAL_ROW|LAST_CELL|QUALIFICATION_IN_CELL|" in prepared.text
    assert "internal  spaces<br/>kept" in prepared.text
    before = [[cell.strip(" ") for cell in line.split("|")] for line in table.split(ending)]
    compacted_table = prepared.text[len(prefix):-len(suffix)]
    after = [line.split("|") for line in compacted_table.split(ending)]
    assert before == after
    assert compacted_table.count(ending) == table.count(ending)
    assert prepare_evidence(prepared.text).text == prepared.text


@pytest.mark.parametrize(
    "text",
    [
        "\u00e9\r\nline\nnext\r",
        "| Header | Value |\n| --- | --- |\n| ` padded  code ` | a\\|b |",
        "<table><tr><td colspan=\"2\">  padded  HTML<br/>  </td></tr></table>",
        "x" * MAX_EVIDENCE_CHARACTERS,
        "<pre>\n| Header | Value |\n| --- | --- |\n</pre>",
    ],
)
def test_units_that_fit_are_byte_identical(text: str) -> None:
    result = prepare_evidence(text)
    assert result.text.encode("utf-8") == text.encode("utf-8")
    assert result.transformation == "verbatim"


@pytest.mark.parametrize(
    "before,after",
    [
        ("```\n", "\n```"),
        ("~~~text\n", "\n~~~"),
        ("`\n", "\n`"),
        ("``multiline\n", "\ncode``"),
        ("<pre>\n", "\n</pre>"),
        ("<code\n class=\"example\">\n", "\n</code>"),
        ("<!--\n", "\n-->"),
        ("<![CDATA[\n", "\n]]>"),
        ("<table><tr><td colspan=\"2\">\n", "\n</td></tr></table>"),
        ("<script>\n", "\n</script>"),
        ("<div>\n", "\n</div>"),
        ("Inline ` padded code ` elsewhere\n", ""),
        ("An escaped \\| elsewhere\n", ""),
        ("<PRE>\n", "\n</PRE>"),
        ("<br data-extra=\"unsafe\">\n", ""),
        ('[ref]: /source "\n', '\n"'),
        ("[ref]: /source '\n", "\n'"),
        ("[ref]: /source (\n", "\n)"),
        ('   [ref]: /source "\n', '\n"'),
        ('[multi\nline]: /source "\n', '\n"'),
        ('> [ref]: /source "\n', '\n"'),
    ],
)
def test_ambiguous_code_html_or_reference_unit_is_refused_without_partial_compaction(
    before: str, after: str
) -> None:
    with pytest.raises(EvidencePreparationError, match="aucune troncature"):
        prepare_evidence(before + padded_table() + after)


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("internal  spaces", "` padded  code `", 1),
        lambda text: text.replace("LAST_CELL", "escaped\\|pipe", 1),
        lambda text: text.replace("LAST_CELL", "has\ttab", 1),
        lambda text: text.replace("|FINAL_ROW", "|extra|FINAL_ROW", 1),
        lambda text: text.replace(":" + "-" * 239, "not a delimiter", 1),
        lambda text: "\n".join("    " + line for line in text.splitlines()),
        lambda text: text.replace("|\n", "\n", 1),
    ],
)
def test_unsupported_markdown_is_not_repaired(change) -> None:
    with pytest.raises(EvidencePreparationError):
        prepare_evidence(change(padded_table()))


def test_irreducible_text_and_tables_are_refused() -> None:
    with pytest.raises(EvidencePreparationError):
        prepare_evidence("x" * (MAX_EVIDENCE_CHARACTERS + 1))
    with pytest.raises(EvidencePreparationError):
        prepare_evidence("|Header|\n|---|\n|" + "content" * 1200 + "|")


def test_metadata_and_raw_source_identity_survive_conversion() -> None:
    raw = padded_table() + "\nFINAL_QUALIFICATION"
    source = replace(
        chunk("fixture.source.2026.continued.01.0010", "fixture.source.2026", raw),
        contextual_text="Original title > Table 13. Continued\n\n" + raw,
        section_path=("Original title", "Table 13. Continued"),
        unit_type="general_principle",
        page_start=17,
        page_end=19,
        recommendation_number=42,
    )
    passage = evidence.to_passage(source)
    assert passage.id == source.chunk_id
    assert passage.document_id == source.document_id
    assert passage.source == source.citation_label
    assert passage.version == source.version_label
    assert passage.clinical_status == "poc_unreviewed"
    assert passage.section == "Original title > Table 13. Continued"
    assert (passage.page, passage.page_end, passage.recommendation_number) == (17, 19, 42)
    assert passage.locator_method == source.locator_method
    assert passage.text.endswith("FINAL_QUALIFICATION")
    assert source.text == raw
    assert passage.text == prepare_evidence(source.contextual_text).text
    with pytest.raises(EvidencePreparationError, match="contexte"):
        evidence.to_passage(replace(source, contextual_text="Context missing the raw source"))


def test_guideline_bound_changes_no_other_text_bound_and_reads_old_stored_bundles() -> None:
    assert MAX_TEXT == 4000 and MAX_EVIDENCE_CHARACTERS == 8000
    old = {
        "id": "fixture.source.2026.old.01.0001",
        "text": "Stored raw excerpt [historical truncation marker]",
        "source": "Historical source",
        "clinical_status": "poc_unreviewed",
        "section": None,
    }
    restored = _passages_of(SimpleNamespace(bundle={"inputs": {"guideline_passages": [old]}}))
    assert len(restored) == 1 and restored[0].text == old["text"]
    assert restored[0].page_end is None and restored[0].section is None
    assert GuidelinePassage(**{**old, "text": "x" * 8000}).text == "x" * 8000
    with pytest.raises(ValidationError):
        GuidelinePassage(**{**old, "text": "x" * 8001})
    with pytest.raises(ValidationError):
        SourceSnippet(id="snip-1", source_document_id="source", text="x" * 4001)


async def test_actual_http_payload_and_checked_trace_share_the_preparation(monkeypatch) -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"results": [{"index": i, "relevance_score": 0.9} for i in range(2)]},
        )

    azure, _ = await call_with(monkeypatch, handler)
    checked = CheckedReranker(azure)
    documents = ["Short \u00e9\r\nunit", "Table title\n\n" + padded_table()]
    assert await checked.score("Synthetic question", documents) == [0.9, 0.9]
    assert sent[0]["documents"] == [prepare_evidence(text).text for text in documents]
    for original, actual, traced in zip(
        documents, sent[0]["documents"], checked.calls[0]["documents"], strict=True
    ):
        assert traced["contextual_text"] == text_identity(original)
        assert traced["azure_payload_text"] == text_identity(actual)
        assert not traced["clipped"]


def snapshot_objects() -> tuple[Case, CaseLedgerVersion]:
    """No application rows are persisted; the session is only for fixture retrieval."""
    entries = parsed().snapshots[0].entries
    entries[0] = replace(
        entries[0],
        source_excerpt="Fixture provenance \u00e9\r\nsecond line\nthird line",
        source_document_id="fixture-source",
    )
    return (
        Case(id=uuid.UUID(int=1), cancer_type=CancerType.bladder),
        CaseLedgerVersion(version=1, ledger={"entries": [asdict(entry) for entry in entries]}),
    )


async def full_bundle(session):
    case, version = snapshot_objects()
    return await bundles.build_bundle(
        session,
        case=case,
        version=version,
        agent_role="medical_oncology",
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
    )


async def test_full_bundle_real_fixture_retrieval_and_citation_round_trip(
    session: AsyncSession,
) -> None:
    await seed(session)
    primary_table = padded_table()
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "p00")
        .values(
            text=primary_table,
            contextual_text="p00|Stored heading|\n\n" + primary_table,
            unit_type="decision_table",
            embedding=None,
            embedding_model=None,
        )
    )
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "s02")
        .values(page_end=9)
    )
    built = await full_bundle(session)
    task = built.bundle
    assert len(task.inputs.guideline_passages) == 13
    assert task.inputs.case_ledger and task.inputs.source_snippets
    assert task.output_format.schema_ and task.constraints
    assert "|FINAL_ROW|LAST_CELL|QUALIFICATION_IN_CELL|" in task.inputs.guideline_passages[0].text
    passage = next(p for p in task.inputs.guideline_passages if p.id == "s02")
    assert "|Final row|Fixture|" in passage.text
    assert passage.text.endswith("Qualification outside the table.")
    assert passage.section == "Stored heading" and passage.page_end == 9
    assert any("seconde recherche" in note for note in task.constraints)
    assert any("compact" in note for note in task.constraints)
    stored = json.loads(bundle_bytes(task))
    restored = _passages_of(SimpleNamespace(bundle=stored))
    assert restored == task.inputs.guideline_passages
    output = AgentOutput(
        case_id=str(uuid.UUID(int=1)),
        agent_role="medical_oncology",
        recommendation="Synthetic fixture response.",
        rationale="This tests citation plumbing, not a clinical recommendation.",
        evidence_references=[{"id": "S02", "supports": "Synthetic source unit."}],
    )
    check_citations(output, task)
    resolved = build_evidence(SimpleNamespace(bundle=stored), output)
    assert resolved[0].id == "s02" and resolved[0].quote == passage.text
    assert (resolved[0].page, resolved[0].page_end) == (7, 9)
    output.evidence_references[0].id = "s02-fragment"
    with pytest.raises(UncitedEvidence):
        check_citations(output, task)


async def test_full_bundle_gate_uses_exact_attachment_bytes(
    session: AsyncSession, monkeypatch, adapter, agents_root
) -> None:
    await seed(session)
    task = (await full_bundle(session)).bundle
    actual = bundle_bytes(task)
    compact = task.model_dump_json(by_alias=True).encode("utf-8")
    assert len(actual) > len(compact)
    assert len(actual) > len(actual.decode("utf-8"))
    monkeypatch.setattr(bundles, "MAX_BUNDLE_BYTES", len(actual))
    accepted = (await full_bundle(session)).bundle
    assert bundle_bytes(accepted) == actual
    shutil.copytree(agents_root / "urology_oncology", agents_root / "medical_oncology")
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", "ok")
    case_id, run_id = uuid.UUID(int=1), uuid.uuid4()
    await adapter.run(
        bundle=accepted, agent_name="medical_oncology", case_id=case_id, run_id=run_id
    )
    physical = adapter.task_path(case_id, "medical_oncology", run_id).read_bytes()
    assert physical == actual and len(physical) == bundles.MAX_BUNDLE_BYTES
    for limit in (len(actual) - 1, len(compact)):
        monkeypatch.setattr(bundles, "MAX_BUNDLE_BYTES", limit)
        with pytest.raises(bundles.BundleTooLarge):
            await full_bundle(session)


async def test_full_bundle_aggregate_unicode_bound_refuses_without_trimming(monkeypatch) -> None:
    calls = []
    units = [chunk(f"fixture.source.2026.unit.01.{i:04}", "fixture.source.2026",
                   "\U0001f9ea" * 7500) for i in range(14)]

    async def selected(session, **kwargs):
        calls.append(kwargs)
        return RetrievalReport(
            chunks=units[10:] if kwargs["question"] == SUPPORTIVE_QUESTION else units[:10]
        )

    monkeypatch.setattr(evidence, "search", selected)
    with pytest.raises(bundles.BundleTooLarge):
        await full_bundle(None)
    assert [call["limit"] for call in calls] == [10, 4]
    assert all(len(unit.text) == 7500 for unit in units)


async def test_full_bundle_no_database_acceptance_seam_preserves_all_fourteen_units(
    monkeypatch,
) -> None:
    html = "<table>\r\n" + "".join(
        f'<tr>\r\n<td colspan="2">  cell  {i}  <br/>  preserved  </td>\r\n</tr>\r\n'
        for i in range(80)
    ) + "</table>\r\nHTML_FINAL_QUALIFICATION"
    assert MAX_TEXT < len(html) < MAX_EVIDENCE_CHARACTERS
    units = []
    for i in range(14):
        raw = html if i == 0 else padded_table() + "\nFINAL_QUALIFICATION"
        units.append(replace(
            chunk(f"fixture.source.2026.unit.01.{i:04}", "fixture.source.2026", raw),
            contextual_text="Original title > Table 11. Continued\n\n" + raw,
            section_path=("Original title", "Table 11. Continued"),
            unit_type="general_principle" if i % 2 == 0 else "decision_table",
        ))

    async def selected(session, **kwargs):
        assert session is None
        supportive = kwargs["question"] == SUPPORTIVE_QUESTION
        assert kwargs["limit"] == (4 if supportive else 10)
        return RetrievalReport(chunks=units[10:] if supportive else units[:10])

    monkeypatch.setattr(evidence, "search", selected)
    task = (await full_bundle(None)).bundle
    assert len(task.inputs.guideline_passages) == 14
    for unit, passage in zip(units, task.inputs.guideline_passages, strict=True):
        assert passage.id == unit.chunk_id
        assert passage.text == prepare_evidence(unit.contextual_text).text
    assert task.inputs.guideline_passages[0].text.endswith(html)
    assert task.inputs.guideline_passages[0].text.encode("utf-8") == (
        units[0].contextual_text.encode("utf-8")
    )
    assert len(bundle_bytes(task)) <= bundles.MAX_BUNDLE_BYTES


async def test_unselected_oversized_candidate_stops_search_bundle_and_runtime_before_http(
    session: AsyncSession, monkeypatch
) -> None:
    await seed(session)
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "keyword-only")
        .values(
            text="TVIM " + "x" * 8001,
            contextual_text="keyword-only|TVIM " + "x" * 8001,
        )
    )
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise AssertionError("No HTTP request may score a partial candidate set.")

    azure, _ = await call_with(monkeypatch, handler)
    case, version = snapshot_objects()
    with pytest.raises(bundles.BundleError, match="Analyse arretee"):
        await bundles.build_bundle(
            session, case=case, version=version, agent_role="medical_oncology",
            embedder=FakeEmbedder(), reranker=azure,
        )
    trace = evidence.EvidenceTrace()
    snapshot = parsed().snapshots[0]
    with pytest.raises(EvidencePreparationError):
        await evidence.guideline_passages_for(
            session, cancer_type=snapshot.cancer_type, agent_role=snapshot.agent_role,
            entries=snapshot.entries, embedder=FakeEmbedder(), reranker=azure, trace=trace,
        )
    assert not trace.complete and trace.evidence_pack_sha256 is None
    with pytest.raises(EvidencePreparationError):
        await evaluate_snapshot(session, snapshot, embedder=FakeEmbedder(), reranker=azure)
    assert sent == []
