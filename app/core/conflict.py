"""Concurrent-edit conflict handling. Policy: **Notion wins**.

A conflict exists when, at the moment we are about to propagate a change, the
*destination* has also changed since the last successful sync (its current hash
differs from the hash we last recorded for it).

* Source Notion → destination Google changed: Notion still wins; we overwrite
  Google and log the discarded Google value.
* Source Google → destination Notion changed: Notion wins; we *drop* the Google
  change and log it. The next mirror-out re-asserts Notion onto Google.
"""

from __future__ import annotations

from sqlmodel import Session

from app.core.echo import _stored_hash
from app.ledger import repo
from app.ledger.models import SyncPair


def destination_changed(
    pair: SyncPair, dest_system: str, facet: str, dest_current_hash: str | None
) -> bool:
    """True if the destination drifted from the last hash we recorded for it."""
    if dest_current_hash is None:
        return False
    return _stored_hash(pair, dest_system, facet) != dest_current_hash


def resolve_notion_wins(
    session: Session,
    pair: SyncPair,
    source_system: str,
    facet: str,
    discarded_value: str | None,
    kept_hash: str | None,
) -> bool:
    """Record a conflict resolved Notion-wins. Returns whether to keep going.

    Returns ``True`` when the caller should still write to the destination
    (source was Notion), ``False`` when the caller should abandon the write
    (source was Google and Notion already holds the winning value).
    """
    repo.record_conflict(
        session,
        pair_id=pair.pair_id,
        facet=facet,
        kept_hash=kept_hash,
        discarded_value=discarded_value,
        kept_system="notion",
    )
    return source_system == "notion"
