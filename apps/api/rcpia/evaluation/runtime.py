"""Actual-runtime evidence selection, deliberately not generation or full-bundle evaluation."""

import argparse
import asyncio
import hashlib
import math
import sys
from dataclasses import asdict
from pathlib import Path
from subprocess import SubprocessError
from typing import Any, Literal

import sqlalchemy as sa
from httpx import HTTPError
from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from rcpia.agents import evidence
from rcpia.core.config import get_settings
from rcpia.core.jsonio import load_json
from rcpia.evaluation.approval import (
    ApprovalRegistry,
    ScoringPermit,
    SplitManifest,
    authorize,
    require_question_approval,
    verify_permit_corpus,
)
from rcpia.evaluation.goldset import (
    Goldset,
    GoldsetError,
    Question,
    parse_goldset,
    verify_against_corpus,
)
from rcpia.evaluation.metrics import aggregate, score_question
from rcpia.evaluation.review import (
    CandidatePool,
    PoolCase,
    ReviewDraft,
    verify_review_sources,
    write_new,
)
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
from rcpia.evidence.payload import MAX_EVIDENCE_CHARACTERS, prepare_evidence
from rcpia.evidence.rerank import (
    MAX_DOCUMENT_CHARACTERS,
    Reranker,
    RerankError,
    build_reranker,
)
from rcpia.evidence.retrieval import CANDIDATE_LIMIT, RELEVANCE_FLOOR, RRF_K
from rcpia.query.decision import (
    FACT_PROJECTION_CONTRACT,
    RETRIEVAL_CONTRACT,
    SETTING_DERIVATION_CONTRACT,
)

SCHEMA_VERSION = "rcpia.runtime-evidence.v3"
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
    "passage_text_cap": MAX_EVIDENCE_CHARACTERS,
    "reranker_document_cap": MAX_DOCUMENT_CHARACTERS,
    "conversion": (
        "whole stored contextual unit and section path; oversized-only Markdown cell-padding "
        "compaction; refuse if still oversized; no clipping or missing-context reconstruction"
    ),
    "safety_engine": "not supplied by guideline_passages_for",
}


def load_inputs(raw: bytes) -> RuntimeInput:
    return RuntimeInput.model_validate(load_json(raw))


QueryMode = Literal["baseline", "decision_targeted_v1"]


