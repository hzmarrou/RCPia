"""Re-embed every stored passage and compare, to settle whether any vector is misattributed.

**The cheap audit does not prove what it looked like it proved.** Comparing `embedding_sha256`
against vector identity found 1,508 distinct texts and 1,508 distinct vectors in exact
correspondence, and that was reported as "the corpus is clean". It is not that: unique texts
and unique vectors cannot detect a *permutation*. If two passages had received each other's
vectors, both counts stay 1,508 and the correspondence still looks perfect.

The defect that motivated this — `sorted(rows, key=lambda row: row.get("index", 0))` in the
embedding client — produced exactly permutations. So the one failure mode the audit could not
see is the one the bug caused.

The only check that settles it is to embed each stored `contextual_text` again and compare the
result with the vector on the row. Deterministic models return the same vector for the same
text, so a mismatch beyond floating-point noise means the stored vector belongs to a different
passage.

    uv run python -m rcpia.evidence.audit_embeddings              # every passage
    uv run python -m rcpia.evidence.audit_embeddings --sample 200

Read-only: it never writes a vector. A mismatch is reported, not repaired, because deciding
what to do about a corrupted corpus is not a decision a script should take.
"""

import argparse
import asyncio
import math
import random
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rcpia.core.config import get_settings
from rcpia.evidence.embedding import BATCH_SIZE, EmbeddingError, build_embedder
from rcpia.evidence.models import EvidenceChunk

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# **The threshold is derived per run, not carried as a constant.**
#
# The first version used `1e-6`, picked as "about zero", and reported six mismatches out of
# twenty — all noise, because the model is not bit-deterministic. The second used `0.01`,
# justified by comparing each vector against *the next passage's* embedding: min 0.140727,
# max same-text noise 0.000058, "2428x separation, a misattribution cannot hide under it".
#
# That was wrong, and wrong in the dangerous direction. The next passage is not the nearest
# one. Measured across the whole corpus, the closest pair of passages with different text sit
# at **0.000998** — a tenth of that threshold. A swap between those two would have scored
# under it and been reported as a match. The claim was exactly backwards.
#
# So the audit measures both ends itself and refuses to report a pass unless they separate:
#
#   ceiling  the largest distance seen re-embedding a passage's own text
#   floor    the smallest distance between any two passages with different text
#
# A stored vector belonging to another passage cannot score below `floor`. Noise cannot score
# above `ceiling`. If `ceiling >= floor` there is no threshold that separates them and the
# audit says so rather than choosing one.
SEPARATION_REQUIRED = 3.0


class AuditError(Exception):
    """The audit could not be completed, so its result must not be read as a pass."""


def cosine_distance(left: list[float], right: list[float]) -> float:
    """Cosine distance, refusing anything that would compare false against every threshold.

    **`NaN > TOLERANCE` is False**, so a non-finite component would sail through as a match —
    the same trap documented at length in `rerank._parse_scores` and then walked into here, in
    a new file, by the same author. A distance that cannot be compared is not a small distance.
    """
    for name, vector in (("stored", left), ("fresh", right)):
        for value in vector:
            if value != value or value in (float("inf"), float("-inf")):
                raise AuditError(f"the {name} vector holds {value}, which is not finite")

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise AuditError("a vector has zero magnitude; cosine distance is undefined")
    distance = 1.0 - dot / (left_norm * right_norm)
    if distance != distance:
        raise AuditError("the distance is not a number")
    return distance


async def nearest_distinct_neighbours(session: AsyncSession) -> dict[str, float]:
    """Per passage, the distance to the closest other passage with different text.

    **The threshold has to be per passage, because a global one does not exist.** The first
    attempt took the corpus-wide minimum as a floor and compared every passage against it.
    Measured: that minimum is 0.000998, while re-embedding the same text reaches 0.006831 on
    the noisiest passage. Noise is larger than the closest possible misattribution, so no
    single number separates them anywhere.

    It separates them *per passage*. A vector misattributed to this passage came from some
    other passage, so it cannot score closer than this passage's nearest distinct neighbour.
    Most passages sit far from their neighbours and are decidable; the few that do not are
    reported as inconclusive rather than folded into a pass.
    """
    rows = (
        await session.execute(
            sa.text(
                """
                select a.chunk_id, min(a.embedding <=> b.embedding) as d
                from evidence_chunks a
                join evidence_chunks b
                  on a.chunk_id <> b.chunk_id
                 and a.embedding_sha256 <> b.embedding_sha256
                where a.embedding is not null and b.embedding is not null
                group by a.chunk_id
                """
            )
        )
    ).all()
    if not rows:
        raise AuditError("the corpus holds fewer than two distinct embedded passages")
    return {row.chunk_id: float(row.d) for row in rows}


