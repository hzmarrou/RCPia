"""LlamaParse Cloud parser.

The v1 default, because it was already validated on real dossiers. The API is
asynchronous: upload returns a job id, the job is polled until it finishes, and the
result is fetched as JSON so page numbers and structure survive — markdown alone would
lose the provenance that extracted facts must cite.

Sending dossiers here means sending them outside the hospital; see
docs/transparency/data-handling.md for the decision and its limits.
"""

import asyncio
import time
from typing import Any

import httpx

from rcpia.parsing.base import (
    BlockType,
    ParsedBlock,
    ParsedDocument,
    ParseRequest,
    ParserFailed,
    ParserNotConfigured,
)

DEFAULT_BASE_URL = "https://api.cloud.llamaindex.ai/api/v1/parsing"
POLL_INTERVAL_SECONDS = 2.0
# Long dossiers with imaging reports take a while; the parsing job itself has a 15-minute
# lease, so stop before it lapses.
DEFAULT_TIMEOUT_SECONDS = 600.0

ITEM_TYPE_TO_BLOCK_TYPE: dict[str, BlockType] = {
    "text": "paragraph",
    "heading": "heading",
    "table": "table",
    "image": "image",
}


class LlamaParseParser:
    name = "llamaparse"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise ParserNotConfigured(
                "PARSER_PROVIDER=llamaparse requires LLAMAPARSE_API_KEY to be set"
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._poll_interval = poll_interval
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        if self._client is not None:
            return await self._parse_with(self._client, request)
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._parse_with(client, request)

    async def _parse_with(
        self, client: httpx.AsyncClient, request: ParseRequest
    ) -> ParsedDocument:
        job_id = await self._upload(client, request)
        await self._await_completion(client, job_id)
        payload = await self._fetch_result(client, job_id)
        return self._to_document(payload)

    async def _upload(self, client: httpx.AsyncClient, request: ParseRequest) -> str:
        try:
            response = await client.post(
                f"{self._base_url}/upload",
                headers=self._headers(),
                files={"file": (request.filename, request.content, request.content_type)},
                data={"language": request.language},
            )
        except httpx.HTTPError as exc:
            raise ParserFailed(f"LlamaParse est injoignable : {exc}") from exc

        if response.status_code in (401, 403):
            raise ParserNotConfigured("LlamaParse a refusé la clé d'API (LLAMAPARSE_API_KEY).")
        if response.status_code >= 400:
            raise ParserFailed(
                f"LlamaParse a refusé le document (HTTP {response.status_code})."
            )

        job_id = response.json().get("id")
        if not job_id:
            raise ParserFailed("Réponse LlamaParse inattendue : identifiant de tâche absent.")
        return str(job_id)

    async def _await_completion(self, client: httpx.AsyncClient, job_id: str) -> None:
        # Wall clock, not accumulated sleeps: with a zero or very small poll interval the
        # accumulated version would spin without ever reaching the deadline.
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            try:
                response = await client.get(
                    f"{self._base_url}/job/{job_id}", headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise ParserFailed(f"LlamaParse est injoignable : {exc}") from exc

            if response.status_code >= 400:
                raise ParserFailed(
                    f"Suivi de la tâche LlamaParse impossible (HTTP {response.status_code})."
                )

            status = str(response.json().get("status", "")).upper()
            if status in ("SUCCESS", "COMPLETED", "PARTIAL_SUCCESS"):
                return
            if status in ("ERROR", "FAILED", "CANCELLED"):
                raise ParserFailed(f"LlamaParse a échoué sur ce document (statut {status}).")

            if time.monotonic() >= deadline:
                raise ParserFailed(
                    f"LlamaParse n'a pas terminé dans le délai imparti "
                    f"({int(self._timeout_seconds)} s)."
                )
            await asyncio.sleep(self._poll_interval)

    async def _fetch_result(self, client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{self._base_url}/job/{job_id}/result/json", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise ParserFailed(f"LlamaParse est injoignable : {exc}") from exc

        if response.status_code >= 400:
            raise ParserFailed(
                f"Résultat LlamaParse indisponible (HTTP {response.status_code})."
            )
        return response.json()

    def _to_document(self, payload: dict[str, Any]) -> ParsedDocument:
        """Flatten the provider's page/item structure into blocks that keep their page.

        Headings are remembered as the current section, so a value extracted three
        paragraphs later still cites the part of the dossier it came from.
        """
        blocks: list[ParsedBlock] = []
        raw_pages: list[str] = []
        warnings: list[str] = list(payload.get("warnings") or [])

        for page in payload.get("pages") or []:
            page_number = page.get("page")
            section: str | None = None
            page_text = page.get("text") or page.get("md") or ""
            if page_text:
                raw_pages.append(str(page_text))

            for item in page.get("items") or []:
                item_type = str(item.get("type", "")).lower()
                text = item.get("value") or item.get("md") or ""
                if item_type == "table" and not text:
                    text = self._table_to_text(item)
                if not str(text).strip():
                    continue

                if item_type == "heading":
                    section = str(text).strip()

                blocks.append(
                    ParsedBlock(
                        text=str(text),
                        page=int(page_number) if isinstance(page_number, int) else None,
                        section=section,
                        bbox=self._bbox(item),
                        block_type=ITEM_TYPE_TO_BLOCK_TYPE.get(item_type, "unknown"),
                    )
                )

        metadata = payload.get("job_metadata") or {}
        version = metadata.get("job_version")
        return ParsedDocument(
            provider=self.name,
            provider_version=str(version) if version else None,
            blocks=blocks,
            raw_text="\n\n".join(raw_pages),
            warnings=warnings,
        )

    @staticmethod
    def _table_to_text(item: dict[str, Any]) -> str:
        rows = item.get("rows") or []
        return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)

    @staticmethod
    def _bbox(item: dict[str, Any]) -> list[float] | None:
        raw = item.get("bBox") or item.get("bbox")
        if isinstance(raw, dict):
            keys = ("x", "y", "w", "h")
            if all(key in raw for key in keys):
                return [float(raw[key]) for key in keys]
            return None
        if isinstance(raw, list) and len(raw) == 4:
            try:
                return [float(value) for value in raw]
            except (TypeError, ValueError):
                return None
        return None
