"""Synthetic, isolated evidence-delivery tests. No provider or generation execution."""

import copy
import json
import shutil
import subprocess
import uuid
from argparse import Namespace
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from rcpia.agents import evidence
from rcpia.agents.schemas import MAX_TEXT
from rcpia.evaluation import runtime
from rcpia.evaluation.goldset import EvidenceGroup, Goldset, GoldsetError, Question
from rcpia.evaluation.runtime import evaluate_run, evaluate_snapshot, load_inputs
from rcpia.evaluation.runtime_inputs import selected_labels
from rcpia.evaluation.runtime_provenance import (
    check_output,
    corpus_identity,
    readonly_snapshot,
    source_identity,
)
from rcpia.evidence.embedding import EmbeddingError
from rcpia.evidence.hashing import canonical_json, payload_sha256, text_identity
from rcpia.evidence.ingestion import CorpusError
from rcpia.evidence.models import EMBEDDING_DIMENSIONS, EvidenceChunk, EvidenceDocument
from rcpia.evidence.rerank import MAX_DOCUMENT_CHARACTERS, RerankError
from rcpia.query.questions import SUPPORTIVE_QUESTION, question_for

MODEL = "synthetic-embedding"


def input_record() -> dict:
    return {
        "schema_version": "rcpia.runtime-input.v1",
        "expected_corpus_sha256": "0" * 64,
        "snapshots": [
            {
                "snapshot_id": "synthetic-01",
                "case_id": "synthetic-case",
                "ledger_version": 1,
                "cancer_type": "bladder",
                "agent_role": "medical_oncology",
                "operator_question": None,
                "entries": [
                    {
                        "key": key,
                        "label": key,
                        "category": "disease",
                        "value": value,
                        "status": "confirmed",
                        "origin": "machine",
                        "highlight": "neutral",
                    }
                    for key, value in (("ctnm", "cT2 N0 M0"), ("muscle_invasion", "Oui"))
                ],
            }
        ],
    }


def parsed(record: dict | None = None):
    return load_inputs(json.dumps(record if record is not None else input_record()).encode())


class FakeEmbedder:
    model = MODEL
    dimensions = EMBEDDING_DIMENSIONS
    fingerprint = "fixture:synthetic-embedding:v1"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts]


class FakeReranker:
    model = "synthetic-reranker"
    fingerprint = "fixture:synthetic-reranker:v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def score(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        scores = []
        for raw in documents:
            text = raw[:MAX_DOCUMENT_CHARACTERS]
            key = text.split("|")[0]
            if query == SUPPORTIVE_QUESTION:
                score = {"s00": 0.99, "p00": 0.98, "s01": 0.97, "s02": 0.96}.get(key, 0.1)
            else:
                score = 0.99 - int(key[1:]) * 0.001 if key.startswith("p") else 0.1
            scores.append(score)
        return scores


async def seed(session: AsyncSession) -> None:
    session.add(
        EvidenceDocument(
            document_id="synthetic-doc",
            title="Synthetic fixture",
            clinical_status="poc_unreviewed",
            release_id="fixture-v1",
            version_label="synthetic",
        )
    )
    await session.flush()
    for key in [f"p{i:02}" for i in range(10)] + ["s00", "s01", "s02", "keyword-only"]:
        text = f"{key} TVIM Traitement cancer vessie muscle invasive bladder cancer."
        if key == "s02":
            text += " Table synthétique." * 300
        contextual = f"{key}|Heading not delivered|" + text
        if key == "s02":
            contextual += " context" * 1000
        session.add(
            EvidenceChunk(
                chunk_id=key,
                document_id="synthetic-doc",
                text=text,
                contextual_text=contextual,
                keyword_text=text,
                section_path=["Heading not delivered"],
                cancer_types=["bladder"],
                disease_setting="bladder_mibc_non_metastatic",
                therapy_line="not_applicable",
                clinical_purpose=["general"],
                agent_roles=["medical_oncology"],
                language="fr",
                clinical_status="poc_unreviewed",
                content_sha256="0" * 64,
                retrieval_eligible=True,
                page_start=7,
                citation_label="Synthetic table",
                unit_type="decision_table" if key == "s02" else None,
                embedding=(
                    [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) if key != "keyword-only" else None
                ),
                embedding_model=MODEL if key != "keyword-only" else None,
            )
        )
    await session.flush()


