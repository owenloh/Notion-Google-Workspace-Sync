"""Delta selection used by the Notion poll."""

from app.scheduler.jobs import select_changed
from tests.fakes import make_item


def test_select_changed_picks_new_and_edited():
    a = make_item("a", "area", "A", last_edited="2026-06-21T10:00:00.000Z")
    b = make_item("b", "area", "B", last_edited="2026-06-21T08:00:00.000Z")
    c = make_item("c", "area", "C", last_edited="2026-06-21T09:30:00.000Z")
    items = [a, b, c]
    # Watermark at 09:00 — only 'a' is newer; 'c' is also new (not yet known).
    changed = select_changed(items, "2026-06-21T09:00:00.000Z", known_ids={"b", "a"})
    ids = {i.notion_id for i in changed}
    assert ids == {"a", "c"}


def test_empty_watermark_selects_all():
    items = [make_item("a", "area", "A"), make_item("b", "area", "B")]
    changed = select_changed(items, "", known_ids={"a", "b"})
    assert len(changed) == 2
