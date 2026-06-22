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

    def request(
        self, method: str, path: str, *, json: dict | None = None,
        params: dict | None = None, version: str | None = None,
    ) -> dict[str, Any]:
        """Issue a request with retry on 429 / 5xx and transient transport/SSL errors.

        ``version`` overrides the ``Notion-Version`` header for this call only (used
        for the block-children fallback on pages that 400 under the pinned version).
        """
        headers = {"Notion-Version": version} if version else None
        delay = 1.0
        last_exc: httpx.TransportError | None = None
        for _attempt in range(5):
            try:
                resp = self._client.request(method, path, json=json, params=params,
                                            headers=headers)
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

    def paginate(
        self, method: str, path: str, *, json: dict | None = None, version: str | None = None
    ) -> Iterator[dict]:
        """Yield every result across paginated endpoints.

        Pagination differs by verb: GET endpoints (e.g. ``/blocks/{id}/children``)
        take ``start_cursor`` as a **query param** — sending it in the body is a 400
        ("body.start_cursor should be not present") — while POST endpoints
        (``/search``, ``/databases/{id}/query``) take it in the **body**.
        """
        is_get = method.upper() == "GET"
        base_body = dict(json or {})
        cursor: str | None = None
        while True:
            if is_get:
                params = {"start_cursor": cursor} if cursor else None
                data = self.request(method, path, params=params, version=version)
            else:
                body = dict(base_body)
                if cursor:
                    body["start_cursor"] = cursor
                data = self.request(method, path, json=body, version=version)
            yield from data.get("results", [])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
