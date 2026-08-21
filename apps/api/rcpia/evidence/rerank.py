"""Scoring how well a passage answers the question.

The two retrieval legs are bi-encoders in spirit: keyword search compares words, and the
dense leg compares a query vector with a passage vector computed independently. Neither ever
reads the query and the passage together. A cross-encoder does — one model, both texts, joint
attention — and returns a single relevance score. It is slower per call, which is why it runs
on the fused candidates rather than on the corpus.

**What it buys us that the legs cannot.** Measured against the loaded corpus: a French
question about neoadjuvant chemotherapy scored an *English* NCCN passage 0.946, above the
French AFU passage at 0.934. The keyword leg cannot cross that boundary at all, and 81% of
the corpus is English.

**The relevance floor is the reason this stage exists at all**, more than the reordering. A
retriever that always returns its top ten has no way to say the corpus does not answer the
question — it returns ten passages regardless, each with a real citation and a real page
number. Three probe queries against the corpus separate cleanly: the worst genuinely relevant
candidate scored 0.579, and the best candidate for a deliberately irrelevant question scored
0.447. The floor sits in that gap.

That is three queries on one corpus, so treat the number as a starting point that the
retrieval evaluation should revise, not as a constant with authority.
"""

import asyncio
from typing import Protocol

import httpx

from rcpia.core.config import Settings

TIMEOUT_SECONDS = 90.0

# Same shape as the embedding client, and for the same reason: a rate limit during a run of
# many questions is ordinary rather than exceptional.
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 4.0

# Cohere charges a search unit per call and accepts long passages, but a runaway
# `contextual_text` should not become an unbounded request. Our longest chunk is 3,598
# characters, so this truncates nothing today.
MAX_DOCUMENT_CHARACTERS = 8000


class RerankError(Exception):
    """The reranker could not be reached, or refused the request."""


