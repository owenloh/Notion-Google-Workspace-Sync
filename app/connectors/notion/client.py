"""Thin Notion REST client (httpx).

Wraps auth headers, the API version, pagination, and basic retry/backoff. Higher
level read/write helpers build on top of this.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.logging import get_logger

log = get_logger(__name__)


class NotionClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._client = client or httpx.Client(
            base_url=self.settings.notion_api_base,
            headers={
                "Authorization": f"Bearer {self.settings.notion_api_token}",
                "Notion-Version": self.settings.notion_version,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, *, json: dict | None = None) -> dict[str, Any]:
        """Issue a request with retry on 429 / 5xx and transient transport/SSL errors."""
        delay = 1.0
        last_exc: httpx.TransportError | None = None
        for _attempt in range(5):
            try:
                resp = self._client.request(method, path, json=json)
            except httpx.TransportError as exc:  # connect/read/SSL blips
                last_exc = exc
                log.warning(
                    "Notion %s %s transport error: %s; retry in %.1fs", method, path, exc, delay
                )
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = float(resp.headers.get("Retry-After", delay))
                log.warning(
                    "Notion %s %s -> %s, retry in %.1fs",
                    method, path, resp.status_code, retry_after,
                )
                time.sleep(retry_after)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        if last_exc is not None:
            raise last_exc
        resp.raise_for_status()
        return {}

    def paginate(self, method: str, path: str, *, json: dict | None = None) -> Iterator[dict]:
        """Yield every result across paginated endpoints (start_cursor)."""
        payload = dict(json or {})
        while True:
            data = self.request(method, path, json=payload)
            yield from data.get("results", [])
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data.get("next_cursor")
