"""Scope filter for the incremental reflect (which changed pages we mirror)."""

from app.ledger import repo
from app.scheduler.jobs import _in_scope
from tests.fakes import make_item

LOOSE = {"1fa6f0ccdd76809e8bcbe5db5ae28237"}  # Library hub (undashed)


def test_spine_and_loose_and_tracked_pages_are_in_scope(session):
    # Spine row.
    assert _in_scope(session, make_item("a", "area", "A"), LOOSE)
    # Loose root (matched undashed).
    loose = make_item("1fa6f0cc-dd76-809e-8bcb-e5db5ae28237", "library", "Library")
    assert _in_scope(session, loose, LOOSE)
    # A page we already track.
    repo.upsert_pair(session, "p1", kind="page", title="Tracked", drive_folder_id="F1")
    assert _in_scope(session, make_item("p1", "page", "Tracked"), LOOSE)


def test_child_of_tracked_parent_is_in_scope_but_orphan_is_not(session):
    repo.upsert_pair(session, "parent", kind="page", title="Parent", drive_folder_id="F2")
    child = make_item("child", "page", "Child", parent_id="parent")
    assert _in_scope(session, child, LOOSE)               # parent is tracked → place under it

    orphan = make_item("loner", "page", "Loner", parent_id="unknown")
    assert not _in_scope(session, orphan, LOOSE)          # unknown parent, untracked → skip