def preflight_queries(inputs: RuntimeInput, query_mode: QueryMode) -> None:
    if query_mode not in {"baseline", "decision_targeted_v1"}:
        raise ValueError("Unknown retrieval query mode.")
    inputs.validate_snapshot_consistency()
    for snapshot in inputs.snapshots:
        snapshot.validate_patient_context_binding()
    if query_mode == "decision_targeted_v1" and any(
        s.retrieval_query is None for s in inputs.snapshots
    ):
        raise ValueError("Every targeted snapshot requires a pinned decision context.")


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
    """Record the same preparation used by Azure, never a separate tracing approximation."""

    def __init__(self, inner: Reranker) -> None:
        self.inner = inner
        self.model = inner.model
        self.fingerprint = inner.fingerprint
        self.calls: list[dict[str, object]] = []

    async def score(self, query: str, documents: list[str]) -> list[float]:
        prepared = [prepare_evidence(text) for text in documents]
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
                        "azure_payload_text": text_identity(item.text),
                        "transformation": item.transformation,
                        "clipped": False,
                    }
                    for text, item in zip(documents, prepared, strict=True)
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
    query_mode: QueryMode = "baseline",
    permit: ScoringPermit | None = None,
) -> dict[str, Any]:
    """The shared runner/test seam calls the exact function used by build_bundle."""
    snapshot.validate_patient_context_binding()
    if query_mode not in {"baseline", "decision_targeted_v1"}:
        raise ValueError("Unknown retrieval query mode.")
    if query_mode == "decision_targeted_v1" and snapshot.retrieval_query is None:
        raise ValueError("Targeted retrieval requires a pinned decision context.")
    if label is not None:
        require_question_approval(label, permit, snapshot)
        if permit is not None:
            await verify_permit_corpus(session, permit)
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
        retrieval_query=snapshot.retrieval_query if query_mode == "decision_targeted_v1" else None,
        patient_context=snapshot.patient_context,
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
        "patient_context_state": "provided" if snapshot.patient_context is not None else "absent",
        "patient_context_contract": (
            snapshot.patient_context.contract if snapshot.patient_context is not None else None
        ),
        "patient_context_sha256": snapshot.patient_context_sha256,
        "patient_context_ledger_sha256": (
            snapshot.patient_context.ledger_sha256 if snapshot.patient_context is not None else None
        ),
        "cancer_type": snapshot.cancer_type,
        "agent_role": snapshot.agent_role,
        "operator_question": snapshot.operator_question,
        "question_state": (
            "operator"
            if snapshot.operator_question
            else "decision_targeted"
            if query_mode == "decision_targeted_v1"
            else "derived_empty"
            if snapshot.operator_question == ""
            else "derived_null"
        ),
        "query_mode": query_mode,
        "retrieval_contract": RETRIEVAL_CONTRACT,
        "fact_projection_contract": FACT_PROJECTION_CONTRACT,
        "setting_derivation_contract": SETTING_DERIVATION_CONTRACT,
        "scoring": (
            "approved_labels" if permit else "legacy_pilot_diagnostic"
        ) if label is not None else "unscored",
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
    query_mode: QueryMode = "baseline",
    manifest: SplitManifest | None = None,
    registry: ApprovalRegistry | None = None,
    review_pack: ReviewDraft | None = None,
) -> dict[str, Any]:
    preflight_queries(inputs, query_mode)
    labels = selected_labels(inputs, goldset, split)
    permit = None
    if goldset is not None and split is not None:
        permit = authorize(
            goldset=goldset, inputs=inputs, split=split, manifest=manifest,
            registry=registry, review_pack=review_pack,
        )
    elif manifest is not None or registry is not None or review_pack is not None:
        raise ValueError("Scoring governance files require labels and an explicit split.")
    isolation = (await session.execute(sa.text("SHOW transaction_isolation"))).scalar_one()
    readonly = (await session.execute(sa.text("SHOW transaction_read_only"))).scalar_one()
    if isolation != "repeatable read" or readonly != "on":
        raise ValueError("Runtime runs require a read-only REPEATABLE READ snapshot.")
    corpus = await corpus_identity(session)
    await preflight_corpus(session, inputs, labels, corpus, embedder=embedder)
    if review_pack is not None:
        await verify_review_sources(session, review_pack)
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
            query_mode=query_mode, permit=permit,
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
        "query_mode": query_mode,
        "retrieval_contract": RETRIEVAL_CONTRACT,
        "fact_projection_contract": FACT_PROJECTION_CONTRACT,
        "setting_derivation_contract": SETTING_DERIVATION_CONTRACT,
        "runtime_input_schema": inputs.schema_version,
        "scoring": (
            "approved_labels" if permit else "legacy_pilot_diagnostic"
        ) if labels else "unscored",
        "review": {
            "approval": permit.approval.model_dump(mode="json") if permit else None,
            "split_manifest_sha256": manifest.sha256 if manifest else None,
            "trust_boundary": "independent operator-supplied attestation; not IAM verification",
        },
        "split": split,
        "defaults": DEFAULTS,
        "corpus": corpus,
        "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
        "clinical_inputs_sha256": inputs.clinical_inputs_sha256,
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
            "reranker_payload_contract": (
                "AzureReranker uses prepare_evidence; fakes must implement the same "
                "whole-unit preparation and refusal, never clipping"
            ),
        },
        "runtime": {
            "entry_point": "rcpia.agents.evidence.guideline_passages_for",
            "role_prompts": "source-file identities only; not loaded or executed",
            "pi": "not executed; no generation runtime/version claim",
            "patient_context": (
                "passed as supplied; case/version/ledger binding validated; absent context is "
                "not reconstructed; source-unit authenticity and clinical approval not attested"
            ),
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
    pool_output = (
        check_output(args.artifacts_root, args.pool_output)
        if getattr(args, "pool_output", None) is not None else None
    )
    if pool_output == output:
        raise ValueError("Pool export must have a distinct output path.")
    source = source_identity(args.source_commit)
    query_mode = TypeAdapter[QueryMode](QueryMode).validate_python(
        getattr(args, "query_mode", "baseline"), strict=True,
    )
    manifest = (
        SplitManifest.model_validate(load_json(
            restricted_input(args.split_manifest, args.artifacts_root),
        ))
        if getattr(args, "split_manifest", None) else None
    )
    registry = (
        ApprovalRegistry.model_validate(load_json(args.approvals.read_bytes()))
        if getattr(args, "approvals", None) else None
    )
    review_pack = (
        ReviewDraft.model_validate(load_json(
            restricted_input(args.review_pack, args.artifacts_root),
        ))
        if getattr(args, "review_pack", None) else None
    )
    if args.corpus_only:
        if args.input is not None or args.goldset is not None or args.split is not None:
            raise ValueError("--corpus-only cannot be combined with run/label inputs.")
        if (
            query_mode != "baseline" or manifest or registry or review_pack or pool_output
        ):
            raise ValueError("--corpus-only cannot use query/review/pool options.")
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
        preflight_queries(inputs, query_mode)
        if goldset is not None:
            authorize(
                goldset=goldset, inputs=inputs, split=args.split, manifest=manifest,
                registry=registry, review_pack=review_pack,
            )
        elif manifest or registry or review_pack:
            raise ValueError("Review/approval options require labels.")
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
                        query_mode=query_mode, manifest=manifest,
                        registry=registry, review_pack=review_pack,
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
    if pool_output is not None:
        pool = CandidatePool(
            schema_version="rcpia.review-pool.v1",
            runtime_artifact_sha256=result["manifest_sha256"],
            source_identity_sha256=payload_sha256(source),
            corpus_sha256=result["corpus"]["sha256"], input_sha256=result["input_sha256"],
            clinical_inputs_sha256=result["clinical_inputs_sha256"],
            cases=[
                PoolCase(
                    snapshot_id=case["snapshot_id"], snapshot_sha256=case["snapshot_sha256"],
                    candidate_ids=sorted({
                        cid for channel in ("primary", "supportive")
                        for attempt in case["trace"][channel]["report"]["attempts"]
                        for cid in attempt["candidate_ids"]
                    }),
                ) for case in result["cases"]
            ],
        )
        write_new(pool_output, pool.model_dump(mode="json"))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--goldset", type=Path)
    parser.add_argument("--split", choices=("pilot", "dev", "test"))
    parser.add_argument(
        "--query-mode", choices=("baseline", "decision_targeted_v1"), default="baseline",
    )
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--approvals", type=Path, help="Independent operator-controlled registry.")
    parser.add_argument("--review-pack", type=Path)
    parser.add_argument(
        "--pool-output", type=Path, help="Private, rank/arm/score-free candidate export.",
    )
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
