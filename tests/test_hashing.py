"""Cross-side hash equality: the same logical content hashes identically whether
it came from Notion page properties or a Google Sheet row."""

from app.core.hashing import body_hash, property_hash


def test_property_hash_is_field_order_invariant():
    a = property_hash("action", {"Name": "Email Bob", "Action Status": "Next"})
    b = property_hash("action", {"Action Status": "Next", "Name": "Email Bob"})
    assert a == b


def test_property_hash_ignores_bookkeeping_and_doc_columns():
    notion_side = property_hash("project", {"Project": "Engine", "Status": "Active"})
    sheet_side = property_hash(
        "project",
        {
            "Project": "Engine",
            "Status": "Active",
            "Doc": "=HYPERLINK(...)",
            "_notion_id": "abc",
            "_hash": "deadbeef",
        },
    )
    assert notion_side == sheet_side


def test_property_hash_normalizes_whitespace_and_relations():
    notion_side = property_hash(
        "project",
        {"Project": "Engine", "Area": ["Career", "Health"]},
    )
    # Sheet stores relations comma-joined, possibly reordered, with stray spaces.
    sheet_side = property_hash(
        "project",
        {"Project": " Engine ", "Area": "Health, Career"},
    )
    assert notion_side == sheet_side


def test_property_hash_distinguishes_real_changes():
    a = property_hash("action", {"Name": "Email Bob", "Action Status": "Next"})
    b = property_hash("action", {"Name": "Email Bob", "Action Status": "Done"})
    assert a != b


def test_due_date_is_date_only():
    a = property_hash("action", {"Name": "x", "Due": "2026-06-21"})
    b = property_hash("action", {"Name": "x", "Due": "2026-06-21T09:00:00.000+00:00"})
    assert a == b


def test_body_hash_ignores_trailing_whitespace_and_blank_runs():
    a = body_hash("# Title\n\nHello world")
    b = body_hash("# Title   \n\n\n\nHello world   \n")
    assert a == b
