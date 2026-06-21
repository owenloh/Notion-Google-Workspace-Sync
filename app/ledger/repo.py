"""Repository helpers over the ledger tables.

All ledger access goes through these functions so the storage details stay in one
place and the engines/echo pipeline read like plain domain operations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.ledger.models import Conflict, InflightMarker, SyncPair


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- SyncPair lookups ------------------------------------------------------

def get_pair_by_notion_id(session: Session, notion_id: str) -> SyncPair | None:
    return session.exec(
        select(SyncPair).where(SyncPair.notion_id == notion_id)
    ).first()


def get_pair_by_gdoc_id(session: Session, gdoc_id: str) -> SyncPair | None:
    return session.exec(select(SyncPair).where(SyncPair.gdoc_id == gdoc_id)).first()


def get_pair_by_drive_folder(session: Session, folder_id: str) -> SyncPair | None:
    return session.exec(
        select(SyncPair).where(SyncPair.drive_folder_id == folder_id)
    ).first()


def get_pair_by_row_key(session: Session, tab: str, row_key: str) -> SyncPair | None:
    return session.exec(
        select(SyncPair)
        .where(SyncPair.gsheet_tab == tab)
        .where(SyncPair.gsheet_row_key == row_key)
    ).first()


def get_pair(session: Session, pair_id: int) -> SyncPair | None:
    return session.get(SyncPair, pair_id)


def all_pairs(
    session: Session, kind: str | None = None, include_tombstoned: bool = False
) -> list[SyncPair]:
    stmt = select(SyncPair)
    if kind is not None:
        stmt = stmt.where(SyncPair.kind == kind)
    if not include_tombstoned:
        stmt = stmt.where(SyncPair.tombstone == False)  # noqa: E712
    return list(session.exec(stmt).all())


# --- SyncPair mutations ----------------------------------------------------

def upsert_pair(session: Session, notion_id: str, **fields) -> SyncPair:
    """Create or update the pair keyed by ``notion_id``.

    ``kind`` is required when creating a new pair.
    """
    pair = get_pair_by_notion_id(session, notion_id)
    if pair is None:
        pair = SyncPair(notion_id=notion_id, kind=fields.get("kind", "page"))
        session.add(pair)
    for key, value in fields.items():
        setattr(pair, key, value)
    pair.updated_at = _utcnow()
    session.add(pair)
    session.commit()
    session.refresh(pair)
    return pair


def tombstone_pair(session: Session, pair: SyncPair) -> None:
    pair.tombstone = True
    pair.tombstoned_at = _utcnow()
    pair.updated_at = _utcnow()
    session.add(pair)
    session.commit()


def purge_tombstones(session: Session, grace_seconds: int) -> int:
    """Delete pairs tombstoned longer than ``grace_seconds`` ago. Returns count."""
    cutoff = _utcnow() - timedelta(seconds=grace_seconds)
    stale = session.exec(
        select(SyncPair)
        .where(SyncPair.tombstone == True)  # noqa: E712
        .where(SyncPair.tombstoned_at != None)  # noqa: E711
    ).all()
    n = 0
    for pair in stale:
        ts = pair.tombstoned_at
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts is not None and ts < cutoff:
            session.delete(pair)
            n += 1
    if n:
        session.commit()
    return n


# --- Inflight markers (echo suppression) -----------------------------------

def set_inflight(
    session: Session,
    pair_id: int,
    system: str,
    facet: str,
    expect_hash: str,
    ttl_seconds: int,
) -> None:
    """Record that we are about to write ``expect_hash`` to ``system``/``facet``."""
    marker = InflightMarker(
        pair_id=pair_id,
        system=system,
        facet=facet,
        expect_hash=expect_hash,
        expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
    )
    session.add(marker)
    session.commit()


def consume_inflight(
    session: Session, pair_id: int, system: str, facet: str, incoming_hash: str
) -> bool:
    """Return True (and delete the marker) if an inbound event is our own echo.

    Matches on ``pair_id``/``system``/``facet``/``hash`` and that the marker has
    not expired. Expired markers are swept here too.
    """
    now = _utcnow()
    markers = session.exec(
        select(InflightMarker)
        .where(InflightMarker.pair_id == pair_id)
        .where(InflightMarker.system == system)
        .where(InflightMarker.facet == facet)
    ).all()
    matched = False
    for m in markers:
        expires = m.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            session.delete(m)
            continue
        if m.expect_hash == incoming_hash:
            session.delete(m)
            matched = True
            break
    session.commit()
    return matched


def purge_expired_inflight(session: Session) -> int:
    now = _utcnow()
    markers = session.exec(select(InflightMarker)).all()
    n = 0
    for m in markers:
        expires = m.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            session.delete(m)
            n += 1
    if n:
        session.commit()
    return n


# --- Conflicts -------------------------------------------------------------

def record_conflict(
    session: Session,
    pair_id: int,
    facet: str,
    kept_hash: str | None,
    discarded_value: str | None,
    kept_system: str = "notion",
) -> None:
    session.add(
        Conflict(
            pair_id=pair_id,
            facet=facet,
            kept_system=kept_system,
            kept_hash=kept_hash,
            discarded_value=discarded_value,
        )
    )
    session.commit()