@pytest.fixture
async def runtime_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(sa.delete(EvidenceDocument))
            await seed(session)
            await session.commit()
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.execute(sa.delete(EvidenceDocument))
        await engine.dispose()


def label() -> Question:
    return Question(
        query_id="synthetic-query",
        split="pilot",
        question="Authored text is not a runtime question",
        cancer_type="bladder",
        concepts={"french": ("deliberately-not-the-runtime-concepts",)},
        filter={"disease_setting": "bladder_nmibc_low_risk", "agent_role": "medical_oncology"},
        relevant_groups=(EvidenceGroup("Synthetic claim", frozenset({"s02"})),),
        forbidden_chunk_ids=frozenset({"s01"}),
        corpus_release="deliberately-not-the-loaded-release",
    )


@pytest.mark.parametrize("operator", [None, "", "Question explicite de l'opérateur"])
async def test_tracing_preserves_production_calls_pack_and_notes(
    runtime_engine: AsyncEngine,
    operator: str | None,
) -> None:
    record = input_record()
    record["snapshots"][0]["operator_question"] = operator
    snapshot = parsed(record).snapshots[0]
    plain_embed, plain_rerank = FakeEmbedder(), FakeReranker()
    trace_embed, trace_rerank = FakeEmbedder(), FakeReranker()
    async with readonly_snapshot(runtime_engine) as session:
        passages, notes = await evidence.guideline_passages_for(
            session,
            cancer_type=snapshot.cancer_type,
            agent_role=snapshot.agent_role,
            entries=snapshot.entries,
            question=operator,
            embedder=plain_embed,
            reranker=plain_rerank,
        )
        measured = await evaluate_snapshot(
            session, snapshot, embedder=trace_embed, reranker=trace_rerank
        )
    assert measured["evidence_pack"] == {
        "passages": [p.model_dump(mode="json") for p in passages],
        "notes": notes,
    }
    assert plain_embed.calls == trace_embed.calls
    assert plain_rerank.calls == trace_rerank.calls
    assert plain_embed.calls[1] == [SUPPORTIVE_QUESTION]
    if operator:
        assert plain_embed.calls[0] == [operator]
    else:
        assert plain_embed.calls[0] == [
            question_for("bladder", "bladder_mibc_non_metastatic", "medical_oncology")
        ]
    traced = measured["trace"]
    assert traced["primary_ids"] == [f"p{i:02}" for i in range(10)]
    assert traced["supportive_ids"] == ["s00", "s01", "s02"]
    assert traced["supportive_duplicate_ids"] == ["p00"]
    assert traced["supportive"]["concepts"] == {"french": (), "english": (), "neutral": ()}
    assert traced["primary"]["filter"] == traced["supportive"]["filter"]
    assert traced["primary"]["filter"]["disease_setting"] == "bladder_mibc_non_metastatic"
    assert traced["primary"]["report"]["attempts"][0]["keyword_ids"]
    assert traced["primary"]["report"]["attempts"][0]["dense_ids"]
    assert traced["primary"]["report"]["attempts"][0]["fusion_scores"]
    assert len(passages) == 13
    long = next(p for p in passages if p.id == "s02")
    assert long.section is None and len(long.text) == MAX_TEXT
    assert long.text.endswith(evidence.TRUNCATION_MARKER)
    assert "Heading not delivered" not in long.text
    assert any("tronqués" in note for note in notes)
    conversion = next(p for p in traced["converted"] if p["id"] == "s02")
    assert conversion["clipped"]
    assert conversion["delivered_text"] == text_identity(long.text)
    call = measured["reranker_calls"][0]
    payload = call["documents"][call["candidate_ids"].index("s02")]
    assert payload["clipped"]
    assert payload["azure_payload_text"]["characters"] == MAX_DOCUMENT_CHARACTERS
    assert measured["scores"] is None and measured["scoring"] == "unscored"


