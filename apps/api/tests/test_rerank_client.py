"""The real reranker client, not the fake.

Everything else exercises `FakeReranker`, which returns a well-formed list because a test
wrote it. The failures that matter are the ones a remote service produces and a length check
waves through — so these tests are about the parsing and the retrying, not about scoring.
"""

import asyncio
from typing import Any

import httpx
import pytest

from rcpia.core.config import Settings
from rcpia.evidence.rerank import (
    BASE_BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    AzureReranker,
    RerankError,
    _parse_scores,
    build_reranker,
)


def rows(*pairs: tuple[int, float]) -> list[dict[str, Any]]:
    return [{"index": index, "relevance_score": score} for index, score in pairs]


# --------------------------------------------------------------------------------------
# Parsing a response
# --------------------------------------------------------------------------------------


def test_scores_are_ordered_by_index_not_by_position() -> None:
    """The service is free to answer in relevance order. Pairing positionally would attach
    every score to the wrong passage, which does not raise and looks like poor retrieval."""
    assert _parse_scores(rows((2, 0.9), (0, 0.3), (1, 0.6)), expected=3) == [0.3, 0.6, 0.9]


def test_a_duplicated_index_is_refused() -> None:
    """The failure the length check cannot see. Three rows for three passages, all naming
    passage 0: two passages would have kept their initial score, fallen below the relevance
    floor, and been counted as 'below floor' — so the search would report that the corpus
    has nothing to say. A false refusal reads as a gap in the guideline, not a fault here."""
    with pytest.raises(RerankError, match="more than once"):
        _parse_scores(rows((0, 0.9), (0, 0.8), (0, 0.7)), expected=3)


def test_a_negative_index_is_refused() -> None:
    """`scores[-1]` is valid Python and silently scores the last passage."""
    with pytest.raises(RerankError, match="outside 0..2"):
        _parse_scores(rows((0, 0.9), (1, 0.8), (-1, 0.7)), expected=3)


def test_an_index_past_the_end_is_refused_as_a_rerank_error() -> None:
    """It raised `IndexError` before, which callers catching `RerankError` do not catch."""
    with pytest.raises(RerankError, match="outside 0..2"):
        _parse_scores(rows((0, 0.9), (1, 0.8), (3, 0.7)), expected=3)


def test_infinity_is_refused() -> None:
    """`inf` clears any relevance floor and sorts to the top of the results."""
    with pytest.raises(RerankError, match="inf"):
        _parse_scores(rows((0, 0.9), (1, float("inf"))), expected=2)


def test_not_a_number_is_refused() -> None:
    """Worse than `inf`, because it is invisible: NaN compares false against everything, so
    it is dropped by the floor *and* missed by the below-floor count."""
    with pytest.raises(RerankError, match="nan"):
        _parse_scores(rows((0, 0.9), (1, float("nan"))), expected=2)


@pytest.mark.parametrize("index", [1.0, 0.5, True, "1", None])
def test_a_non_integer_index_is_refused(index: object) -> None:
    """`int()` is a converter, not a validator: `int(2.9)` is 2 and `int(True)` is 1, so a
    float or boolean index would be coerced onto a neighbouring passage — the same silent
    misattribution every other check here refuses."""
    with pytest.raises(RerankError, match="must be an integer"):
        _parse_scores(
            [{"index": 0, "relevance_score": 0.9}, {"index": index, "relevance_score": 0.4}],
            expected=2,
        )


def test_a_missing_key_is_refused_as_a_rerank_error() -> None:
    with pytest.raises(RerankError, match="unreadable"):
        _parse_scores([{"index": 0}, {"index": 1, "relevance_score": 0.4}], expected=2)


def test_a_row_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(RerankError, match="not an object"):
        _parse_scores([0.9, 0.4], expected=2)


def test_a_short_response_is_refused() -> None:
    with pytest.raises(RerankError, match="1 scores for 2 passages"):
        _parse_scores(rows((0, 0.9)), expected=2)


def test_a_missing_results_list_is_refused() -> None:
    with pytest.raises(RerankError, match="no results list"):
        _parse_scores(None, expected=2)


def test_integral_scores_are_accepted() -> None:
    """A service returning `1` rather than `1.0` is well-formed, not a failure."""
    assert _parse_scores([{"index": 0, "relevance_score": 1}], expected=1) == [1.0]


# --------------------------------------------------------------------------------------
# Talking to the service
# --------------------------------------------------------------------------------------


def transport(handler: Any) -> Any:
    return httpx.MockTransport(handler)