def _parse_scores(rows: object, *, expected: int) -> list[float]:
    """Turn the service's rows into one score per passage, or refuse the response.

    **Counting the rows is not validating them**, and this function exists because that is
    what the first version did. Every failure below passes a length check, and every one of
    them corrupts the ranking silently rather than raising:

    - **A duplicated index** writes one slot repeatedly and leaves the rest at their initial
      value. With a `0.0` default, forty-nine passages then fall below the relevance floor
      and are counted as "below floor" — so the search reports that the corpus has nothing
      to say about the question. A false refusal is the one failure mode a clinician cannot
      see: it reads as a gap in the guideline rather than a fault in us.
    - **A negative index** writes to the wrong end of the list, attaching a score to a
      passage that did not earn it.
    - **An index past the end** raises `IndexError`, which is not a `RerankError`, so a
      caller catching the documented exception does not catch it.
    - **A non-finite score** is worse than either. `inf` clears the relevance floor and
      sorts to the top of the results. `NaN` compares false against everything, so it is
      dropped by the floor *and* missed by the `below_floor` count — it vanishes from both
      tallies and the arithmetic silently stops adding up.

    Refusing the whole response is the right response to any of them. A rerank call is
    retryable and its failure is loud; a corrupted ranking is neither.
    """
    if not isinstance(rows, list):
        raise RerankError(f"Rerank response held no results list, got {type(rows).__name__}")
    if len(rows) != expected:
        raise RerankError(f"Rerank response held {len(rows)} scores for {expected} passages")

    scores: list[float | None] = [None] * expected
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RerankError(f"Rerank result {position} was {type(row).__name__}, not an object")
        try:
            index = int(row["index"])
            score = float(row["relevance_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError(f"Rerank result {position} was unreadable: {exc}") from exc

        if not 0 <= index < expected:
            raise RerankError(
                f"Rerank result {position} indexed passage {index}, outside 0..{expected - 1}"
            )
        if scores[index] is not None:
            raise RerankError(f"Rerank response scored passage {index} more than once")
        # `x != x` is true only for NaN; the comparisons catch both infinities. Written this
        # way rather than with `math.isfinite` so the NaN case is visible to a reader.
        if score != score or score in (float("inf"), float("-inf")):
            raise RerankError(f"Rerank result {position} scored passage {index} as {score}")
        scores[index] = score

    # Unreachable: `expected` distinct indexes in `0..expected-1` fill every slot. Asserted
    # rather than assumed, because the alternative is a `None` reaching the relevance floor.
    if any(score is None for score in scores):
        raise RerankError("Rerank response left passages unscored")
    return [score for score in scores if score is not None]


class Reranker(Protocol):
    """What retrieval needs. Implemented by Azure, and by a fake in the tests."""

    @property
    def model(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...

    async def score(self, query: str, documents: list[str]) -> list[float]: ...


class AzureReranker:
    """`Cohere-rerank-v4.0-pro`, deployed in Azure AI Foundry.

    Azure hosts it behind Cohere's own API shape rather than the OpenAI one, so it answers on
    `/models/v1/rerank` and not under `/openai/v1`. It is a different base URL, not another
    deployment name.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        deployment: str,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._deployment = deployment
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._deployment

    @property
    def fingerprint(self) -> str:
        """What actually served the request, for a run record to pin.

        `model` alone is not enough. An Azure deployment name is chosen by whoever created
        the deployment: two workspaces can both call it `Cohere-rerank-v4.0-pro` and serve
        different model versions, and the same name can be repointed at a new version
        without any change here. An evaluation whose result is attributed to
        `Cohere-rerank-v4.0-pro` and nothing else cannot be reproduced or compared across
        releases, which is what the release gate needs it for.

        The host distinguishes the deployment; the key does not appear.
        """
        host = self._base_url.split("//", 1)[-1].split("/", 1)[0]
        return f"azure-rerank:{host}:{self._deployment}"

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Relevance for each document, in the order the documents were given.

        Order matters as much as it does for embeddings: the response identifies rows by
        `index`, and pairing them positionally instead would attach every score to the wrong
        passage — an error that does not raise and looks like poor retrieval.
        """
        if not documents:
            return []

        payload = {
            "model": self._deployment,
            "query": query,
            "documents": [document[:MAX_DOCUMENT_CHARACTERS] for document in documents],
            "top_n": len(documents),
        }

        response = await self._post_with_retries(payload)
        return _parse_scores(response.json().get("results"), expected=len(documents))

    async def _post_with_retries(self, payload: dict[str, object]) -> httpx.Response:
        import json

        # Encoded explicitly rather than left to the client. The service returns 500 with a
        # `UnicodeDecodeError` when the body is not valid UTF-8, and every passage we send is
        # French.
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last = ""

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        f"{self._base_url}/rerank",
                        headers={
                            "api-key": self._api_key,
                            "Content-Type": "application/json",
                        },
                        content=body,
                    )
                except httpx.HTTPError as exc:
                    last = f"transport error: {exc}"
                else:
                    if response.status_code == 200:
                        return response
                    last = f"{response.status_code}: {response.text[:200]}"
                    if response.status_code not in (429, 500, 502, 503, 504):
                        break
                    # Nothing follows the last attempt, so waiting before it is time spent
                    # to no purpose — 64 seconds at the fifth attempt, and once per query in
                    # an evaluation run of a hundred of them.
                    if attempt == MAX_ATTEMPTS:
                        break
                    header = response.headers.get("retry-after")
                    delay = (
                        float(header)
                        if header and header.replace(".", "", 1).isdigit()
                        else BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    )
                    await asyncio.sleep(delay)
                    continue

                if attempt == MAX_ATTEMPTS:
                    break
                await asyncio.sleep(BASE_BACKOFF_SECONDS * attempt)

        raise RerankError(f"Rerank request failed after {MAX_ATTEMPTS} attempts - {last}")


def build_reranker(settings: Settings) -> Reranker:
    # The live workspace issues one key for both endpoints, so `azure_rerank_api_key` is
    # normally empty and the embedding key is used. Naming it separately means a workspace
    # that issues per-endpoint keys is configuration rather than a code change.
    api_key = settings.azure_rerank_api_key or settings.azure_openai_api_key
    if not api_key or not settings.azure_rerank_base_url:
        raise RerankError(
            "AZURE_RERANK_BASE_URL and an API key (AZURE_RERANK_API_KEY, or "
            "AZURE_OPENAI_API_KEY) must be set to rerank passages."
        )
    return AzureReranker(
        base_url=settings.azure_rerank_base_url,
        api_key=api_key,
        deployment=settings.azure_rerank_deployment,
    )