async def test_opt_in_search_flags_and_no_collector_allocation(monkeypatch) -> None:
    calls = []

    async def search(session, **kwargs):
        calls.append(kwargs)
        from rcpia.evidence.retrieval import RetrievalReport

        return RetrievalReport()

    monkeypatch.setattr(evidence, "search", search)
    snapshot = parsed().snapshots[0]
    collector = evidence.EvidenceTrace()
    args = dict(
        cancer_type=snapshot.cancer_type,
        agent_role=snapshot.agent_role,
        entries=snapshot.entries,
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
    )
    await evidence.guideline_passages_for(None, **args)
    await evidence.guideline_passages_for(None, **args, trace=collector)
    assert [c.pop("trace") for c in calls] == [False, False, True, True]
    assert calls[:2] == calls[2:]
    assert collector.complete
    with pytest.raises(ValueError, match="single-use"):
        await evidence.guideline_passages_for(None, **args, trace=collector)


async def test_labelled_and_unlabelled_run_semantics(runtime_engine: AsyncEngine) -> None:
    async with readonly_snapshot(runtime_engine) as session:
        identity = await corpus_identity(session)
        record = input_record()
        record["expected_corpus_sha256"] = identity["sha256"]
        unlabelled = await evaluate_run(
            session, parsed(record), embedder=FakeEmbedder(), reranker=FakeReranker()
        )
        record["query_to_snapshot"] = {"synthetic-query": "synthetic-01"}
        labelled = await evaluate_run(
            session,
            parsed(record),
            embedder=FakeEmbedder(),
            reranker=FakeReranker(),
            goldset=Goldset((label(),)),
            split="pilot",
        )
    assert unlabelled["scores"] is None and unlabelled["scoring"] == "unscored"
    assert labelled["scores"]["primary"]["group_recall"] == 0
    assert labelled["scores"]["merged_pack"]["group_recall"] == 1
    assert labelled["scores"]["merged_pack"]["harmful_at_k"] == 1
    assert labelled["scores"]["merged_pack"]["precision_at_10"] == 0
    assert labelled["scores"]["merged_pack"]["ndcg_at_10"] == 0
    case = labelled["cases"][0]
    assert case["scores"]["merged_pack"]["returned"] == 13
    assert case["trace"]["primary"]["question"] != label().question
    assert labelled["corpus"]["release_ids"] == ["fixture-v1"]
    assert labelled["defaults"]["merged_pack_budget"] == 14
    assert labelled["evidence_packs_sha256"] == unlabelled["evidence_packs_sha256"]
    assert "No extraction" in labelled["boundary"]
    assert '"embedding":' not in canonical_json(labelled["corpus"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "Changed actual text, stale stored hash unchanged"),
        ("contextual_text", "New heading context"),
        ("keyword_text", "Different keywords"),
        ("agent_roles", ["urology_oncology"]),
        ("retrieval_eligible", False),
        ("embedding_model", "another-model"),
        ("embedding", [0.0, 1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2)),
        ("section_path", ["Changed section"]),
        ("page_start", 99),
    ],
)
async def test_corpus_digest_sensitive_to_actual_retrieval_data(
    session: AsyncSession,
    field: str,
    value: object,
) -> None:
    await seed(session)
    before = await corpus_identity(session)
    assert await corpus_identity(session) == before
    await session.execute(
        sa.update(EvidenceChunk).where(EvidenceChunk.chunk_id == "p00").values({field: value})
    )
    after = await corpus_identity(session)
    assert after["sha256"] != before["sha256"]