async def call_with(monkeypatch: pytest.MonkeyPatch, handler: Any) -> tuple[Any, list[float]]:
    """Run `score` against a mock transport, recording every sleep instead of taking it."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)

    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    reranker = AzureReranker(
        base_url="https://example.invalid/models/v1", api_key="k", deployment="d"
    )
    return reranker, slept


async def test_documents_are_sent_and_scores_come_back_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": rows((1, 0.2), (0, 0.8))})

    reranker, _ = await call_with(monkeypatch, handler)
    assert await reranker.score("question", ["premier", "second"]) == [0.8, 0.2]
    assert seen["documents"] == ["premier", "second"]
    assert seen["top_n"] == 2


async def test_french_passages_survive_the_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body is encoded explicitly because the service answers 500 with a
    `UnicodeDecodeError` when it is not valid UTF-8, and every French passage has accents."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"results": rows((0, 0.7))})

    reranker, _ = await call_with(monkeypatch, handler)
    await reranker.score("Quel traitement ?", ["Curiethérapie à bas débit de dose"])
    assert seen["documents"] == ["Curiethérapie à bas débit de dose"]


async def test_no_documents_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have been called")

    reranker, _ = await call_with(monkeypatch, handler)
    assert await reranker.score("question", []) == []


async def test_the_last_attempt_is_not_followed_by_a_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing follows the final attempt, so waiting before giving up is time spent for
    nothing — 64 seconds at the fifth attempt, once per query in an evaluation run."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    reranker, slept = await call_with(monkeypatch, handler)
    with pytest.raises(RerankError, match="after 5 attempts"):
        await reranker.score("question", ["passage"])

    assert len(slept) == MAX_ATTEMPTS - 1
    assert slept[-1] == BASE_BACKOFF_SECONDS * 2 ** (MAX_ATTEMPTS - 2)


async def test_a_transport_error_also_stops_sleeping_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    reranker, slept = await call_with(monkeypatch, handler)
    with pytest.raises(RerankError, match="transport error"):
        await reranker.score("question", ["passage"])

    assert len(slept) == MAX_ATTEMPTS - 1


async def test_a_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad request")

    reranker, slept = await call_with(monkeypatch, handler)
    with pytest.raises(RerankError, match="400"):
        await reranker.score("question", ["passage"])

    assert len(calls) == 1
    assert slept == []


async def test_retry_after_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, text="slow down")
        return httpx.Response(200, json={"results": rows((0, 0.6))})

    reranker, slept = await call_with(monkeypatch, handler)
    assert await reranker.score("question", ["passage"]) == [0.6]
    assert slept == [7.0]


# --------------------------------------------------------------------------------------
# Identifying what served the request
# --------------------------------------------------------------------------------------


def test_the_fingerprint_names_the_host_as_well_as_the_deployment() -> None:
    """An Azure deployment name is chosen by whoever made the deployment. Two workspaces can
    both call it `Cohere-rerank-v4.0-pro` and serve different versions, so a run record
    pinning only the name cannot be compared across releases."""
    reranker = AzureReranker(
        base_url="https://rcpia-eu.services.ai.azure.com/models/v1",
        api_key="k",
        deployment="Cohere-rerank-v4.0-pro",
    )
    assert reranker.fingerprint == (
        "azure-rerank:rcpia-eu.services.ai.azure.com:Cohere-rerank-v4.0-pro"
    )


def test_the_fingerprint_does_not_carry_the_key() -> None:
    reranker = AzureReranker(
        base_url="https://example.invalid/models/v1", api_key="secret-key", deployment="d"
    )
    assert "secret-key" not in reranker.fingerprint


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "azure_openai_api_key": "shared",
        "azure_rerank_base_url": "https://example.invalid/models/v1",
        "auth_secret": "x" * 64,
    }
    base.update(overrides)
    return Settings(**base)


def test_the_embedding_key_is_used_when_no_rerank_key_is_set() -> None:
    """What the live workspace issues today: one key for both endpoints."""
    reranker = build_reranker(settings())
    assert isinstance(reranker, AzureReranker)
    assert reranker._api_key == "shared"


def test_a_rerank_key_takes_precedence_when_set() -> None:
    """A workspace with per-endpoint keys is then a configuration change, not a code change
    — and rotating the embedding key cannot silently break reranking."""
    reranker = build_reranker(settings(azure_rerank_api_key="rerank-only"))
    assert isinstance(reranker, AzureReranker)
    assert reranker._api_key == "rerank-only"


def test_a_rerank_key_alone_is_enough() -> None:
    reranker = build_reranker(settings(azure_openai_api_key="", azure_rerank_api_key="rerank-only"))
    assert isinstance(reranker, AzureReranker)


def test_no_key_at_all_is_refused() -> None:
    with pytest.raises(RerankError, match="API key"):
        build_reranker(settings(azure_openai_api_key=""))


def test_no_base_url_is_refused() -> None:
    with pytest.raises(RerankError, match="AZURE_RERANK_BASE_URL"):
        build_reranker(settings(azure_rerank_base_url=""))
