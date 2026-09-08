"""Actual-runtime evidence selection, deliberately not generation or full-bundle evaluation."""

import argparse
import asyncio
import hashlib
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from subprocess import SubprocessError
from typing import Any

import sqlalchemy as sa
from httpx import HTTPError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from rcpia.agents import evidence
from rcpia.agents.schemas import MAX_TEXT
from rcpia.core.config import get_settings
from rcpia.evaluation.goldset import (
    Goldset,
    GoldsetError,
    Question,
    parse_goldset,
    verify_against_corpus,
)
from rcpia.evaluation.metrics import aggregate, score_question
from rcpia.evaluation.runtime_inputs import RuntimeInput, RuntimeSnapshot, selected_labels
from rcpia.evaluation.runtime_provenance import (
    check_output,
    corpus_identity,
    readonly_snapshot,
    restricted_input,
    source_identity,
)
from rcpia.evidence.embedding import Embedder, EmbeddingError, build_embedder
from rcpia.evidence.hashing import canonical_json, payload_sha256, text_identity
from rcpia.evidence.ingestion import CorpusError
from rcpia.evidence.models import EMBEDDING_DIMENSIONS, EvidenceChunk
from rcpia.evidence.rerank import (
    MAX_DOCUMENT_CHARACTERS,
    Reranker,
    RerankError,
    build_reranker,
)
from rcpia.evidence.retrieval import CANDIDATE_LIMIT, RELEVANCE_FLOOR, RRF_K

SCHEMA_VERSION = "rcpia.runtime-evidence.v1"
BOUNDARY = (
    "Evidence selection and delivery only, through guideline_passages_for. "
    "No extraction, full TaskBundle size gate, Pi execution, generation, synthesis, "
    "clinical efficacy, or new clinical score is evaluated."
)
DEFAULTS = {
    "primary_limit": evidence.PASSAGE_LIMIT,
    "supportive_limit": evidence.SUPPORTIVE_PASSAGE_LIMIT,
    "merged_pack_budget": evidence.PASSAGE_LIMIT + evidence.SUPPORTIVE_PASSAGE_LIMIT,
    "candidate_limit": CANDIDATE_LIMIT,
    "rrf_k": RRF_K,
    "relevance_floor": RELEVANCE_FLOOR,
    "passage_text_cap": MAX_TEXT,
    "reranker_document_cap": MAX_DOCUMENT_CHARACTERS,
    "conversion": "raw text with truncation marker; section=None; no heading repair",
    "safety_engine": "not supplied by guideline_passages_for",
}


def load_inputs(raw: bytes) -> RuntimeInput:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON input key.")
            result[key] = value
        return result

    decoded = json.loads(raw, object_pairs_hook=unique)
    return RuntimeInput.model_validate(decoded)