async def test_corpus_hash_ignores_incidental_ingestion_identity(session: AsyncSession) -> None:
    await seed(session)
    before = await corpus_identity(session)
    await session.execute(
        sa.update(EvidenceChunk).where(EvidenceChunk.chunk_id == "p00").values(id=uuid.uuid4())
    )
    await session.execute(
        sa.update(EvidenceDocument).values(ingested_at=sa.func.now() + sa.text("interval '1 hour'"))
    )
    assert await corpus_identity(session) == before
    await session.execute(sa.update(EvidenceDocument).values(version_label="changed"))
    assert (await corpus_identity(session))["sha256"] != before["sha256"]


async def test_readonly_repeatable_snapshot_excludes_concurrent_corpus_writes(
    runtime_engine: AsyncEngine,
) -> None:
    async with readonly_snapshot(runtime_engine) as session:
        before = await corpus_identity(session)
        async with runtime_engine.begin() as connection:
            await connection.execute(
                sa.update(EvidenceChunk)
                .where(EvidenceChunk.chunk_id == "p00")
                .values(text="A concurrent corpus update")
            )
        assert await corpus_identity(session) == before
    async with readonly_snapshot(runtime_engine) as session:
        assert await corpus_identity(session) != before


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(snapshots=[]),
        lambda r: r["snapshots"].append(copy.deepcopy(r["snapshots"][0])),
        lambda r: r["snapshots"][0].update(entries=[]),
        lambda r: r["snapshots"][0]["entries"].append(r["snapshots"][0]["entries"][0]),
        lambda r: r["snapshots"][0].update(ledger_version=True),
        lambda r: r["snapshots"][0].update(ledger_version=0),
        lambda r: r["snapshots"][0].update(agent_role="rcp_synthesis"),
        lambda r: r["snapshots"][0].update(agent_role="not-a-role"),
        lambda r: r["snapshots"][0].update(cancer_type="kidney"),
        lambda r: r["snapshots"][0].pop("operator_question"),
        lambda r: r["snapshots"][0].update(operator_question="   "),
        lambda r: r["snapshots"][0]["entries"][0].update(value=27),
        lambda r: r["snapshots"][0]["entries"][0].update(unknown="value"),
        lambda r: r["snapshots"][0]["entries"][0].update(status="incorrect"),
        lambda r: r["snapshots"][0]["entries"][0].update(highlight="red"),
        lambda r: r["snapshots"][0]["entries"][0].update(key="not-a-field"),
    ],
)
def test_invalid_frozen_inputs(mutation) -> None:
    record = input_record()
    mutation(record)
    with pytest.raises((ValueError, ValidationError)):
        parsed(record)


def test_ledger_validation_normalization_and_snapshot_hashes() -> None:
    record = input_record()
    record["snapshots"][0]["agent_role"] = "Oncologie médicale"
    record["snapshots"][0]["entries"][0]["readings"] = [
        {
            "value": "cT2 N0 M0",
            "source_chunk_ids": ["synthetic-source"],
        }
    ]
    snapshot = parsed(record).snapshots[0]
    assert snapshot.agent_role == "medical_oncology"
    assert snapshot.entries[0].readings[0].source_chunk_ids == ("synthetic-source",)
    assert snapshot.ledger_sha256 == payload_sha256([asdict(e) for e in snapshot.entries])
    assert parsed(record).snapshots[0].snapshot_sha256 == snapshot.snapshot_sha256
    record["snapshots"][0]["operator_question"] = "A new question"
    changed = parsed(record).snapshots[0]
    assert changed.snapshot_sha256 != snapshot.snapshot_sha256
    assert changed.ledger_sha256 == snapshot.ledger_sha256
    record["snapshots"][0]["entries"][0]["value"] = "cT3 N0 M0"
    assert parsed(record).snapshots[0].ledger_sha256 != snapshot.ledger_sha256
    with pytest.raises(ValueError, match="Duplicate JSON"):
        load_inputs(b'{"snapshots": [], "snapshots": []}')


