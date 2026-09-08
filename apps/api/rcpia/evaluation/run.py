"""Run the gold set through every arm, and say which stage lost each missing passage.

**A score without an attribution is not actionable.** `group_recall = 0.62` says the system
missed a third of the claims; it does not say whether the filter refused the passage, whether
neither search leg surfaced it, whether the ranking buried it, or whether the floor threw it
away. Those four have four different repairs, and three of them are cheap. So every miss is
attributed to the stage that lost it, using the trace `retrieval.AttemptTrace` already records
and the per-dimension refusal `retrieval.filter_admits` already computes.

**Nothing here re-implements retrieval.** The arms are driven through the same `search` and
`search_without_reranking` the application calls, with the same defaults. An evaluation that
reached into `_search` with its own clauses would measure a pipeline nobody ships -- which is
the failure this project has already had once, in a probe that admitted more than the filter.

**Single-leg arms are configured, not carved out.** `dense` passes no concepts (the keyword leg
is skipped, and the report says so); `fts` passes no embedding. Both are ordinary parameters of
the production path, so neither arm needs a private code path that could drift from it.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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
from rcpia.evaluation.arms import ABLATIONS, ARMS, Arm, concepts_for, filter_for, filter_for_arm
from rcpia.evaluation.goldset import (
    GOLDSET_FILENAME,
    Goldset,
    GoldsetError,
    Question,
    load_goldset,
    verify_against_corpus,
)
from rcpia.evaluation.ledgers import facts_for, ledger_for
from rcpia.evaluation.metrics import ArmScore, QuestionScore, aggregate, score_question
from rcpia.evaluation.review import ReviewDraft, verify_review_sources
from rcpia.evaluation.runtime_inputs import RuntimeInput
from rcpia.evaluation.runtime_provenance import corpus_identity, readonly_snapshot
from rcpia.evidence.embedding import Embedder, build_embedder
from rcpia.evidence.models import EvidenceChunk
from rcpia.evidence.rerank import Reranker, build_reranker
from rcpia.evidence.retrieval import (
    Concepts,
    RetrievalReport,
    SafetyContext,
    filter_admits,
    search,
    search_without_reranking,
)
from rcpia.query.builder import build_filter
from rcpia.safety_rules.engine import SafetyEngine

if sys.platform == "win32":
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DEFAULT_GOLDSET = Path("docs/evaluation") / GOLDSET_FILENAME
DEFAULT_LIMIT = 10

# The stages a labelled passage can be lost at, in the order it would pass through them. The
# order is the point: a passage the filter refused never reaches the legs, so attributing it to
# ranking would send someone to tune a reranker over a routing bug.
STAGE_STALE = "stale label"
STAGE_FILTER = "filter"
STAGE_CANDIDATES = "candidate set"
STAGE_FLOOR = "relevance floor"
STAGE_RANK = "ranking"
# A passage a safety rule removed. Placed before the floor because the engine runs between
# fusion and reranking. Always zero in v1 -- every rule's action is FLAG and nothing is
# removed -- and wired anyway, so that enabling an exclusion cannot silently be blamed on the
# ranking.
STAGE_SAFETY = "safety rule"
STAGES = (STAGE_STALE, STAGE_FILTER, STAGE_CANDIDATES, STAGE_SAFETY, STAGE_FLOOR, STAGE_RANK)


@dataclass(frozen=True)
class Loss:
    """One labelled passage that did not come back, and where it was lost."""

    chunk_id: str
    claim: str
    stage: str
    detail: str = ""


@dataclass(frozen=True)
class QuestionRun:
    """One question under one arm: what came back, what it scored, what it lost."""

    arm: str
    query_id: str
    returned: tuple[str, ...]
    score: QuestionScore
    losses: tuple[Loss, ...] = ()
    widened: int = 0
    reranked: bool = False
    keyword_hits: int = 0
    dense_hits: int = 0
    below_floor: int = 0
    # The engine's effect, reported *alongside* harm@1 and never instead of it. The rules
    # produce the forbidden labels harm@1 is scored against; if the same rules also removed
    # those passages, the metric would read zero by construction.
    safety_flagged: int = 0
    safety_excluded: int = 0
    safety_unevaluated: tuple[str, ...] = ()

    @property
    def unmet_groups(self) -> int:
        return sum(1 for _ in self.losses)


@dataclass(frozen=True)
class ArmRun:
    arm: Arm
    runs: tuple[QuestionRun, ...]
    score: ArmScore

    @property
    def safety_effect(self) -> dict[str, int]:
        """What the engine did, reported beside harm@1 rather than folded into it."""
        return {
            "flagged": sum(r.safety_flagged for r in self.runs),
            "excluded": sum(r.safety_excluded for r in self.runs),
            "unevaluated": sum(len(r.safety_unevaluated) for r in self.runs),
        }

    @property
    def losses_by_stage(self) -> dict[str, int]:
        counts = dict.fromkeys(STAGES, 0)
        for run in self.runs:
            for loss in run.losses:
                counts[loss.stage] += 1
        return counts


@dataclass(frozen=True)
class Report:
    corpus_release: str
    split: str
    limit: int
    questions: int
    arms: tuple[ArmRun, ...] = field(default_factory=tuple)
    approval: dict[str, object] | None = None


def _declared_purposes(question: Question) -> list[str]:
    """The purposes the question asks about. A property of the question, not of the patient."""
    raw = question.filter.get("clinical_purpose")
    return [str(p) for p in raw] if isinstance(raw, list | tuple) else []


async def present_chunk_ids(session: AsyncSession) -> frozenset[str]:
    rows = await session.execute(select(EvidenceChunk.chunk_id))
    return frozenset(rows.scalars().all())


async def _attribute(
    session: AsyncSession,
    *,
    question: Question,
    arm: Arm,
    report: RetrievalReport,
    returned: Sequence[str],
) -> tuple[Loss, ...]:
    """Name the stage that lost each unsatisfied claim.

    **One loss per claim, not per id.** A group is a claim with several acceptable passages, and
    a claim satisfied by any one of them is not a miss. When none came back, the *best-placed*
    acceptable passage is the one that explains the loss: if one of four reached the candidate
    set and the other three were refused by the filter, the honest attribution is ranking, not
    routing -- fixing the filter would not have helped, because a candidate was already there.
    """
    unmet = [g for g in question.relevant_groups if not (g.acceptable_chunk_ids & set(returned))]
    if not unmet:
        return ()

    if not report.attempts:
        raise ValueError("Retrieval loss attribution requires recorded search attempts.")
    # The final attempt may use a built or widened filter, not the gold set's declaration.
    attempt = report.attempts[-1]
    wanted = sorted(frozenset().union(*(g.acceptable_chunk_ids for g in unmet)))
    decision = await filter_admits(
        session, filter=attempt.filter_requested, chunk_ids=wanted
    )
    by_id = {p.chunk_id: p for p in decision.passages}

    # **`fused_ids`, not `candidate_ids`.** "Reached the candidate set" means a leg surfaced
    # it, and `fused_ids` is exactly the union of the two legs on both paths. `candidate_ids`
    # is the reranked path's `fused[:CANDIDATE_LIMIT]` and is left empty when no cross-encoder
    # ran -- so an unreranked arm would have attributed every one of its misses to the legs,
    # including the ones the legs found and the ranking then dropped.
    candidates = set(attempt.fused_ids)
    removed_by_rule = report.safety.excluded if report.safety else frozenset()
    scored = dict(attempt.relevances)
    above = set(attempt.above_floor)

    def stage_of(chunk_id: str) -> tuple[int, str, str]:
        passage = by_id.get(chunk_id)
        if passage is None or not passage.found:
            return 0, STAGE_STALE, "not in the corpus"
        if chunk_id in removed_by_rule:
            rules = ", ".join(
                sorted({a.rule_id for a in report.safety.for_chunk(chunk_id)})  # type: ignore[union-attr]
            )
            return 2, STAGE_SAFETY, f"removed by {rules or 'a safety rule'}"
        if chunk_id in candidates:
            if arm.uses_floor and chunk_id in scored and chunk_id not in above:
                return 3, STAGE_FLOOR, f"relevance {scored[chunk_id]:.3f}"
            return 4, STAGE_RANK, "a candidate, but outside the returned list"
        if not passage.admitted:
            refused = ", ".join(passage.refused_by) or "an invariant"
            return 1, STAGE_FILTER, f"refused by {refused}"
        return 2, STAGE_CANDIDATES, "admitted, but neither leg surfaced it"

    losses = []
    for group in unmet:
        # Furthest-travelled first: the passage that got closest is the one that explains the
        # miss. Ties break on chunk id so a rerun reports the same passage.
        best = max(
            sorted(group.acceptable_chunk_ids),
            key=lambda chunk_id: stage_of(chunk_id)[0],
        )
        _, stage, detail = stage_of(best)
        losses.append(Loss(chunk_id=best, claim=group.claim, stage=stage, detail=detail))
    return tuple(losses)


async def run_question(
    session: AsyncSession,
    *,
    question: Question,
    arm: Arm,
    embedding: list[float] | None,
    embedding_model: str,
    reranker: Reranker,
    limit: int = DEFAULT_LIMIT,
    engine: SafetyEngine | None = None,
    built_queries: bool = False,
    permit: ScoringPermit | None = None,
) -> QuestionRun:
    require_question_approval(question, permit)
    if built_queries and question.split != "pilot":
        raise GoldsetError("Historical inferred ledgers are pilot diagnostics only; use runtime.")
    if permit is not None:
        await verify_permit_corpus(session, permit)
    # **`built` is the measurement the whole harness exists to enable.** Every score reported
    # so far used the gold set's hand-written filter and concepts, which makes them a ceiling:
    # what retrieval can do when a person has already solved the query. `built` derives both
    # from the dossier, and the drop between the two is the query builder's score.
    if built_queries:
        query = build_filter(
            question.cancer_type,
            facts_for(question),
            agent_role=str(question.filter.get("agent_role") or "") or None,
            clinical_purpose=_declared_purposes(question),
        )
        filter = filter_for_arm(query.filter, arm)
        built_concepts = query.concepts
    else:
        filter = filter_for(question, arm)
        built_concepts = concepts_for(question)

    concepts = Concepts() if arm.legs == "dense" else built_concepts
    vector = None if arm.legs == "keyword" else embedding
    # No ledger, no rules. A question whose dossier cannot be identified gets no safety pass
    # rather than one run against an empty patient, which would report every check as
    # unevaluated and read as if it had been attempted.
    ledger = ledger_for(question)
    safety = SafetyContext(engine=engine, ledger=ledger) if engine and ledger else None

    if arm.rerank:
        report = await search(
            session,
            question=question.question,
            concepts=concepts,
            embedding=vector,
            embedding_model=embedding_model,
            filter=filter,
            reranker=reranker,
            limit=limit,
            trace=True,
            safety=safety,
        )
    else:
        report = await search_without_reranking(
            session,
            question=question.question,
            concepts=concepts,
            embedding=vector,
            embedding_model=embedding_model,
            filter=filter,
            limit=limit,
            trace=True,
            safety=safety,
        )

    returned = [chunk.chunk_id for chunk in report.chunks]
    return QuestionRun(
        arm=arm.name,
        query_id=question.query_id,
        returned=tuple(returned),
        score=score_question(question, returned),
        losses=await _attribute(
            session, question=question, arm=arm, report=report, returned=returned
        ),
        widened=report.widened,
        reranked=report.reranked,
        keyword_hits=report.keyword_hits,
        dense_hits=report.dense_hits,
        below_floor=report.below_floor,
        safety_flagged=len(report.safety.flagged_chunk_ids) if report.safety else 0,
        safety_excluded=len(report.safety.excluded) if report.safety else 0,
        safety_unevaluated=(
            tuple(u.rule_id for u in report.safety.unevaluated) if report.safety else ()
        ),
    )


async def embed_questions(
    embedder: Embedder, questions: Sequence[Question]
) -> dict[str, list[float]]:
    """One vector per question, reused across every arm.

    Embedding per arm would bill nine times for the same text and, worse, would let two arms be
    compared across two different vectors.
    """
    if not questions:
        return {}
    vectors = await embedder.embed([q.question for q in questions])
    return {q.query_id: vector for q, vector in zip(questions, vectors, strict=True)}


async def run_arm(
    session: AsyncSession,
    *,
    arm: Arm,
    questions: Sequence[Question],
    embeddings: dict[str, list[float]],
    embedding_model: str,
    reranker: Reranker,
    limit: int = DEFAULT_LIMIT,
    engine: SafetyEngine | None = None,
    built_queries: bool = False,
    permit: ScoringPermit | None = None,
) -> ArmRun:
    runs = [
        await run_question(
            session,
            question=question,
            arm=arm,
            embedding=embeddings.get(question.query_id),
            embedding_model=embedding_model,
            reranker=reranker,
            limit=limit,
            engine=engine,
            built_queries=built_queries,
            permit=permit,
        )
        for question in questions
    ]
    return ArmRun(
        arm=arm,
        runs=tuple(runs),
        score=aggregate(arm.name, [run.score for run in runs]),
    )


async def run_evaluation(
    session: AsyncSession,
    *,
    goldset: Goldset,
    split: str,
    arms: Sequence[Arm],
    embedder: Embedder,
    reranker: Reranker,
    limit: int = DEFAULT_LIMIT,
    engine: SafetyEngine | None = None,
    built_queries: bool = False,
    inputs: RuntimeInput | None = None,
    manifest: SplitManifest | None = None,
    registry: ApprovalRegistry | None = None,
    review_pack: ReviewDraft | None = None,
) -> Report:
    """Every arm over one split, after refusing a gold set the corpus cannot support."""
    questions = goldset.split(split)
    permit = None
    if split != "pilot" or any(
        item is not None for item in (inputs, manifest, registry, review_pack)
    ):
        if inputs is None:
            raise GoldsetError("Dev/test scoring requires frozen runtime inputs.")
        permit = authorize(
            goldset=goldset, inputs=inputs, split=split, manifest=manifest,
            registry=registry, review_pack=review_pack,
        )
    if built_queries and split != "pilot":
        raise GoldsetError("Historical inferred ledgers are pilot diagnostics only; use runtime.")
    if permit is not None:
        if (
            (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            != "repeatable read"
            or (await session.execute(text("SHOW transaction_read_only"))).scalar_one() != "on"
        ):
            raise GoldsetError("Approved runs require a read-only REPEATABLE READ snapshot.")
        if (await corpus_identity(session))["sha256"] != permit.approval.corpus_sha256:
            raise GoldsetError("Actual corpus differs from the approved corpus.")
        if review_pack is not None:
            await verify_review_sources(session, review_pack)
    verify_against_corpus(goldset, await present_chunk_ids(session))
    embeddings = await embed_questions(embedder, questions)
    releases = {q.corpus_release for q in questions if q.corpus_release}

    results = [
        await run_arm(
            session,
            arm=arm,
            questions=questions,
            embeddings=embeddings,
            embedding_model=embedder.model,
            reranker=reranker,
            limit=limit,
            engine=engine,
            built_queries=built_queries,
            permit=permit,
        )
        for arm in arms
    ]
    return Report(
        corpus_release=", ".join(sorted(releases)),
        split=split,
        limit=limit,
        questions=len(questions),
        arms=tuple(results),
        approval=permit.approval.model_dump(mode="json") if permit else None,
    )


def format_report(report: Report) -> str:
    """A markdown table, safety first.

    The column order is the argument: `harmful@1`, `false refusal` and `correct refusal` come
    before recall because a system that finds the passage nine times in ten and puts a
    contraindicated one at rank 1 on the tenth is worse than one that finds it eight times and
    never does.

    **`correct ref` is here because the first pilot run made it necessary.** It was computed and
    not printed, and every arm scored 0.00 on it -- every one of them answered the question the
    corpus has no answer to. A metric that is aggregated but not rendered is a metric nobody
    reads.
    """
    lines = [
        f"# Retrieval evaluation -- {report.split} split",
        "Approved labels; declared-query ceiling, not actual runtime."
        if report.approval else "Legacy pilot diagnostic; no clinician-approved truth.",
        "",
        f"{report.questions} questions, top {report.limit}, corpus "
        f"{report.corpus_release or 'unrecorded'}.",
        "",
        "| arm | harm@1 | false ref | correct ref | group rec | req rec | chunk rec | P@5 | "
        "nDCG@10 | MRR | irrelevant |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in report.arms:
        s = result.score
        lines.append(
            f"| {s.arm} | {s.harmful_at_1:.2f} | {s.false_refusal:.2f} | "
            f"{s.correct_refusal:.2f} | {s.group_recall:.2f} | "
            f"{s.required_group_recall:.2f} | {s.chunk_recall:.2f} | "
            f"{s.precision_at_5:.2f} | {s.ndcg_at_10:.2f} | {s.mrr:.2f} | "
            f"{s.irrelevant_context:.2f} |"
        )

    if any(any(r.safety_flagged or r.safety_unevaluated for r in a.runs) for a in report.arms):
        lines += [
            "",
            "## What the safety engine did",
            "",
            "Reported **beside** `harm@1`, never instead of it: the same rules produce the "
            "forbidden labels `harm@1` is scored against, so an engine that also removed those "
            "passages would drive the metric to zero by construction.",
            "",
            "| arm | passages flagged | removed | checks not run |",
            "|---|---|---|---|",
        ]
        for result in report.arms:
            effect = result.safety_effect
            lines.append(
                f"| {result.arm.name} | {effect['flagged']} | {effect['excluded']} | "
                f"{effect['unevaluated']} |"
            )

    lines += ["", "## Where the misses were lost", "", "| arm | " + " | ".join(STAGES) + " |",
              "|---" * (len(STAGES) + 1) + "|"]
    for result in report.arms:
        counts = result.losses_by_stage
        lines.append(
            f"| {result.arm.name} | " + " | ".join(str(counts[stage]) for stage in STAGES) + " |"
        )
    return "\n".join(lines) + "\n"


def report_as_dict(report: Report) -> dict:
    return {
        "scoring": "approved_labels" if report.approval else "legacy_pilot_diagnostic",
        "approval": report.approval,
        "corpus_release": report.corpus_release,
        "split": report.split,
        "limit": report.limit,
        "questions": report.questions,
        "arms": [
            {
                "arm": result.arm.name,
                "note": result.arm.note,
                "score": asdict(result.score),
                "losses_by_stage": result.losses_by_stage,
                "safety_effect": result.safety_effect,
                "runs": [
                    {
                        "query_id": run.query_id,
                        "returned": list(run.returned),
                        "widened": run.widened,
                        "reranked": run.reranked,
                        "keyword_hits": run.keyword_hits,
                        "dense_hits": run.dense_hits,
                        "below_floor": run.below_floor,
                        "safety_flagged": run.safety_flagged,
                        "safety_excluded": run.safety_excluded,
                        "safety_unevaluated": list(run.safety_unevaluated),
                        "score": asdict(run.score),
                        "losses": [asdict(loss) for loss in run.losses],
                    }
                    for run in result.runs
                ],
            }
            for result in report.arms
        ],
    }


def arms_for(names: Sequence[str] | None, *, ablations: bool) -> tuple[Arm, ...]:
    available = {arm.name: arm for arm in (*ARMS, *ABLATIONS)}
    if names:
        unknown = [name for name in names if name not in available]
        if unknown:
            raise SystemExit(
                f"unknown arm(s): {', '.join(unknown)}. Known: {', '.join(available)}"
            )
        return tuple(available[name] for name in names)
    return (*ARMS, *ABLATIONS) if ablations else ARMS


async def _run(args: argparse.Namespace) -> int:  # pragma: no cover - process entrypoint
    goldset = load_goldset(args.goldset)
    goldset.split(args.split)
    inputs = (
        RuntimeInput.model_validate(load_json(args.runtime_input.read_bytes()))
        if getattr(args, "runtime_input", None) else None
    )
    manifest = (
        SplitManifest.model_validate(load_json(args.split_manifest.read_bytes()))
        if getattr(args, "split_manifest", None) else None
    )
    registry = (
        ApprovalRegistry.model_validate(load_json(args.approvals.read_bytes()))
        if getattr(args, "approvals", None) else None
    )
    review_pack = (
        ReviewDraft.model_validate(load_json(args.review_pack.read_bytes()))
        if getattr(args, "review_pack", None) else None
    )
    if args.split != "pilot" or any(
        item is not None for item in (inputs, manifest, registry, review_pack)
    ):
        if inputs is None:
            raise GoldsetError("Dev/test scoring requires frozen runtime inputs.")
        authorize(
            goldset=goldset, inputs=inputs, split=args.split, manifest=manifest,
            registry=registry, review_pack=review_pack,
        )
    if args.queries == "built" and args.split != "pilot":
        raise GoldsetError("Historical inferred ledgers are pilot diagnostics only; use runtime.")
    settings = get_settings()
    embedder = build_embedder(settings)
    reranker = build_reranker(settings)
    engine = create_async_engine(settings.database_url)
    try:
        async with readonly_snapshot(engine) as session:
            report = await run_evaluation(
                session,
                goldset=goldset,
                split=args.split,
                arms=arms_for(args.arm, ablations=args.ablations),
                embedder=embedder,
                reranker=reranker,
                limit=args.limit,
                engine=SafetyEngine() if args.safety else None,
                built_queries=args.queries == "built",
                inputs=inputs, manifest=manifest, registry=registry, review_pack=review_pack,
            )
    finally:
        await engine.dispose()

    text = format_report(report)
    print(text)
    if args.out:
        args.out.write_text(json.dumps(report_as_dict(report), indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def main() -> int:  # pragma: no cover - process entrypoint
    parser = argparse.ArgumentParser(description="Score the gold set against the live corpus.")
    parser.add_argument("--goldset", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--split", default="pilot")
    parser.add_argument("--runtime-input", type=Path)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--review-pack", type=Path)
    parser.add_argument("--arm", action="append", help="run only these arms; repeatable")
    parser.add_argument("--ablations", action="store_true", help="include the per-dimension arms")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--out", type=Path, help="write the full per-question JSON here")
    parser.add_argument(
        "--queries",
        choices=("declared", "built"),
        default="declared",
        help="`declared` uses the gold set's hand-written filter and concepts (a ceiling); "
        "`built` derives both from the dossier, which is what the deployed system will do",
    )
    parser.add_argument(
        "--safety",
        action="store_true",
        help="run the safety rules over the retrieved passages and report their effect",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
