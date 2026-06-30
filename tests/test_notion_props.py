"""Notion property extraction and payload building."""

from app.connectors.notion.read import extract_properties, extract_title, page_to_item
from app.connectors.notion.write import build_properties
from app.core.canonical import property_projection


def _action_page():
    return {
        "id": "page-1",
        "archived": False,
        "parent": {"type": "data_source_id", "data_source_id": "1d3eb1dd28034692a4d56ca9709ae570"},
        "last_edited_time": "2026-06-21T09:00:00.000Z",
        "last_edited_by": {"id": "user-1"},
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Email Bob"}]},
            "Action Status": {"type": "status", "status": {"name": "Next"}},
            "Due": {"type": "date", "date": {"start": "2026-06-25"}},
            "Project": {"type": "relation", "relation": [{"id": "proj-9"}]},
        },
    }


def test_extract_title_and_properties():
    page = _action_page()
    assert extract_title(page["properties"]) == "Email Bob"
    scalars, relations = extract_properties(page["properties"])
    assert scalars["Name"] == "Email Bob"
    assert scalars["Action Status"] == "Next"
    assert scalars["Due"] == "2026-06-25"
    assert relations["Project"] == ["proj-9"]


def test_page_to_item_classifies_kind():
    # Legacy data-source id in parent still classifies (back-compat).
    item = page_to_item(_action_page())
    assert item.kind == "action"
    assert item.title == "Email Bob"
    assert item.last_edited_by == "user-1"


def test_page_to_item_classifies_by_database_id():
    # A row's parent may report the DATABASE id (e.g. on re-reflect/child crawl);
    # classification must still resolve the kind.
    page = _action_page()
    page["parent"] = {"type": "database_id", "database_id": "2ebc58c5861747488021fcc2a37d3a97"}
    assert page_to_item(page).kind == "action"


def test_build_properties_shapes_payload():
    payload = build_properties(
        "action",
        {"Name": "Email Bob", "Action Status": "Next", "Due": "2026-06-25"},
        relation_ids={"Project": ["proj-9"]},
    )
    assert payload["Name"]["title"][0]["text"]["content"] == "Email Bob"
    assert payload["Action Status"]["status"]["name"] == "Next"
    assert payload["Due"]["date"]["start"] == "2026-06-25"
    assert payload["Project"]["relation"] == [{"id": "proj-9"}]


def test_empty_status_becomes_null():
    payload = build_properties("action", {"Name": "x", "Action Status": ""})
    assert payload["Action Status"]["status"] is None


def test_extract_build_projection_consistency():
    """A Notion page and a Sheet-style value dict for the same content must hash
    to the same property projection (relations compared by name)."""
    page = _action_page()
    scalars, _ = extract_properties(page["properties"])
    # Notion side resolves the relation id to its name for projection.
    notion_props = {**scalars, "Project": ["PourDynamics engine"]}
    notion_proj = property_projection("action", notion_props)

    # Sheet side: same values as a row would hold.
    sheet_proj = property_projection(
        "action",
        {
            "Name": "Email Bob",
            "Action Status": "Next",
            "Due": "2026-06-25",
            "Project": "PourDynamics engine",
        },
    )
    assert notion_proj == sheet_proj
