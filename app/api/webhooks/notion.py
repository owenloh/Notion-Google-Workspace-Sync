"""Notion webhook endpoint.

Handles the one-time verification handshake and verifies the HMAC-SHA256
signature on every subsequent event before doing any work. Event processing is
handed to a background task so we always acknowledge within Notion's timeout.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Header, Request, Response

from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of ``X-Notion-Signature`` (``sha256=<hex>``)."""
    if not secret or not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def extract_page_ids(event: dict) -> list[str]:
    """Pull affected page ids out of a Notion webhook event payload."""
    entity = event.get("entity", {})
    ids: list[str] = []
    if entity.get("type") == "page" and entity.get("id"):
        ids.append(entity["id"])
    # Some payloads nest the affected ids under data.
    for item in (event.get("data", {}) or {}).get("updated_blocks", []) or []:
        if item.get("id"):
            ids.append(item["id"])
    return ids


@router.post("/webhooks/notion")
async def notion_webhook(
    request: Request,
    background: BackgroundTasks,
    x_notion_signature: str | None = Header(default=None),
) -> Response:
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return Response(status_code=400)

    # One-time verification handshake: Notion posts a token to confirm the URL.
    if "verification_token" in payload:
        log.warning(
            "Notion verification_token received — set NOTION_WEBHOOK_SECRET to: %s",
            payload["verification_token"],
        )
        return Response(status_code=200)

    settings = get_settings()
    if not verify_signature(settings.notion_webhook_secret, raw, x_notion_signature):
        log.warning("Rejected Notion webhook: bad signature")
        return Response(status_code=401)

    for page_id in extract_page_ids(payload):
        background.add_task(_process_page, page_id)
    return Response(status_code=200)


def _process_page(page_id: str) -> None:
    """Mirror a single changed page (imported lazily to avoid import cycles)."""
    from app.engines.mirror_out import MirrorOut
    from app.ledger.db import session_scope
    from app.runtime import get_runtime

    rt = get_runtime()
    try:
        item = rt.notion.get_item(page_id)
    except Exception:  # noqa: BLE001 — a deleted/inaccessible page is not fatal
        log.exception("Failed to fetch page %s from webhook", page_id)
        return
    with session_scope() as session:
        MirrorOut(session, rt.notion, rt.google, rt.settings).mirror_item(item)
