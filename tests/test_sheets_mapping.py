"""Pure row<->record mapping for the index sheet tabs."""

from app.connectors.google.sheets import (
    TAB_COLUMNS,
    hyperlink,
    record_to_row,
    row_to_record,
    split_relation,
)


def test_record_to_row_orders_columns():
    rec = {
        "Name": "Email Bob",
        "Action Status": "Next",
        "Due": "2026-06-25",
        "Project": ["PourDynamics engine"],
        "Checkbox": "false",
        "Doc": "",
        "_notion_id": "abc",
        "_last_edited": "2026-06-21",
        "_hash": "deadbeef",
    }
    row = record_to_row("Actions", rec)
    assert row == [
        "Email Bob", "Next", "2026-06-25", "PourDynamics engine", "false",
        "", "abc", "2026-06-21", "deadbeef",
    ]


def test_row_to_record_pads_missing_cells():
    rec = row_to_record("Actions", ["Email Bob", "Next"])
    assert rec["Name"] == "Email Bob"
    assert rec["Action Status"] == "Next"
    assert rec["_notion_id"] == ""
    assert set(rec) == set(TAB_COLUMNS["Actions"])


def test_roundtrip_record_row():
    rec = {col: f"v{i}" for i, col in enumerate(TAB_COLUMNS["Projects"])}
    back = row_to_record("Projects", record_to_row("Projects", rec))
    assert back == rec


def test_hyperlink_formula():
    f = hyperlink("https://docs.google.com/document/d/X/edit")
    assert f.startswith("=HYPERLINK(")
    assert "X/edit" in f


def test_split_relation():
    assert split_relation("Career, Health") == ["Career", "Health"]
    assert split_relation("") == []
