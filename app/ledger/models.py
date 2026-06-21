"""SQLModel tables for the sync ledger.

The ledger is the single source of identity. Every mirrored Notion page maps to
exactly one :class:`SyncPair` row that records where its two facets live on each
side and the last-known content hash of each facet. Echo suppression and drift
detection both read from here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SyncPair(SQLModel, table=True):
    """One Notion item and its Google counterparts (Sheet row + Doc + folder)."""

    __tablename__ = "sync_pairs"

    pair_id: int | None = Field(default=None, primary_key=True)
    # 'area' | 'project' | 'action' | 'page' | 'reference' | 'briefing'
    kind: str = Field(index=True)
    notion_id: str = Field(index=True)
    title: str = ""

    # Structured facet location (the index Sheet).
    gsheet_tab: str | None = None
    gsheet_row_key: str | None = None  # stable key written into a hidden column

    # Body facet location (Drive).
    gdoc_id: str | None = Field(default=None, index=True)
    drive_folder_id: str | None = Field(default=None, index=True)
    drive_parent_id: str | None = None

    # Last-known content hashes per side / facet.
    notion_prop_hash: str | None = None
    notion_body_hash: str | None = None
    g_prop_hash: str | None = None
    g_body_hash: str | None = None

    # Last-seen edit timestamps (ISO strings) used to detect concurrent edits.
    notion_edited: str | None = None
    g_edited: str | None = None

    last_sync_at: datetime | None = None
    tombstone: bool = Field(default=False, index=True)
    tombstoned_at: datetime | None = None
    version: int = 0

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class InflightMarker(SQLModel, table=True):
    """A short-lived "we are about to write this" marker.

    When we propagate a change to a destination we pre-record the hash we are
    about to write. When that write bounces back as an inbound event, its hash
    matches the marker and we drop it (echo suppression, step 5).
    """

    __tablename__ = "inflight_markers"

    id: int | None = Field(default=None, primary_key=True)
    pair_id: int = Field(index=True)
    system: str = Field(index=True)  # 'notion' | 'google'
    facet: str = Field(index=True)  # 'property' | 'body'
    expect_hash: str
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class Conflict(SQLModel, table=True):
    """Record of a concurrent-edit conflict that was resolved Notion-wins."""

    __tablename__ = "conflicts"

    id: int | None = Field(default=None, primary_key=True)
    pair_id: int = Field(index=True)
    facet: str
    kept_system: str = "notion"
    kept_hash: str | None = None
    discarded_value: str | None = None
    detected_at: datetime = Field(default_factory=_utcnow)
