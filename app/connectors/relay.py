"""Guarded relay to the existing Alistair Skills API (the write path).

This service never writes Notion itself; it forwards a validated command to the
user's deployed API. The relay is deliberately **not** a blind proxy:

* only paths in ``RELAY_ALLOWED_PATHS`` may be called (so a command task can never
  reach ``github/push-file`` or a delete);
* ``update-page`` bodies using ``replace_content`` are rejected unless an explicit
  ``force: true`` is present (the body-clobber footgun);
* the response is summarized and the affected Notion page id/url is extracted so
  the executor can re-reflect just that item.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings
from app.engines.command_schema import RelayRequest
from app.logging import get_logger

log = get_logger(__name__)


@dataclass
class RelayResult:
    ok: bool
    status: int
    summary: str
    affected_id: str | None = None


def _extract_affected_id(data: object) -> str | None:
    """Best-effort pull of a Notion page id/url from a response body."""
    if isinstance(data, dict):
        for key in ("id", "page_id", "notion_id"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        for key in ("url", "page_url"):
            val = data.get(key)
            if isinstance(val, str) and "notion.so" in val:
                return val.rstrip("/").split("/")[-1].split("-")[-1]
        for nested in ("page", "result", "data"):
            found = _extract_affected_id(data.get(nested))
            if found:
                return found
    return None


class RelayClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._client = client or httpx.Client(
            base_url=self.settings.relay_api_base_url,
            headers={"X-API-Key": self.settings.relay_api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _guard(self, req: RelayRequest) -> str | None:
        """Return an error message if the request is not permitted, else None."""
        allowed = self.settings.allowed_relay_paths
        if req.path not in allowed:
            return f"path '{req.path}' is not allowed (permitted: {', '.join(allowed)})"
        if "update-page" in req.path:
            cmd = req.body.get("command") or req.body.get("operation")
            if cmd == "replace_content" and not req.body.get("force"):
                return "replace_content is blocked (set force:true to override)"
        return None

    def execute(self, req: RelayRequest) -> RelayResult:
        blocked = self._guard(req)
        if blocked:
            return RelayResult(ok=False, status=0, summary=blocked)
        try:
            resp = self._client.request(req.method, req.path, json=req.body)
        except httpx.HTTPError as exc:
            return RelayResult(ok=False, status=0, summary=f"relay request failed: {exc}")

        data: object = None
        if resp.content:
            try:
                data = resp.json()
            except ValueError:
                data = resp.text

        if resp.is_success:
            affected = _extract_affected_id(data)
            return RelayResult(
                ok=True, status=resp.status_code,
                summary=f"{req.method} {req.path} → {resp.status_code}",
                affected_id=affected,
            )
        detail = data if isinstance(data, str) else str(data)
        return RelayResult(
            ok=False, status=resp.status_code,
            summary=f"{req.method} {req.path} → {resp.status_code}: {detail[:300]}",
        )
