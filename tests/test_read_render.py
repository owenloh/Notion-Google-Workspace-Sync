"""Read-only rich/nested rendering (Notion blocks → Markdown, one-way)."""

from types import SimpleNamespace

import httpx

from app.connectors.notion import read as nread
from app.core.markdown import notion_blocks_to_markdown


class _FakeClient:
    """Minimal stand-in for NotionClient.paginate over /blocks/{id}/children.

    ``fail_ids`` 400 on the pinned version; those also in ``recover_ids`` succeed
    when retried with the fallback version (exercising get_block_children).
    """

    def __init__(self, children_by_id, fail_ids=(), recover_ids=()):
        self.children_by_id = children_by_id
        self.fail_ids = set(fail_ids)
        self.recover_ids = set(recover_ids)
        self.settings = SimpleNamespace(
            notion_version="2022-06-28", notion_version_fallback="2025-09-03"
        )

    def paginate(self, method, path, *, version=None):
        block_id = path.split("/")[2]  # /blocks/{id}/children
        using_fallback = version == self.settings.notion_version_fallback
        if block_id in self.fail_ids and not (using_fallback and block_id in self.recover_ids):
            req = httpx.Request(method, "https://api.notion.com" + path)
            resp = httpx.Response(400, request=req)
            raise httpx.HTTPStatusError("400", request=req, response=resp)
        return list(self.children_by_id.get(block_id, []))


def test_fetch_block_tree_skips_unreadable_children():
    # A toggle reports has_children but its /children 400s on both versions — it
    # must be retained (rendered without children) and the crawl must not raise.
    page_blocks = [
        {"id": "bad", "type": "toggle", "has_children": True, "toggle": {"rich_text": []}}
    ]
    client = _FakeClient({"page": page_blocks}, fail_ids={"bad"})
    tree = nread._fetch_block_tree(client, "page")
    assert [b["id"] for b in tree] == ["bad"]
    assert "children" not in tree[0]  # children skipped, block kept


def test_block_children_400_recovers_on_fallback_version():
    # A page whose children 400 on the pinned version but succeed on the newer
    # fallback version are fetched via the retry.
    client = _FakeClient(
        {"wiki": [{"id": "c1", "type": "paragraph", "paragraph": {"rich_text": []}}]},
        fail_ids={"wiki"}, recover_ids={"wiki"},
    )
    blocks = nread.get_block_children(client, "wiki")
    assert [b["id"] for b in blocks] == ["c1"]


def _para(text):
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": text, "text": {"content": text}}]},
    }


def _rt(text):
    return [{"plain_text": text, "text": {"content": text}}]


def test_nested_bullets_are_indented():
    blocks = [
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rt("parent")},
            "children": [
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _rt("child")},
                }
            ],
        }
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "- parent" in md
    assert "  - child" in md  # indented one level


def test_callout_and_toggle_render():
    blocks = [
        {
            "type": "callout",
            "callout": {"rich_text": _rt("heads up"), "icon": {"emoji": "⚠️"}},
        },
        {
            "type": "toggle",
            "toggle": {"rich_text": _rt("Details")},
            "children": [_para("hidden body")],
        },
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "> ⚠️ heads up" in md
    assert "**▸ Details**" in md
    assert "  hidden body" in md  # toggle body indented


def test_table_renders_as_gfm():
    blocks = [
        {
            "type": "table",
            "table": {"table_width": 2},
            "children": [
                {"type": "table_row", "table_row": {"cells": [_rt("A"), _rt("B")]}},
                {"type": "table_row", "table_row": {"cells": [_rt("1"), _rt("2")]}},
            ],
        }
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "| A | B |" in md
    assert "| --- | --- |" in md
    assert "| 1 | 2 |" in md


def test_image_and_bookmark_render_as_links():
    blocks = [
        {
            "type": "image",
            "image": {"external": {"url": "https://x/y.png"}, "caption": _rt("diagram")},
        },
        {"type": "bookmark", "bookmark": {"url": "https://example.com", "caption": []}},
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "(https://x/y.png)" in md and "diagram" in md
    assert "(https://example.com)" in md


def test_child_database_noted_not_expanded():
    blocks = [{"type": "child_database", "child_database": {"title": "Tasks DB"}}]
    md = notion_blocks_to_markdown(blocks)
    assert "Tasks DB" in md and "Database" in md


def test_column_container_children_render():
    # Layout containers (column_list/column) have no text of their own; their
    # children must still render (regression: the else-branch used to wipe them).
    blocks = [{
        "type": "column_list",
        "children": [
            {"type": "column", "children": [_para("inside a column")]},
            {"type": "column", "children": [
                {"type": "child_page", "id": "p-9", "child_page": {"title": "Colpage"}},
            ]},
        ],
    }]
    md = notion_blocks_to_markdown(blocks)
    assert "inside a column" in md          # column text not dropped
    assert "Colpage" in md                   # child_page marker inside a column


def test_child_page_rendered_as_marker_with_name():
    # The sub-page is mirrored as its own Doc, but the parent body keeps a named
    # marker (+ id) so Gemini knows it sits here and can find that Doc.
    blocks = [
        _para("keep me"),
        {"type": "child_page", "id": "abc-123", "child_page": {"title": "Sub"}},
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "keep me" in md
    assert "Sub" in md          # named so Gemini has context
    assert "abc-123" in md      # id for cross-referencing the catalog
