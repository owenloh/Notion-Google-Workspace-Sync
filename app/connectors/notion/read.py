"""Reading from Notion: data-source queries, page properties, and block bodies.

The property-extraction helpers are pure (they take a Notion API page dict) so
they can be unit-tested without the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import ACTIONS_DS_ID, AREAS_DS_ID, LEGACY_SPINE_DS_IDS, PROJECTS_DS_ID
from app.connectors.notion.client import NotionClient
from app.core.markdown import notion_blocks_to_markdown
from app.logging import get_logger

log = get_logger(__name__)


@dataclass
class NotionItem:
    """A page reduced to what the sync needs."""

    notion_id: str
    kind: str  # 'area' | 'project' | 'action' | 'page' | 'reference' | 'briefing'
    title: str
    properties: dict[str, Any]  # scalar/list property values (relations as names TBD)
    relations: dict[str, list[str]]  # prop name -> related page ids
    parent_id: str | None
    parent_type: str | None
    last_edited_time: str | None
    last_edited_by: str | None
    archived: bool = False
    children: list[NotionItem] = field(default_factory=list)


def _rich_text_plain(rt: list[dict]) -> str:
    return "".join(span.get("plain_text", "") for span in rt or [])


def extract_title(props: dict) -> str:
    for value in props.values():
        if value.get("type") == "title":
            return _rich_text_plain(value.get("title", []))
    return ""


def extract_properties(props: dict) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Reduce a Notion ``properties`` object to (scalars, relations).

    ``scalars`` maps property name → a plain value (str / list / bool / number /
    date string). ``relations`` maps property name → list of related page ids,
    kept separate so the engine can resolve them to names against the ledger.
    """
    scalars: dict[str, Any] = {}
    relations: dict[str, list[str]] = {}
    for name, value in props.items():
        t = value.get("type")
        if t == "title":
            scalars[name] = _rich_text_plain(value.get("title", []))
        elif t == "rich_text":
            scalars[name] = _rich_text_plain(value.get("rich_text", []))
        elif t in {"select", "status"}:
            inner = value.get(t)
            scalars[name] = inner.get("name", "") if inner else ""
        elif t == "multi_select":
            scalars[name] = [o.get("name", "") for o in value.get("multi_select", [])]
        elif t == "date":
            d = value.get("date")
            scalars[name] = d.get("start", "") if d else ""
        elif t == "checkbox":
            scalars[name] = bool(value.get("checkbox"))
        elif t == "url":
            scalars[name] = value.get("url") or ""
        elif t == "number":
            scalars[name] = value.get("number")
        elif t == "relation":
            relations[name] = [r.get("id") for r in value.get("relation", [])]
        # created_time / last_edited_time / people / formula are ignored for hashing.
    return scalars, relations


def _norm_id(notion_id: str | None) -> str:
    return (notion_id or "").replace("-", "")


# Keyed by dash-stripped id so lookups are robust to formatting differences.
# Includes both the database ids (what we query) and the legacy data-source ids,
# since a page's parent may report either depending on the API version.
_DS_KIND = {
    _norm_id(AREAS_DS_ID): "area",
    _norm_id(PROJECTS_DS_ID): "project",
    _norm_id(ACTIONS_DS_ID): "action",
    **{_norm_id(ds): kind for ds, kind in LEGACY_SPINE_DS_IDS.items()},
}


def kind_for_parent(parent: dict) -> str:
    """Classify a page by its parent data-source/database, else generic page."""
    if parent.get("type") in {"database_id", "data_source_id"}:
        ds = parent.get("database_id") or parent.get("data_source_id")
        return _DS_KIND.get(_norm_id(ds), "page") if ds else "page"
    return "page"


def page_to_item(page: dict, kind: str | None = None) -> NotionItem:
    parent = page.get("parent", {})
    scalars, relations = extract_properties(page.get("properties", {}))
    return NotionItem(
        notion_id=page["id"],
        kind=kind or kind_for_parent(parent),
        title=extract_title(page.get("properties", {})),
        properties=scalars,
        relations=relations,
        parent_id=parent.get("database_id")
        or parent.get("data_source_id")
        or parent.get("page_id")
        or parent.get("block_id"),
        parent_type=parent.get("type"),
        last_edited_time=page.get("last_edited_time"),
        last_edited_by=(page.get("last_edited_by") or {}).get("id"),
        archived=page.get("archived", False) or page.get("in_trash", False),
    )


