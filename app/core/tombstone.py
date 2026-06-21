"""Deletion / archive propagation via tombstones.

When an item is archived in Notion or trashed in Google we mark its pair with a
tombstone, delete the counterpart on the other side, and keep the ledger row for
a grace period so a slow echo cannot resurrect the item. After the grace period
the nightly reconcile purges the row.
"""

from __future__ import annotations

from sqlmodel import Session

from app.ledger import repo
from app.ledger.models import SyncPair


def tombstone(session: Session, pair: SyncPair) -> None:
    repo.tombstone_pair(session, pair)


def is_tombstoned(pair: SyncPair | None) -> bool:
    return bool(pair and pair.tombstone)


def purge_expired(session: Session, grace_seconds: int) -> int:
    return repo.purge_tombstones(session, grace_seconds)