async def run(*, sample: int | None) -> int:
    settings = get_settings()
    try:
        embedder = build_embedder(settings)
    except EmbeddingError as exc:
        print(f"cannot embed: {exc}", file=sys.stderr)
        return 2

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    mismatched: list[tuple[str, float]] = []
    inconclusive: list[tuple[str, float, float]] = []
    checked = 0
    ceiling = 0.0

    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.select(
                        EvidenceChunk.chunk_id,
                        EvidenceChunk.contextual_text,
                        EvidenceChunk.embedding,
                        EvidenceChunk.embedding_model,
                    ).where(EvidenceChunk.embedding.is_not(None))
                )
            ).all()

            # **Skipping and then reporting success is the failure this tool exists to catch.**
            # The first version filtered these out, warned on stderr, and finished by saying
            # "no misattribution found across every passage" — a false claim, in the audit
            # written because a false claim had been made.
            stale = [row for row in rows if row.embedding_model != embedder.model]
            if stale:
                print(
                    f"{len(stale)} passage(s) were embedded with a model other than "
                    f"{embedder.model} and cannot be compared against it. The audit covers "
                    "every embedded passage or it covers nothing. Models found: "
                    + ", ".join(sorted({row.embedding_model or "(none)" for row in stale})),
                    file=sys.stderr,
                )
                return 2
            if not rows:
                print(
                    "no embedded passages to check; an audit over nothing is not a pass",
                    file=sys.stderr,
                )
                return 2

            if sample is not None and sample < len(rows):
                # Seeded so a reported result can be reproduced exactly.
                rows = random.Random(20260821).sample(rows, sample)

            neighbours = await nearest_distinct_neighbours(session)
            print(f"comparing {len(rows)} passages against {embedder.model}")
            for start in range(0, len(rows), BATCH_SIZE):
                batch = rows[start : start + BATCH_SIZE]
                fresh = await embedder.embed([row.contextual_text for row in batch])
                for row, vector in zip(batch, fresh, strict=True):
                    distance = cosine_distance(list(row.embedding), vector)
                    checked += 1
                    ceiling = max(ceiling, distance)
                    nearest = neighbours.get(row.chunk_id)
                    if nearest is None:
                        # Every other passage shares this text, so no wrong vector would be
                        # distinguishable. Not a mismatch, and not a proof either.
                        inconclusive.append((row.chunk_id, distance, 0.0))
                    elif distance * SEPARATION_REQUIRED >= nearest:
                        # Either the vector is wrong, or this passage sits too close to its
                        # neighbour for the comparison to decide. Both need a person.
                        if distance >= nearest:
                            mismatched.append((row.chunk_id, distance))
                        else:
                            inconclusive.append((row.chunk_id, distance, nearest))
                print(f"  {min(start + BATCH_SIZE, len(rows))}/{len(rows)}", flush=True)
    except EmbeddingError as exc:
        print(f"embedding failed: {exc}", file=sys.stderr)
        return 2
    except AuditError as exc:
        print(f"audit refused: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    print(f"\nchecked          {checked}")
    print(f"noise ceiling    {ceiling:.6f}  (largest same-text distance seen)")
    print(f"proven           {checked - len(mismatched) - len(inconclusive)}")
    print(f"inconclusive     {len(inconclusive)}")
    print(f"mismatched       {len(mismatched)}")

    if mismatched:
        print("\nA stored vector is further from a fresh embedding of its own text than that")
        print("passage's nearest distinct neighbour. Either the vector belongs to another")
        print("passage, or the text changed after embedding.\n")
        for chunk_id, distance in sorted(mismatched, key=lambda item: -item[1])[:20]:
            print(f"  {distance:.6f}  {chunk_id}")
        if len(mismatched) > 20:
            print(f"  … and {len(mismatched) - 20} more")
        return 1

    if inconclusive:
        print("\nToo close to their nearest distinct neighbour for this method to decide:\n")
        for chunk_id, distance, nearest in sorted(inconclusive, key=lambda i: -i[1])[:10]:
            print(f"  self {distance:.6f}  nearest {nearest:.6f}  {chunk_id}")
        if len(inconclusive) > 10:
            print(f"  … and {len(inconclusive) - 10} more")
        print(
            f"\n{checked - len(inconclusive)} of {checked} passages are proven to hold their "
            f"own vector. The remaining {len(inconclusive)} cannot be decided this way: their "
            "nearest distinct neighbour sits closer than the noise in re-embedding them. "
            "Reporting that as a pass is what this audit exists to stop.",
            file=sys.stderr,
        )
        return 1

    scope = (
        f"all {checked} embedded passages"
        if sample is None
        else f"a random sample of {checked}"
    )
    print(f"\nEvery vector matches a fresh embedding of its own text, across {scope}.")
    return 0


def main() -> int:  # pragma: no cover - process entrypoint
    parser = argparse.ArgumentParser(
        description="Re-embed stored passages and compare, to detect misattributed vectors."
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="check this many passages at random instead of all of them",
    )
    arguments = parser.parse_args()
    return asyncio.run(run(sample=arguments.sample))


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
