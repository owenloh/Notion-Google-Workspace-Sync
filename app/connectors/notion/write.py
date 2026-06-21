"""Writing to Notion: build property payloads, create/update pages, bodies.

The property-schema map encodes the known types of the three core databases so a
Google-side change (a Sheet row, where everything is text) can be turned back
into a correctly typed Notion property payload. ``build_properties`` is pure and
unit-tested.
"""

from __future__ import annotations

from typing import Any

from app.connectors.notion.client import NotionClient
from app.core.markdown import markdown_to_notion_blocks, md_to_rich_text

# kind -> {column name: notion property type}
PROPERTY_SCHEMA: dict[str, dict[str, str]] = {
    "area": {
        "Name": "title",
        "Status": "status",
        "Type": "select",
        "Standards": "rich_text",
        "Projects": "relation",
    },
    "project": {
        "Project": "title",
        "Area": "relation",
        "Direction": "rich_text",
        "Status": "status",
        "Repo": "url",
        "Next actions": "relation",
    },
    "action": {
        "Name": "title",
        "Action Status": "status",
        "Due": "date",
        "Project": "relation",
        "Checkbox": "checkbox",
    },
}

# Columns that carry relations, mapped to how the engine supplies resolved ids.
RELATION_COLUMNS = {
    "area": ["Projects"],
    "project": ["Area", "Next actions"],
    "action": ["Project"],
}


def _one(value: Any) -> str:
    if isinstance(value, list):
        return value[0] if value else ""
    return "" if value is None else str(value)


def build_properties(
    kind: str,
    values: dict[str, Any],
    relation_ids: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build a Notion ``properties`` payload from column values.

    ``values`` holds scalar column values (strings). ``relation_ids`` maps a
    relation column to the resolved Notion page ids it should point at.
    """
    schema = PROPERTY_SCHEMA.get(kind, {})
    relation_ids = relation_ids or {}
    payload: dict[str, Any] = {}

    for column, ptype in schema.items():
        if ptype == "relation":
            ids = relation_ids.get(column, [])
            payload[column] = {"relation": [{"id": i} for i in ids]}
            continue
        if column not in values:
            continue
        raw = values[column]
        if ptype == "title":
            payload[column] = {"title": md_to_rich_text(_one(raw))}
        elif ptype == "rich_text":
            payload[column] = {"rich_text": md_to_rich_text(_one(raw))}
        elif ptype == "status":
            text = _one(raw)
            payload[column] = {"status": {"name": text} if text else None}
        elif ptype == "select":
            text = _one(raw)
            payload[column] = {"select": {"name": text} if text else None}
        elif ptype == "date":
            text = _one(raw)
            payload[column] = {"date": {"start": text} if text else None}
        elif ptype == "checkbox":
            payload[column] = {"checkbox": _truthy(raw)}
        elif ptype == "url":
            payload[column] = {"url": _one(raw) or None}
    return payload


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "x", "✓"}


# --- API-backed helpers ----------------------------------------------------

def create_page(
    client: NotionClient,
    parent: dict,
    properties: dict,
    children: list[dict] | None = None,
) -> dict:
    body = {"parent": parent, "properties": properties}
    if children:
        body["children"] = children
    return client.request("POST", "/pages", json=body)


def update_page_properties(client: NotionClient, page_id: str, properties: dict) -> dict:
    return client.request("PATCH", f"/pages/{page_id}", json={"properties": properties})


def archive_page(client: NotionClient, page_id: str) -> dict:
    return client.request("PATCH", f"/pages/{page_id}", json={"archived": True})


def append_children(client: NotionClient, block_id: str, children: list[dict]) -> dict:
    return client.request(
        "PATCH", f"/blocks/{block_id}/children", json={"children": children}
    )


def replace_body(client: NotionClient, page_id: str, markdown: str) -> None:
    """Replace a page's body with blocks rendered from ``markdown``.

    Notion has no bulk replace: existing non-child-page blocks are deleted, then
    the new blocks are appended. Child pages are preserved (mirrored separately).
    """
    existing = list(client.paginate("GET", f"/blocks/{page_id}/children"))
    for block in existing:
        if block.get("type") == "child_page":
            continue
        client.request("DELETE", f"/blocks/{block['id']}")
    blocks = markdown_to_notion_blocks(markdown)
    # Notion caps children at 100 per request.
    for i in range(0, len(blocks), 100):
        append_children(client, page_id, blocks[i : i + 100])
