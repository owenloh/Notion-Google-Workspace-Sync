"""Scheduled sync jobs: command inbox poll, Notion delta poll, full reconcile."""

from __future__ import annotations

import threading

from app.connectors.notion.read import NotionItem
from app.core.tombstone import purge_expired
from app.engines.commands import CommandExecutor
from app.engines.mirror_out import MirrorOut
from app.ledger import repo
from app.ledger.db import session_scope
from app.logging import get_logger
from app.runtime import Runtime, get_runtime

log = get_logger(__name__)

_NOTION_WATERMARK = "notion_watermark"

# Mirror jobs share one Google client + one ledger + the per-sync sheet cache, so
# they must NOT run concurrently (httplib2 isn't thread-safe; the cache would
# race). full_reconcile takes the lock (blocking); the periodic polls skip a
# cycle if a sync is already running.
_MIRROR_LOCK = threading.Lock()


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
    if not _MIRROR_LOCK.acquire(blocking=False):
        log.info("poll_notion skipped (a mirror sync is already running)")
        return 0
    try:
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
    finally:
        _MIRROR_LOCK.release()


def poll_commands(rt: Runtime | None = None) -> int:
    """Execute pending command tasks (Google Tasks → relay → Notion). Returns count."""
    rt = rt or get_runtime()
    # Commands re-reflect affected pages (mirror_item), so they share the mirror
    # path; skip if a full sync is running and pick them up next cycle.
    if not _MIRROR_LOCK.acquire(blocking=False):
        log.info("poll_commands skipped (a mirror sync is already running)")
        return 0
    try:
        with session_scope() as session:
            n = CommandExecutor(
                session, rt.notion, rt.google, rt.relay, rt.settings
            ).run_pending()
            if n:
                log.info("poll_commands handled %d command(s)", n)
            return n
    finally:
        _MIRROR_LOCK.release()


def full_reconcile(rt: Runtime | None = None) -> dict[str, int]:
    """Full re-crawl (catches deep child-page edits), then sweep stale state.

    Runs on ``full_sync_seconds`` (default 30 min) and is also what the on-demand
    ``/admin/full-sync`` endpoint invokes. ``sync_all`` recurses into every child
    page and skips unchanged content, so this is safe to run frequently. Holds the
    mirror lock so the periodic polls don't run concurrently (shared client/cache).
    """
    rt = rt or get_runtime()
    with _MIRROR_LOCK, session_scope() as session:
        counts = MirrorOut(session, rt.notion, rt.google, rt.settings).sync_all()
        repo.purge_expired_inflight(session)
        purged = purge_expired(session, rt.settings.tombstone_grace_seconds)
        log.info("full_reconcile done %s; purged %d tombstone(s)", counts, purged)
        return counts