# --- API-backed helpers ----------------------------------------------------

def query_data_source(client: NotionClient, data_source_id: str) -> list[NotionItem]:
    """Return all pages of a database/data source as NotionItems."""
    kind = _DS_KIND.get(_norm_id(data_source_id), "page")
    items = []
    for page in client.paginate("POST", f"/databases/{data_source_id}/query"):
        items.append(page_to_item(page, kind=kind))
    return items


def get_page(client: NotionClient, page_id: str) -> NotionItem:
    page = client.request("GET", f"/pages/{page_id}")
    return page_to_item(page)


def search_pages_changed_since(client: NotionClient, since: str) -> list[NotionItem]:
    """Pages edited at/after ``since`` (ISO), via /search sorted by last_edited_time.

    Unlike a spine query, this surfaces *any* changed page the integration can see,
    including deep sub-pages (each page bumps its own last_edited_time). Results are
    newest-first, so we stop once we pass the watermark.
    """
    body = {
        "sort": {"timestamp": "last_edited_time", "direction": "descending"},
        "filter": {"property": "object", "value": "page"},
    }
    out: list[NotionItem] = []
    for page in client.paginate("POST", "/search", json=body):
        if page.get("object") != "page":
            continue
        let = page.get("last_edited_time") or ""
        if since and let <= since:
            break  # descending order → everything after this is older than the watermark
        out.append(page_to_item(page))
    return out


def get_block_children(client: NotionClient, block_id: str) -> list[dict]:
    """Return the raw child blocks of a block/page (one level).

    Some pages (wiki / newer block types) return 400 under the pinned API version;
    retry once with the configured newer fallback version before giving up.
    """
    path = f"/blocks/{block_id}/children"
    try:
        return list(client.paginate("GET", path))
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            fallback = client.settings.notion_version_fallback
            if fallback and fallback != client.settings.notion_version:
                log.warning("blocks/%s/children 400 on %s; retrying with %s",
                            block_id, client.settings.notion_version, fallback)
                return list(client.paginate("GET", path, version=fallback))
        raise


# Block types whose children are *not* recursed: child pages are mirrored as
# their own items; child databases are rendered as a link, not expanded.
_NO_RECURSE = {"child_page", "child_database"}
_MAX_BLOCK_DEPTH = 6


def _fetch_block_tree(client: NotionClient, block_id: str, depth: int = 0) -> list[dict]:
    """Fetch a block's children recursively, attaching each block's ``children``.

    Child-page blocks are dropped (separate items). Recursion is depth-limited as
    a guard against pathological nesting.
    """
    blocks = []
    for block in get_block_children(client, block_id):
        if block.get("type") == "child_page":
            continue
        if (
            block.get("has_children")
            and block.get("type") not in _NO_RECURSE
            and depth < _MAX_BLOCK_DEPTH
        ):
            # Some block types (synced blocks, link_to_page, certain inline
            # databases) report has_children but 400/404 on /children. Skip those
            # rather than aborting the whole crawl; the block itself still renders.
            try:
                block["children"] = _fetch_block_tree(client, block["id"], depth + 1)
            except httpx.HTTPStatusError as exc:
                log.warning(
                    "skipping unreadable children of %s block %s: %s",
                    block.get("type"), block.get("id"), exc,
                )
        blocks.append(block)
    return blocks


def get_body_markdown(client: NotionClient, page_id: str) -> str:
    """Fetch a page's body (recursively, incl. nested blocks) as read Markdown.

    Child *pages* are excluded (mirrored as their own items); all other nested
    content — toggles, columns, indented lists, table rows — is captured.
    """
    return notion_blocks_to_markdown(_fetch_block_tree(client, page_id))


def get_child_page_ids(client: NotionClient, page_id: str) -> list[str]:
    """Return ids of block-level child pages directly under ``page_id``."""
    return [
        b["id"]
        for b in get_block_children(client, page_id)
        if b.get("type") == "child_page"
    ]
