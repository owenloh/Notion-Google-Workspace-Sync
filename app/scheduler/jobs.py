"""Scheduled sync jobs: Notion poll, Google poll, nightly reconcile."""

from __future__ import annotations

from app.connectors.notion.read import NotionItem
from app.core.tombstone import purge_expired
from app.engines.mirror_in import MirrorIn
from app.engines.mirror_out import MirrorOut
from app.ledger import repo
from app.ledger.db import session_scope
from app.logging import get_logger
from app.runtime import Runtime, get_runtime

log = get_logger(__name__)

_NOTION_WATERMARK = "notion_watermark"
_DRIVE_TOKEN = "drive_page_token"


def select_changed(
    items: list[NotionItem], watermark: str, known_ids: set[str]
) -> list[NotionItem]:
    """Items edited after the watermark, or not yet mirrored. Pure/testable."""
    out = []
    for it in items:
        edited = it.last_edited_time or ""
        if it.notion_id not in known_ids or (watermark and edited > watermark) or not watermark:
            out.append(it)
    return out


def poll_notion(rt: Runtime | None = None) -> int:
    """Mirror Notion items changed since the last watermark. Returns count."""
    rt = rt or get_runtime()
    with session_scope() as session:
        watermark = repo.get_state(session, _NOTION_WATERMARK)
        known = {p.notion_id for p in repo.all_pairs(session, include_tombstoned=True)}
        items = rt.notion.spine_items() + rt.notion.loose_items()
        changed = select_changed(items, watermark, known)
        engine = MirrorOut(session, rt.notion, rt.google, rt.settings)
        for it in changed:
            engine.mirror_item(it)
        newest = max((it.last_edited_time or "" for it in items), default=watermark)
        if newest:
            repo.set_state(session, _NOTION_WATERMARK, newest)
        if changed:
            log.info("poll_notion mirrored %d changed item(s)", len(changed))
        return len(changed)


def poll_google(rt: Runtime | None = None) -> int:
    """Pull Sheet edits and Drive changes into Notion. Returns count propagated."""
    from app.connectors.google import drive as gdrive

    rt = rt or get_runtime()
    with session_scope() as session:
        engine = MirrorIn(session, rt.notion, rt.google, rt.settings)
        n = engine.sync_sheets()

        token = repo.get_state(session, _DRIVE_TOKEN)
        if not token:
            token = gdrive.get_start_page_token(rt.google.services.drive)
        changes, new_token = gdrive.list_changes(rt.google.services.drive, token)
        n += engine.sync_drive(changes)
        repo.set_state(session, _DRIVE_TOKEN, new_token)
        if n:
            log.info("poll_google propagated %d change(s)", n)
        return n


def full_reconcile(rt: Runtime | None = None) -> dict[str, int]:
    """Full re-crawl (catches deep child-page edits), then sweep stale state.

    Runs on ``full_sync_seconds`` (default 30 min) and is also what the on-demand
    ``/admin/full-sync`` endpoint invokes. ``sync_all`` recurses into every child
    page and skips unchanged content, so this is safe to run frequently.
    """
    rt = rt or get_runtime()
    with session_scope() as session:
        counts = MirrorOut(session, rt.notion, rt.google, rt.settings).sync_all()
        repo.purge_expired_inflight(session)
        purged = purge_expired(session, rt.settings.tombstone_grace_seconds)
        log.info("full_reconcile done %s; purged %d tombstone(s)", counts, purged)
        return counts
