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


class Reranker(Protocol):
    """What retrieval needs. Implemented by Azure, and by a fake in the tests."""

    @property
    def model(self) -> str: ...

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
        rows = response.json().get("results")
        if not isinstance(rows, list) or len(rows) != len(documents):
            raise RerankError(
                f"Rerank response held {len(rows) if isinstance(rows, list) else 0} scores "
                f"for {len(documents)} passages"
            )

        scores = [0.0] * len(documents)
        for row in rows:
            scores[int(row["index"])] = float(row["relevance_score"])
        return scores

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
                    header = response.headers.get("retry-after")
                    delay = (
                        float(header)
                        if header and header.replace(".", "", 1).isdigit()
                        else BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    )
                    await asyncio.sleep(delay)
                    continue

                await asyncio.sleep(BASE_BACKOFF_SECONDS * attempt)

        raise RerankError(f"Rerank request failed after {MAX_ATTEMPTS} attempts - {last}")


def build_reranker(settings: Settings) -> Reranker:
    if not settings.azure_openai_api_key or not settings.azure_rerank_base_url:
        raise RerankError(
            "AZURE_OPENAI_API_KEY and AZURE_RERANK_BASE_URL must be set to rerank passages."
        )
    return AzureReranker(
        base_url=settings.azure_rerank_base_url,
        api_key=settings.azure_openai_api_key,
        deployment=settings.azure_rerank_deployment,
    )
