"""Conflict resolution (Notion wins) and tombstone propagation."""

from datetime import UTC, datetime, timedelta

from app.core import conflict, tombstone
from app.ledger import repo
from app.ledger.models import Conflict


def _pair(session, **kw):
    return repo.upsert_pair(session, notion_id="n1", kind="project", **kw)


def test_destination_changed_detects_drift(session):
    pair = _pair(session, g_prop_hash="g_old")
    assert conflict.destination_changed(pair, "google", "property", "g_new") is True
    assert conflict.destination_changed(pair, "google", "property", "g_old") is False
    assert conflict.destination_changed(pair, "google", "property", None) is False


def test_notion_source_keeps_writing_and_logs(session):
    pair = _pair(session, g_prop_hash="g_old")
    keep_going = conflict.resolve_notion_wins(
        session, pair, source_system="notion", facet="property",
        discarded_value="google value", kept_hash="n_hash",
    )
    assert keep_going is True
    # Conflict row was recorded.
    from sqlmodel import select
    logged = session.exec(select(Conflict)).all()
    assert len(logged) == 1
    assert logged[0].kept_system == "notion"


def test_google_source_abandons_write(session):
    pair = _pair(session, notion_prop_hash="n_cur")
    keep_going = conflict.resolve_notion_wins(
        session, pair, source_system="google", facet="property",
        discarded_value="google value", kept_hash="n_cur",
    )
    assert keep_going is False


def test_tombstone_marks_and_purges_after_grace(session):
    pair = _pair(session)
    tombstone.tombstone(session, pair)
    assert tombstone.is_tombstoned(pair)

    # Not purged within grace.
    assert tombstone.purge_expired(session, grace_seconds=86400) == 0

    # Backdate the tombstone and purge.
    pair.tombstoned_at = datetime.now(UTC) - timedelta(days=2)
    session.add(pair)
    session.commit()
    assert tombstone.purge_expired(session, grace_seconds=86400) == 1
    assert repo.get_pair_by_notion_id(session, "n1") is None