@pytest.mark.parametrize(
    "mapping,split",
    [
        ({}, "pilot"),
        ({"other-query": "synthetic-01"}, "pilot"),
        ({"synthetic-query": "missing-snapshot"}, "pilot"),
        ({"synthetic-query": "synthetic-01"}, "test"),
        ({"synthetic-query": "synthetic-01"}, None),
    ],
)
def test_exact_label_mapping_and_nonempty_splits(mapping, split) -> None:
    record = input_record()
    record["query_to_snapshot"] = mapping
    with pytest.raises((ValueError, GoldsetError)):
        selected_labels(parsed(record), Goldset((label(),)), split)


@pytest.mark.parametrize("mismatch", ["digest", "dimension", "model", "relevant", "forbidden"])
async def test_corpus_preflight_rejects_before_providers(
    runtime_engine: AsyncEngine,
    mismatch: str,
) -> None:
    embeddings, reranking = FakeEmbedder(), FakeReranker()
    gold = label()
    async with readonly_snapshot(runtime_engine) as session:
        record = input_record()
        record["expected_corpus_sha256"] = (await corpus_identity(session))["sha256"]
        record["query_to_snapshot"] = {"synthetic-query": "synthetic-01"}
        if mismatch == "digest":
            record["expected_corpus_sha256"] = "f" * 64
        elif mismatch == "dimension":
            embeddings.dimensions = 3
        elif mismatch == "model":
            embeddings.model = "another-model"
        elif mismatch == "relevant":
            gold = replace(gold, relevant_groups=(EvidenceGroup("Stale", frozenset({"gone"})),))
        else:
            gold = replace(gold, forbidden_chunk_ids=frozenset({"gone"}))
        with pytest.raises((ValueError, GoldsetError)):
            await evaluate_run(
                session,
                parsed(record),
                embedder=embeddings,
                reranker=reranking,
                goldset=Goldset((gold,)),
                split="pilot",
            )
    assert embeddings.calls == [] and reranking.calls == []


@pytest.mark.parametrize(
    "failure",
    ["empty_embedding", "bad_dimension", "nan_score", "short_scores", "missing_trace", "exception"],
)
async def test_runtime_failures_never_become_empty_packs(
    runtime_engine: AsyncEngine,
    monkeypatch,
    failure: str,
) -> None:
    embeddings, reranking = FakeEmbedder(), FakeReranker()
    expected = (ValueError, EmbeddingError, RerankError, RuntimeError)
    if failure == "empty_embedding":
        monkeypatch.setattr(embeddings, "embed", AsyncMock(return_value=[]))
    elif failure == "bad_dimension":
        monkeypatch.setattr(embeddings, "embed", AsyncMock(return_value=[[1.0]]))
    elif failure == "nan_score":
        monkeypatch.setattr(reranking, "score", AsyncMock(return_value=[float("nan")] * 14))
    elif failure == "short_scores":
        monkeypatch.setattr(reranking, "score", AsyncMock(return_value=[]))
    elif failure == "missing_trace":
        monkeypatch.setattr(evidence, "guideline_passages_for", AsyncMock(return_value=([], [])))
    else:
        monkeypatch.setattr(reranking, "score", AsyncMock(side_effect=RuntimeError("failure")))
    async with readonly_snapshot(runtime_engine) as session:
        with pytest.raises(expected):
            await evaluate_snapshot(
                session, parsed().snapshots[0], embedder=embeddings, reranker=reranking
            )


def test_pack_hash_is_canonical_and_sensitive() -> None:
    assert payload_sha256({"a": 1, "b": 2}) == payload_sha256({"b": 2, "a": 1})
    assert payload_sha256({"notes": []}) != payload_sha256({"notes": ["warning"]})
    assert text_identity("é")["utf8_bytes"] == 2


async def test_source_output_and_input_preflight_before_settings(monkeypatch) -> None:
    # All artifacts in the owned project, not OS temporary directories.
    root = Path(__file__).resolve().parents[1]
    settings = AsyncMock(side_effect=AssertionError("settings must not load"))
    monkeypatch.setattr(runtime, "get_settings", settings)
    args = Namespace(
        artifacts_root=root,
        output=root / "never-created.json",
        source_commit="0" * 40,
        corpus_only=False,
        input=None,
        goldset=None,
        split=None,
    )
    with pytest.raises(ValueError, match="outside source"):
        await runtime.execute(args)
    assert not (root / "never-created.json").exists()
    settings.assert_not_called()


