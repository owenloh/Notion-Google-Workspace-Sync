"""Generate the read-only `_Dashboard` and `_Commands` Docs (pure builders).

`_Dashboard` is a compact, voice-readable list of the spine with Notion ids.
`_Commands` teaches Gemini how to issue a write: the allowed endpoints, the relay
request shape, the (optional) `skill/*` rules fetched from the Alistair API, and
the same name↔id catalog so it can target real items.
"""

from __future__ import annotations

from dataclasses import dataclass

_KIND_HEADING = {"area": "Areas", "project": "Projects", "action": "Actions"}
_KIND_ORDER = ["area", "project", "action"]


@dataclass
class CatalogEntry:
    kind: str
    name: str
    notion_id: str


def build_dashboard_md(entries: list[CatalogEntry]) -> str:
    lines = ["# Notion Dashboard", "", "_Read-only reflection. Source of truth is Notion._", ""]
    for kind in _KIND_ORDER:
        items = [e for e in entries if e.kind == kind]
        if not items:
            continue
        lines.append(f"## {_KIND_HEADING[kind]}")
        for e in sorted(items, key=lambda x: x.name.lower()):
            lines.append(f"- {e.name}  `{e.notion_id}`")
        lines.append("")
    return "\n".join(lines).strip("\n")


def build_intray_md(items: list[dict]) -> str:
    """Read-only mirror of the Microsoft To-Do in-tray (quick-capture list)."""
    lines = [
        "# Microsoft To-Do — In-Tray",
        "",
        "_Read-only reflection of your Microsoft To-Do in-tray. Source of truth is "
        "Microsoft To-Do._",
        "",
    ]
    if not items:
        lines.append("_(in-tray is empty)_")
    else:
        for it in items:
            title = (it.get("title") or "").strip() or "(untitled)"
            lines.append(f"- {title}")
    return "\n".join(lines).strip("\n")


def build_commands_md(
    entries: list[CatalogEntry],
    allowed_paths: list[str],
    skill_texts: dict[str, str] | None = None,
) -> str:
    skill_texts = skill_texts or {}
    lines = [
        "# Notion Commands — how to change Notion by voice",
        "",
        "To change Notion, create a task in the **Notion Commands** list whose **notes**",
        "contain a single JSON request. The sync service forwards it and writes a",
        "`✓`/`✗` receipt back into the task. You will NOT get an answer in the same turn —",
        "ask again ('did that go through?') to read the receipt.",
        "",
        "## Request shape",
        "```",
        '{ "path": "<one of the allowed paths>",',
        '  "body": { ... fields per skill/notion-master ... } }',
        "```",
        "",
        "## Allowed paths (write-only)",
    ]
    lines += [f"- `{p}`" for p in allowed_paths]
    lines += [
        "",
        "On `update-page` use `command: update_properties` to set status/due/fields and",
        "`command: insert_content` to append a note. Never use `replace_content` (blocked).",
        "There is **no archive/delete**: `update-page` does not accept `archived`, and no",
        "delete endpoint exists, so those requests return a `✗ unsupported` receipt.",
        "",
        "## Data sources (`create-pages` parent ids for new rows)",
        "- Areas → `collection://dfa76d06-073b-4493-9f96-319a9f088a5e`",
        "- Projects → `collection://b9c0cd8c-fa6c-46d1-95ed-87d7ef97d971`",
        "- Actions → `collection://2ebc58c5-8617-4748-8021-fcc2a37d3a97`",
        "",
        "## Property value encoding",
        "- Dates: `\"date:<Prop>:start\"` (+ optional `\"date:<Prop>:end\"`,",
        "  `\"date:<Prop>:is_datetime\"`). e.g. `\"date:Due:start\": \"2026-06-25\"`.",
        "- Checkbox: `\"__YES__\"` / `\"__NO__\"`.",
        "- Relation (e.g. `Project`): an array of Notion page ids — `[\"<page id>\"]`.",
        "- A property literally named `id` or `url`: prefix with `userDefined:`.",
        "- Status enums — Area: Active/Paused/Retired · Project: Active/Someday/Complete/",
        "  Dropped · Action Status: Next/Waiting/Someday/Done.",
        "",
        "## Example — add an action (create-pages)",
        "```",
        '{ "path": "/api/notion/create-pages",',
        '  "body": {',
        '    "parent": { "data_source_id": "collection://2ebc58c5-8617-4748-8021-fcc2a37d3a97" },',
        '    "pages": [ { "properties": {',
        '        "Name": "Email Bob",',
        '        "Action Status": "Next",',
        '        "Project": ["<project page id>"] } } ] } }',
        "```",
        "",
        "## Example — set status / due (update-page)",
        "```",
        '{ "path": "/api/notion/update-page",',
        '  "body": {',
        '    "page_id": "<action page id>",',
        '    "command": "update_properties",',
        '    "properties": { "Action Status": "Done", "Checkbox": "__YES__",',
        '                    "date:Due:start": "2026-06-25" } } }',
        "```",
        "",
        "## Example — append a note (update-page)",
        "```",
        '{ "path": "/api/notion/update-page",',
        '  "body": {',
        '    "page_id": "<page id>",',
        '    "command": "insert_content",',
        '    "content": "a note appended to the end of the page" } }',
        "```",
        "",
        "## Microsoft To-Do in-tray (quick capture)",
        "Capture/clear items in your Microsoft To-Do in-tray. To complete or delete,",
        "first read `_Intray (Microsoft To-Do)` for the item, then use its id.",
        "```",
        '{ "path": "/api/intray", "body": { "action": "add", "title": "Buy oat milk" } }',
        '{ "path": "/api/intray", "body": { "action": "done",   "task_id": "<id>" } }',
        '{ "path": "/api/intray", "body": { "action": "delete", "task_id": "<id>" } }',
        "```",
        "",
    ]
    for slug, text in skill_texts.items():
        if text:
            lines += [f"## Rules: {slug}", "", text.strip(), ""]
    lines += ["## Catalog (use these ids as targets)", ""]
    for kind in _KIND_ORDER:
        items = [e for e in entries if e.kind == kind]
        if not items:
            continue
        lines.append(f"### {_KIND_HEADING[kind]}")
        for e in sorted(items, key=lambda x: x.name.lower()):
            lines.append(f"- {e.name} → `{e.notion_id}`")
        lines.append("")
    return "\n".join(lines).strip("\n")
