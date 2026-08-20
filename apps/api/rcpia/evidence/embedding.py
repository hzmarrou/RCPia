"""Turning guideline passages into vectors.

One small client over the Azure OpenAI embeddings endpoint, kept behind a protocol so the
model can be swapped after the retrieval evaluation exists. Which model is right for French
clinical text is a measurement, not an argument — and the answer changes nothing above this
module except the numbers in the column.

Two things are recorded rather than assumed:

- **the dimension count**, because `text-embedding-3-large` returns 3072 by default and
  pgvector's indexes cap at 2000; asking for 1536 keeps the column indexable;
- **the model name on every row**, because vectors from different models are not
  comparable. A table holding both would rank nonsense with nothing to show for it.
"""

import asyncio
from typing import Protocol

import httpx

from rcpia.core.config import Settings

# Azure prices and rate-limits per request, so passages go up in batches. Large enough to
# be cheap, small enough that one failure does not lose much work.
BATCH_SIZE = 64
TIMEOUT_SECONDS = 120.0

# The S0 tier rate-limits an ingest of a few thousand passages, so a 429 is expected rather
# than exceptional. Azure usually says how long to wait; where it does not, back off
# exponentially. Failing the run instead would abandon the passages already embedded and
# paid for.
MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 4.0


class EmbeddingError(Exception):
    """The embedding service could not be reached, or refused the request."""


class Embedder(Protocol):
    """What the ingestion needs. Implemented by Azure, and by a fake in the tests."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class AzureEmbedder:
    """The deployed `text-embedding-3-large`, at a dimension pgvector can index."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        deployment: str,
        dimensions: int,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._deployment = deployment
        self._dimensions = dimensions
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._deployment

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def _post_with_retries(self, texts: list[str]) -> httpx.Response:
        """Send one batch, waiting out rate limits.

        A 429 during a corpus ingest is ordinary: the tier allows a few requests a minute
        and a release holds thousands of passages. Giving up would discard the work already
        done, so the batch waits and tries again.
        """
        last: str = ""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        f"{self._base_url}/embeddings",
                        headers={"api-key": self._api_key},
                        json={
                            "model": self._deployment,
                            "input": texts,
                            "dimensions": self._dimensions,
                        },
                    )
                except httpx.HTTPError as exc:
                    last = f"transport error: {exc}"
                else:
                    if response.status_code == 200:
                        return response
                    last = f"{response.status_code}: {response.text[:200]}"
                    if response.status_code not in (429, 500, 502, 503, 504):
                        break
                    # Azure names the wait where it can; otherwise back off.
                    header = response.headers.get("retry-after")
                    delay = (
                        float(header)
                        if header and header.replace(".", "", 1).isdigit()
                        else BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    )
                    await asyncio.sleep(delay)
                    continue

                await asyncio.sleep(BASE_BACKOFF_SECONDS * attempt)

        raise EmbeddingError(f"Embedding request failed after {MAX_ATTEMPTS} attempts — {last}")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, preserving order.

        Order matters: the caller pairs the result with the passages it sent by position,
        so a reordering would attach every vector to the wrong text — an error that would
        not raise, and would look like poor retrieval rather than a bug.
        """
        if not texts:
            return []

        response = await self._post_with_retries(texts)

        payload = response.json()
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise EmbeddingError(
                f"Embedding response held {len(rows) if isinstance(rows, list) else 0} "
                f"vectors for {len(texts)} passages"
            )

        # Azure returns an `index` per row. Sorting by it rather than trusting arrival
        # order is cheap insurance against the failure above.
        ordered = sorted(rows, key=lambda row: row.get("index", 0))
        vectors = [row["embedding"] for row in ordered]

        for vector in vectors:
            if len(vector) != self._dimensions:
                raise EmbeddingError(
                    f"Embedding has {len(vector)} dimensions, expected {self._dimensions}"
                )
        return vectors


def build_embedder(settings: Settings) -> Embedder:
    if not settings.azure_openai_api_key or not settings.azure_openai_base_url:
        raise EmbeddingError(
            "AZURE_OPENAI_API_KEY and AZURE_OPENAI_BASE_URL must be set to embed the corpus."
        )
    return AzureEmbedder(
        base_url=settings.azure_openai_base_url,
        api_key=settings.azure_openai_api_key,
        deployment=settings.azure_openai_embedding_deployment,
        dimensions=settings.embedding_dimensions,
    )