def test_source_requires_expected_clean_checkout(monkeypatch) -> None:
    import rcpia.evaluation.runtime_provenance as provenance

    def fake_git(command, **kwargs):
        if command[-1] == "HEAD":
            return "a" * 40
        return " M apps/api/rcpia/agents/evidence.py"

    monkeypatch.setattr(provenance.subprocess, "check_output", fake_git)
    with pytest.raises(ValueError, match="HEAD"):
        source_identity("b" * 40)
    with pytest.raises(ValueError, match="clean"):
        source_identity("a" * 40)


def test_output_existing_or_inside_git_is_rejected() -> None:
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError):
        check_output(root, Path(__file__))
    with pytest.raises(ValueError, match="Git checkout"):
        check_output(root, root / "unused.json", source=root / "unrelated-source")


@pytest.fixture
def artifact_directory():
    """Synthetic artifacts stay in this owned checkout, never an OS temporary directory."""
    root = Path(__file__).resolve().parents[1] / f".runtime-test-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def artifact_args(root: Path) -> Namespace:
    return Namespace(
        artifacts_root=root,
        output=root / "run.json",
        source_commit="a" * 40,
        corpus_only=False,
        input=root / "input.json",
        goldset=None,
        split=None,
    )


def allow_synthetic_artifacts(monkeypatch) -> None:
    # Production requires clean source and artifacts outside Git. These two gates are
    # separately tested; fixture output stays inside the owned checkout for cleanup.
    monkeypatch.setattr(runtime, "check_output", lambda root, output: output)
    monkeypatch.setattr(runtime, "source_identity", lambda commit: {"fixture_source": commit})


async def test_cli_persists_deterministic_manifest_and_never_overwrites(
    runtime_engine: AsyncEngine,
    artifact_directory: Path,
    monkeypatch,
) -> None:
    allow_synthetic_artifacts(monkeypatch)
    root = artifact_directory
    args = artifact_args(root)
    async with readonly_snapshot(runtime_engine) as session:
        record = input_record()
        record["expected_corpus_sha256"] = (await corpus_identity(session))["sha256"]
    args.input.write_text(canonical_json(record), encoding="utf-8")
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=runtime_engine.url.render_as_string(hide_password=False),
            secret="DO-NOT-SERIALIZE-SETTINGS",
        ),
    )
    monkeypatch.setattr(runtime, "build_embedder", lambda settings: FakeEmbedder())
    monkeypatch.setattr(runtime, "build_reranker", lambda settings: FakeReranker())
    assert await runtime.execute(args) == args.output
    first = args.output.read_bytes()
    result = json.loads(first)
    digest = result.pop("manifest_sha256")
    assert digest == payload_sha256(result)
    assert result["status"] == "complete" and result["cases"]
    assert b"DO-NOT-SERIALIZE" not in first
    with pytest.raises(FileExistsError):
        await runtime.execute(args)
    assert args.output.read_bytes() == first
    args.output = root / "second-run.json"
    await runtime.execute(args)
    assert args.output.read_bytes() == first


@pytest.mark.parametrize("invalid", ["schema", "mapping", "split", "outside-input"])
async def test_cli_input_failures_precede_settings_and_provider_construction(
    artifact_directory: Path,
    monkeypatch,
    invalid: str,
) -> None:
    allow_synthetic_artifacts(monkeypatch)
    args = artifact_args(artifact_directory)
    record = input_record()
    if invalid == "schema":
        record["snapshots"] = []
    elif invalid == "mapping":
        record["query_to_snapshot"] = {"unscored-query": "synthetic-01"}
    elif invalid == "split":
        args.split = "test"
    args.input.write_text(canonical_json(record), encoding="utf-8")
    if invalid == "outside-input":
        args.input = Path(__file__)
    settings = AsyncMock(side_effect=AssertionError("must not access settings"))
    monkeypatch.setattr(runtime, "get_settings", settings)
    with pytest.raises(ValueError):
        await runtime.execute(args)
    settings.assert_not_called()
    assert not args.output.exists()


