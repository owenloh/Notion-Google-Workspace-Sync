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


def _in_scope(session, item: NotionItem, loose_ids: set[str]) -> bool:
    """Should a changed page be reflected? In scope = already tracked, a spine row,
    a loose root, or a child of a tracked page. Anything else (pages the integration
    can see but we don't mirror) is ignored; the rare full reconcile is the backstop
    for brand-new deep subtrees whose parent isn't tracked yet."""
    if item.kind in {"area", "project", "action"}:
        return True
    nid = item.notion_id.replace("-", "")
    if nid in loose_ids:
        return True
    if repo.get_pair_by_notion_id(session, item.notion_id) is not None:
        return True
    return bool(item.parent_id) and repo.get_pair_by_notion_id(session, item.parent_id) is not None


def poll_incremental(rt: Runtime | None = None) -> int:
    """Reflect every Notion page changed since the watermark — including deep
    sub-pages (via /search by last_edited_time). Replaces the old shallow
    spine/loose delta poll; deep hand-edits now show within one poll cycle."""
    rt = rt or get_runtime()
    if not _MIRROR_LOCK.acquire(blocking=False):
        log.info("poll_incremental skipped (a mirror sync is already running)")
        return 0
    try:
        with session_scope() as session:
            from app.engines.notion_source import LOOSE_PAGES

            loose_ids = {k.replace("-", "") for k in LOOSE_PAGES}
            watermark = repo.get_state(session, _NOTION_WATERMARK) or ""
            changed = rt.notion.changed_since(watermark)
            engine = MirrorOut(session, rt.notion, rt.google, rt.settings)
            done = 0
            for it in changed:
                if not _in_scope(session, it, loose_ids):
                    continue
                try:
                    engine.mirror_item(it)
                    done += 1
                except Exception:  # noqa: BLE001 — per-item isolation
                    log.exception("incremental reflect failed for %s", it.notion_id)
            # Keep the catalog fresh AND catch spine deletions/archives every cycle
            # (cheap, one spine fetch) so Gemini sees current ids/status within ~a
            # poll, not only at the daily reconcile.
            try:
                engine.reconcile_spine()
            except Exception:  # noqa: BLE001 — best-effort
                log.exception("spine reconcile in poll_incremental failed")
            try:
                engine.refresh_intray()
            except Exception:  # noqa: BLE001 — best-effort
                log.exception("intray refresh in poll_incremental failed")
            newest = max((it.last_edited_time or "" for it in changed), default=watermark)
            if newest:
                repo.set_state(session, _NOTION_WATERMARK, newest)
            if done:
                log.info("poll_incremental reflected %d changed page(s)", done)
            return done
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
