"""Read-only rich/nested rendering (Notion blocks → Markdown, one-way)."""

from app.core.markdown import notion_blocks_to_markdown


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


def test_child_page_skipped():
    blocks = [
        _para("keep me"),
        {"type": "child_page", "child_page": {"title": "Sub"}},
    ]
    md = notion_blocks_to_markdown(blocks)
    assert "keep me" in md
    assert "Sub" not in md