async def test_cli_provider_failure_leaves_only_incomplete_artifact(
    runtime_engine: AsyncEngine,
    artifact_directory: Path,
    monkeypatch,
) -> None:
    allow_synthetic_artifacts(monkeypatch)
    args = artifact_args(artifact_directory)
    async with readonly_snapshot(runtime_engine) as session:
        record = input_record()
        record["expected_corpus_sha256"] = (await corpus_identity(session))["sha256"]
    args.input.write_text(canonical_json(record), encoding="utf-8")
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=runtime_engine.url.render_as_string(hide_password=False)
        ),
    )
    bad = FakeEmbedder()
    monkeypatch.setattr(bad, "embed", AsyncMock(side_effect=EmbeddingError("failure")))
    monkeypatch.setattr(runtime, "build_embedder", lambda settings: bad)
    monkeypatch.setattr(runtime, "build_reranker", lambda settings: FakeReranker())
    with pytest.raises(EmbeddingError):
        await runtime.execute(args)
    assert json.loads(args.output.read_text()) == {
        "schema_version": runtime.SCHEMA_VERSION,
        "status": "incomplete",
    }


async def test_valid_empty_pack_records_attempts_and_correct_refusal(
    runtime_engine: AsyncEngine,
    monkeypatch,
) -> None:
    reranker = FakeReranker()

    async def below_floor(query, documents):
        return [0.1] * len(documents)

    monkeypatch.setattr(reranker, "score", below_floor)
    gold = replace(label(), relevant_groups=(), unanswerable=True)
    async with readonly_snapshot(runtime_engine) as session:
        record = input_record()
        record["expected_corpus_sha256"] = (await corpus_identity(session))["sha256"]
        record["query_to_snapshot"] = {"synthetic-query": "synthetic-01"}
        result = await evaluate_run(
            session,
            parsed(record),
            embedder=FakeEmbedder(),
            reranker=reranker,
            goldset=Goldset((gold,)),
            split="pilot",
        )
    assert result["cases"][0]["evidence_pack"]["passages"] == []
    assert result["cases"][0]["trace"]["primary"]["report"]["attempts"][0]["below_floor_ids"]
    assert result["scores"]["primary"]["correct_refusal"] == 1
    assert result["scores"]["merged_pack"]["correct_refusal"] == 1


async def test_non_snapshot_session_refused_before_provider_work(session: AsyncSession) -> None:
    embeddings, reranking = FakeEmbedder(), FakeReranker()
    with pytest.raises(ValueError, match="read-only"):
        await evaluate_run(session, parsed(), embedder=embeddings, reranker=reranking)
    assert embeddings.calls == [] and reranking.calls == []


async def test_shared_runner_hashes_actual_conversion_not_discarded_headings(
    session: AsyncSession,
) -> None:
    await seed(session)
    snapshot = parsed().snapshots[0]

    async def measured():
        return await evaluate_snapshot(
            session, snapshot, embedder=FakeEmbedder(), reranker=FakeReranker()
        )

    before = await measured()
    assert (await measured())["trace"]["evidence_pack_sha256"] == (
        before["trace"]["evidence_pack_sha256"]
    )
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "p00")
        .values(contextual_text="p00|A changed heading, not delivered")
    )
    headings = await measured()
    assert headings["trace"]["evidence_pack_sha256"] == before["trace"]["evidence_pack_sha256"]
    assert headings["reranker_calls"] != before["reranker_calls"]
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "s02")
        .values(text=EvidenceChunk.text + " Changed clipped tail")
    )
    tail = await measured()
    assert tail["trace"]["evidence_pack_sha256"] == headings["trace"]["evidence_pack_sha256"]
    assert tail["trace"]["converted"] != headings["trace"]["converted"]
    await session.execute(
        sa.update(EvidenceChunk)
        .where(EvidenceChunk.chunk_id == "p00")
        .values(text="Changed delivered raw text")
    )
    changed = await measured()
    assert changed["trace"]["evidence_pack_sha256"] != before["trace"]["evidence_pack_sha256"]