class CheckedEmbedder:
    """Validate complete provider results without changing inputs or successful vectors."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.model = inner.model
        self.dimensions = inner.dimensions
        self.calls: list[dict[str, object]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await self.inner.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError("Runtime baseline received an incomplete embedding response.")
        for vector in vectors:
            if (
                len(vector) != self.dimensions
                or any(
                    isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
                    for v in vector
                )
                or not any(vector)
            ):
                raise EmbeddingError("Runtime baseline received an invalid embedding vector.")
        self.calls.append(
            {
                "texts": texts,
                "vectors_sha256": payload_sha256(vectors),
                "dimensions": self.dimensions,
            }
        )
        return vectors


class CheckedReranker:
    """Record the production Azure clipping contract without modifying the call."""

    def __init__(self, inner: Reranker) -> None:
        self.inner = inner
        self.model = inner.model
        self.fingerprint = inner.fingerprint
        self.calls: list[dict[str, object]] = []

    async def score(self, query: str, documents: list[str]) -> list[float]:
        scores = await self.inner.score(query, documents)
        if len(scores) != len(documents) or any(
            isinstance(s, bool) or not isinstance(s, int | float) or not math.isfinite(s)
            for s in scores
        ):
            raise RerankError("Runtime baseline received incomplete/invalid rerank scores.")
        self.calls.append(
            {
                "question": query,
                "documents": [
                    {
                        "contextual_text": text_identity(text),
                        "azure_payload_text": text_identity(text[:MAX_DOCUMENT_CHARACTERS]),
                        "clipped": len(text) > MAX_DOCUMENT_CHARACTERS,
                    }
                    for text in documents
                ],
                "scores": scores,
            }
        )
        return scores


async def evaluate_snapshot(
    session: AsyncSession,
    snapshot: RuntimeSnapshot,
    *,
    embedder: Embedder,
    reranker: Reranker,
    label: Question | None = None,
) -> dict[str, Any]:
    """The shared runner/test seam calls the exact function used by build_bundle."""
    traced = evidence.EvidenceTrace()
    embeddings, reranking = CheckedEmbedder(embedder), CheckedReranker(reranker)
    passages, notes = await evidence.guideline_passages_for(
        session,
        cancer_type=snapshot.cancer_type,
        agent_role=snapshot.agent_role,
        entries=snapshot.entries,
        question=snapshot.operator_question,
        embedder=embeddings,
        reranker=reranking,
        trace=traced,
    )
    if not traced.complete or traced.primary is None or traced.supportive is None:
        raise ValueError("Missing/incomplete production evidence trace.")
    searches = [traced.primary, traced.supportive]
    for search in searches:
        if not search.report.attempts:
            raise ValueError("Missing production retrieval attempts.")
        for attempt in search.report.attempts:
            if set(attempt.candidate_ids) != set(attempt.relevances):
                raise ValueError("Incomplete rerank attempt trace.")
    attempts = [
        attempt
        for search in searches
        for attempt in search.report.attempts
        if attempt.candidate_ids
    ]
    if len(attempts) != len(reranking.calls):
        raise ValueError("Reranker calls do not match recorded retrieval attempts.")
    for attempt, call in zip(attempts, reranking.calls, strict=True):
        call["candidate_ids"] = attempt.candidate_ids
    final_ids = [p.id for p in passages]
    if final_ids != traced.primary_ids + traced.supportive_ids or len(final_ids) != len(
        set(final_ids)
    ):
        raise ValueError("Trace and converted evidence pack disagree.")
    pack = {"passages": [p.model_dump(mode="json") for p in passages], "notes": notes}
    if traced.evidence_pack_sha256 != payload_sha256(pack):
        raise ValueError("Evidence pack hash mismatch.")
    scores = None
    if label is not None:
        scores = {
            "primary": asdict(score_question(label, traced.primary_ids)),
            "merged_pack": asdict(score_question(label, final_ids)),
        }
    return {
        "snapshot_id": snapshot.snapshot_id,
        "case_id": snapshot.case_id,
        "query_id": label.query_id if label else None,
        "ledger_version": snapshot.ledger_version,
        "ledger_sha256": snapshot.ledger_sha256,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "cancer_type": snapshot.cancer_type,
        "agent_role": snapshot.agent_role,
        "operator_question": snapshot.operator_question,
        "question_state": (
            "operator"
            if snapshot.operator_question
            else "derived_empty"
            if snapshot.operator_question == ""
            else "derived_null"
        ),
        "scoring": "labelled" if label is not None else "unscored",
        "scores": scores,
        "trace": asdict(traced),
        "evidence_pack": pack,
        "embedding_calls": embeddings.calls,
        "reranker_calls": reranking.calls,
    }


async def preflight_corpus(
    session: AsyncSession,
    inputs: RuntimeInput,
    labels: dict[str, Question],
    corpus: dict[str, Any],
    *,
    embedder: Embedder,
) -> None:
    if inputs.expected_corpus_sha256 != corpus["sha256"]:
        raise ValueError("Actual loaded corpus does not match expected_corpus_sha256.")
    if embedder.dimensions != EMBEDDING_DIMENSIONS or not embedder.model.strip():
        raise ValueError("Embedding dimensions/model do not match retrieval storage.")
    inventory = corpus["inventory"]
    embedded = [row for row in inventory if row["embedded"] and row["retrieval_eligible"]]
    if not embedded or any(
        row["embedding_model"] != embedder.model or row["dimensions"] != embedder.dimensions
        for row in embedded
    ):
        raise ValueError("Loaded eligible vectors do not match the runtime embedding model.")
    if labels:
        present = frozenset((await session.execute(sa.select(EvidenceChunk.chunk_id))).scalars())
        verify_against_corpus(Goldset(questions=tuple(labels.values())), present)


async def evaluate_run(
    session: AsyncSession,
    inputs: RuntimeInput,
    *,
    embedder: Embedder,
    reranker: Reranker,
    goldset: Goldset | None = None,
    split: str | None = None,
) -> dict[str, Any]:
    labels = selected_labels(inputs, goldset, split)
    isolation = (await session.execute(sa.text("SHOW transaction_isolation"))).scalar_one()
    readonly = (await session.execute(sa.text("SHOW transaction_read_only"))).scalar_one()
    if isolation != "repeatable read" or readonly != "on":
        raise ValueError("Runtime runs require a read-only REPEATABLE READ snapshot.")
    corpus = await corpus_identity(session)
    await preflight_corpus(session, inputs, labels, corpus, embedder=embedder)
    embedding_fingerprint = getattr(embedder, "fingerprint", None)
    if (
        not isinstance(embedding_fingerprint, str)
        or not embedding_fingerprint.strip()
        or not reranker.model.strip()
        or not reranker.fingerprint.strip()
    ):
        raise ValueError("Explicit retriever fingerprints are required.")
    cases = [
        await evaluate_snapshot(
            session,
            snapshot,
            embedder=embedder,
            reranker=reranker,
            label=labels.get(snapshot.snapshot_id),
        )
        for snapshot in inputs.snapshots
    ]
    # The MVCC view freezes row contents; this also catches observable config drift.
    if await corpus_identity(session) != corpus:
        raise ValueError("Corpus/search environment changed during the runtime run.")
    scores = None
    if labels:
        scores = {}
        for name, ids in (("primary", "primary_ids"), ("merged_pack", None)):
            scores[name] = asdict(
                aggregate(
                    name,
                    [
                        score_question(
                            labels[case["snapshot_id"]],
                            case["trace"][ids]
                            if ids
                            else [p["id"] for p in case["evidence_pack"]["passages"]],
                        )
                        for case in cases
                    ],
                )
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "boundary": BOUNDARY,
        "status": "complete",
        "mode": "actual-runtime-evidence",
        "scoring": "labelled" if labels else "unscored",
        "split": split,
        "defaults": DEFAULTS,
        "corpus": corpus,
        "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
        "query_to_snapshot": inputs.query_to_snapshot,
        "models": {
            "embedding": {
                "model_or_deployment": embedder.model,
                "fingerprint": embedding_fingerprint,
                "dimensions": embedder.dimensions,
            },
            "reranking": {
                "model_or_deployment": reranker.model,
                "fingerprint": reranker.fingerprint,
            },
            "weight_revision": "not exposed or frozen by the provider API",
            "reranker_payload_contract": "AzureReranker (fakes must implement the same cap)",
        },
        "runtime": {
            "entry_point": "rcpia.agents.evidence.guideline_passages_for",
            "role_prompts": "source-file identities only; not loaded or executed",
            "pi": "not executed; no generation runtime/version claim",
        },
        "score_semantics": {
            "primary_budget": evidence.PASSAGE_LIMIT,
            "merged_pack_budget": evidence.PASSAGE_LIMIT + evidence.SUPPORTIVE_PASSAGE_LIMIT,
            "coverage_and_harmful_at_k": "entire named pack",
            "precision_at_10_and_ndcg_at_10": "first ten only, including in merged_pack",
            "gold_question_and_filter": "label provenance only; never used to build patient inputs",
            "supportive": "no independent clinical labels; merged order is not joint reranking",
        },
        "scores": scores,
        "cases": cases,
        "evidence_packs_sha256": payload_sha256(
            [[c["snapshot_id"], c["trace"]["evidence_pack_sha256"]] for c in cases]
        ),
    }


async def execute(args: argparse.Namespace) -> Path:
    output = check_output(args.artifacts_root, args.output)
    source = source_identity(args.source_commit)
    if args.corpus_only:
        if args.input is not None or args.goldset is not None or args.split is not None:
            raise ValueError("--corpus-only cannot be combined with run/label inputs.")
        inputs, raw, goldset, goldset_raw = None, None, None, None
    else:
        if args.input is None:
            raise ValueError("Runtime evaluation requires --input.")
        raw = restricted_input(args.input, args.artifacts_root)
        inputs = load_inputs(raw)
        goldset_raw = args.goldset.read_bytes() if args.goldset else None
        goldset = (
            parse_goldset(goldset_raw.decode("utf-8").splitlines())
            if goldset_raw is not None
            else None
        )
        selected_labels(inputs, goldset, args.split)
    goldset_hash = hashlib.sha256(goldset_raw).hexdigest() if goldset_raw is not None else None
    # Reserve before settings, DB, or providers. A failure leaves an explicit incomplete run.
    with output.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json({"schema_version": SCHEMA_VERSION, "status": "incomplete"}))
        handle.flush()
        settings = get_settings()
        engine = create_async_engine(settings.database_url)
        try:
            async with readonly_snapshot(engine) as session:
                result: dict[str, Any]
                if inputs is None:
                    result = {
                        "schema_version": SCHEMA_VERSION,
                        "status": "complete",
                        "mode": "corpus-inspection",
                        "boundary": BOUNDARY,
                        "scoring": "unscored",
                        "corpus": await corpus_identity(session),
                    }
                else:
                    result = await evaluate_run(
                        session,
                        inputs,
                        embedder=build_embedder(settings),
                        reranker=build_reranker(settings),
                        goldset=goldset,
                        split=args.split,
                    )
        finally:
            await engine.dispose()
        if source_identity(args.source_commit) != source:
            raise ValueError("Source checkout changed during evaluation.")
        result["source"] = source
        result["input_file_sha256"] = hashlib.sha256(raw).hexdigest() if raw else None
        result["goldset_file_sha256"] = goldset_hash
        result["manifest_sha256"] = payload_sha256(result)
        handle.seek(0)
        handle.write(canonical_json(result) + "\n")
        handle.truncate()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--goldset", type=Path)
    parser.add_argument("--split", choices=("pilot", "dev", "test"))
    parser.add_argument("--source-commit", required=True, help="Exact full clean checkout HEAD.")
    parser.add_argument(
        "--artifacts-root",
        required=True,
        type=Path,
        help="Existing restricted directory outside Git; caller manages ACLs.",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="New JSON file; never overwritten."
    )
    parser.add_argument(
        "--corpus-only", action="store_true", help="Inspect identity without providers."
    )
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Exceptions remain failures, never synthesized empty packs. Do not print patient traces.
    try:
        output = asyncio.run(execute(args))
    except (
        ValueError,
        OSError,
        SubprocessError,
        SQLAlchemyError,
        GoldsetError,
        CorpusError,
        EmbeddingError,
        RerankError,
        HTTPError,
    ) as exc:
        # ValueError includes Pydantic, JSON and Unicode input validation failures.
        # Expected failures may contain patient text or credentials; programming bugs propagate.
        print(
            f"Runtime evidence run failed ({type(exc).__name__}); no complete baseline written.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(f"Evidence artifact written: {output}")


if __name__ == "__main__":
    main()
