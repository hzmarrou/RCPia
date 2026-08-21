"""The real embedding client, not the fake.

**This file should have existed a round earlier.** The identical response-validation defect was
found and fixed in `rerank.py`, and nobody looked at the sibling that produces the vectors — so
the same bug survived, in the one place where its consequence is permanent.

A corrupted rerank score spoils a query. A misattached vector is *stored*, and
`embedding_sha256` is computed from our own text, so on the next ingest the hash matches, the
loader reports the passage unchanged, and skips re-embedding. The wrong vector then outlives
every subsequent release until somebody forces it out by hand. The passage simply retrieves for
the wrong questions, quietly, for as long as the corpus lives.
"""

import asyncio
from typing import Any

import httpx
import pytest

from rcpia.core.config import Settings
from rcpia.evidence.embedding import (
    BASE_BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    AzureEmbedder,
    EmbeddingError,
    _parse_vectors,
    build_embedder,
)

DIMENSIONS = 4


def rows(*pairs: tuple[Any, list[float]]) -> list[dict[str, Any]]:
    return [{"index": index, "embedding": vector} for index, vector in pairs]


def vector(fill: float) -> list[float]:
    return [fill] * DIMENSIONS


def parse(rows_in: object, expected: int = 2) -> list[list[float]]:
    return _parse_vectors(rows_in, expected=expected, dimensions=DIMENSIONS)


# --------------------------------------------------------------------------------------
# Parsing a response — the defect that was fixed in the reranker and left here
# --------------------------------------------------------------------------------------


def test_vectors_are_ordered_by_index_not_by_arrival() -> None:
    assert parse(rows((1, vector(2.0)), (0, vector(1.0)))) == [vector(1.0), vector(2.0)]


def test_a_row_with_no_index_is_refused() -> None:
    """The original sorted by `row.get("index", 0)`, so a missing index silently became 0 and
    sorted first — attaching that vector to the first passage and shifting every other."""
    with pytest.raises(EmbeddingError, match="must be an integer"):
        parse([{"index": 0, "embedding": vector(1.0)}, {"embedding": vector(2.0)}])


def test_a_duplicated_index_is_refused() -> None:
    """Sorting by a key that repeats preserves no order at all. Both rows survived and every
    later position shifted by one, with no error anywhere."""
    with pytest.raises(EmbeddingError, match="passage 0 twice"):
        parse(rows((0, vector(1.0)), (0, vector(2.0))))


def test_a_negative_index_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="outside 0..1"):
        parse(rows((0, vector(1.0)), (-1, vector(2.0))))


def test_an_index_past_the_end_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="outside 0..1"):
        parse(rows((0, vector(1.0)), (2, vector(2.0))))


@pytest.mark.parametrize("index", [1.0, 0.5, True, "1", None])
def test_a_non_integer_index_is_refused(index: object) -> None:
    """`sorted` accepts a float key without complaint, and `int()` would have coerced it."""
    with pytest.raises(EmbeddingError, match="must be an integer"):
        parse(rows((0, vector(1.0)), (index, vector(2.0))))


def test_a_short_response_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="1 vectors for 2 passages"):
        parse(rows((0, vector(1.0))))


def test_a_missing_data_list_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="no data list"):
        parse(None)


def test_a_row_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="not an object"):
        parse([vector(1.0), vector(2.0)])


# --------------------------------------------------------------------------------------
# The vector itself
# --------------------------------------------------------------------------------------


def test_a_vector_of_the_wrong_width_is_refused() -> None:
    """The column is `vector(1536)` and PostgreSQL refuses anything else, so this would fail
    on write — but it would fail in the middle of a batch, after money had been spent."""
    with pytest.raises(EmbeddingError, match="3 dimensions, expected 4"):
        parse(rows((0, vector(1.0)), (1, [0.1, 0.2, 0.3])))


def test_a_missing_vector_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="no dimensions"):
        parse([{"index": 0, "embedding": vector(1.0)}, {"index": 1}])


def test_a_non_numeric_component_is_refused() -> None:
    with pytest.raises(EmbeddingError, match="not a number"):
        parse(rows((0, vector(1.0)), (1, [0.1, "x", 0.3, 0.4])))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_component_is_refused(bad: float) -> None:
    """pgvector stores these happily, and every cosine distance against the vector is then
    meaningless — the passage neither matches nor fails to match."""
    with pytest.raises(EmbeddingError, match="not finite"):
        parse(rows((0, vector(1.0)), (1, [0.1, bad, 0.3, 0.4])))


def test_integers_are_accepted_as_components() -> None:
    """A service returning `0` rather than `0.0` is well-formed, not a failure."""
    assert parse([{"index": 0, "embedding": [0, 1, 0, 1]}], expected=1) == [
        [0.0, 1.0, 0.0, 1.0]
    ]


# --------------------------------------------------------------------------------------
# Talking to the service
# --------------------------------------------------------------------------------------


async def call_with(
    monkeypatch: pytest.MonkeyPatch, handler: Any
) -> tuple[AzureEmbedder, list[float]]:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    embedder = AzureEmbedder(
        base_url="https://example.invalid/openai/v1",
        api_key="k",
        deployment="text-embedding-3-large",
        dimensions=DIMENSIONS,
    )
    return embedder, slept


async def test_no_texts_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have been called")

    embedder, _ = await call_with(monkeypatch, handler)
    assert await embedder.embed([]) == []


async def test_vectors_come_back_in_the_order_the_texts_went(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": rows((1, vector(2.0)), (0, vector(1.0)))}
        )

    embedder, _ = await call_with(monkeypatch, handler)
    assert await embedder.embed(["premier", "second"]) == [vector(1.0), vector(2.0)]


async def test_a_response_that_is_not_json_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    embedder, _ = await call_with(monkeypatch, handler)
    with pytest.raises(EmbeddingError, match="not JSON"):
        await embedder.embed(["passage"])


async def test_the_last_attempt_is_not_followed_by_a_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same waste the reranker had: a long final wait, then giving up regardless."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    embedder, slept = await call_with(monkeypatch, handler)
    with pytest.raises(EmbeddingError, match=f"after {MAX_ATTEMPTS} attempts"):
        await embedder.embed(["passage"])

    assert len(slept) == MAX_ATTEMPTS - 1
    assert slept[-1] == BASE_BACKOFF_SECONDS * 2 ** (MAX_ATTEMPTS - 2)


async def test_a_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad request")

    embedder, slept = await call_with(monkeypatch, handler)
    with pytest.raises(EmbeddingError, match="400"):
        await embedder.embed(["passage"])

    assert len(calls) == 1
    assert slept == []


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


def test_the_configured_dimensions_are_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """`text-embedding-3-large` returns 3072 by default and pgvector's indexes cap at 2000, so
    a column that stores fine would then refuse an index. The parameter is not optional."""
    settings = Settings(
        azure_openai_base_url="https://example.invalid/openai/v1",
        azure_openai_api_key="k",
        embedding_dimensions=1536,
        auth_secret="x" * 64,
    )
    embedder = build_embedder(settings)
    assert embedder.dimensions == 1536