async def test_traced_disabled_supportive_search_preserves_primary_contract(
    runtime_engine: AsyncEngine,
) -> None:
    snapshot = parsed().snapshots[0]
    trace = evidence.EvidenceTrace()
    embeddings, reranking = FakeEmbedder(), FakeReranker()
    async with readonly_snapshot(runtime_engine) as session:
        passages, notes = await evidence.guideline_passages_for(
            session,
            cancer_type=snapshot.cancer_type,
            agent_role=snapshot.agent_role,
            entries=snapshot.entries,
            embedder=embeddings,
            reranker=reranking,
            supportive_limit=0,
            trace=trace,
        )
    assert trace.complete and trace.supportive is None
    assert len(embeddings.calls) == 1
    assert [p.id for p in passages] == trace.primary_ids
    assert not trace.supportive_ids and not trace.supportive_duplicate_ids
    assert notes == trace.notes


@pytest.fixture
def main_arguments(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime.sys,
        "argv",
        [
            "runtime",
            "--source-commit",
            "a" * 40,
            "--artifacts-root",
            "synthetic-artifacts",
            "--output",
            "synthetic-artifacts\\run.json",
            "--corpus-only",
        ],
    )


@pytest.mark.parametrize(
    "error",
    [
        ValueError("sensitive patient input"),
        ValidationError.from_exception_data(
            "FrozenInput",
            [
                {
                    "type": "int_type",
                    "loc": ("ledger_version",),
                    "input": "sensitive patient input",
                },
            ],
        ),
        json.JSONDecodeError("sensitive patient input", "sensitive patient input", 0),
        UnicodeDecodeError("utf-8", b"sensitive patient input", 0, 1, "sensitive patient input"),
        GoldsetError("sensitive patient input"),
        CorpusError("sensitive patient input"),
        FileNotFoundError("sensitive patient input"),
        PermissionError("sensitive patient input"),
        subprocess.CalledProcessError(
            1,
            ["git", "sensitive patient input"],
            output="sensitive patient input",
            stderr="sensitive patient input",
        ),
        subprocess.TimeoutExpired(
            ["git", "sensitive patient input"],
            1,
            output="sensitive patient input",
            stderr="sensitive patient input",
        ),
        sa.exc.OperationalError(
            "sensitive patient input", {}, Exception("sensitive patient input")
        ),
        httpx.ConnectError("sensitive patient input"),
        httpx.ReadTimeout("sensitive patient input"),
        EmbeddingError("sensitive patient input"),
        RerankError("sensitive patient input"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_main_sanitizes_expected_failures_and_exits_nonzero(
    main_arguments,
    monkeypatch,
    capsys,
    error: Exception,
) -> None:
    execute = AsyncMock(side_effect=error)
    monkeypatch.setattr(runtime, "execute", execute)
    with pytest.raises(SystemExit) as failure:
        runtime.main()
    assert failure.value.code == 1
    execute.assert_awaited_once()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"Runtime evidence run failed ({type(error).__name__}); no complete baseline written.\n"
    )
    assert "sensitive patient input" not in captured.err


@pytest.mark.parametrize(
    "error",
    [
        TypeError("unexpected programming failure"),
        KeyError("unexpected programming failure"),
        AssertionError("unexpected programming failure"),
        RuntimeError("unexpected programming failure"),
    ],
)
def test_main_does_not_swallow_unexpected_programming_failures(
    main_arguments,
    monkeypatch,
    capsys,
    error: Exception,
) -> None:
    execute = AsyncMock(side_effect=error)
    monkeypatch.setattr(runtime, "execute", execute)
    with pytest.raises(type(error)) as failure:
        runtime.main()
    assert failure.value is error
    execute.assert_awaited_once()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
