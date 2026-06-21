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
        "Use `update_properties` to set status/due/fields and `insert_content` to append",
        "notes. Never use `replace_content` (it is blocked).",
        "",
        "## Example — add an action",
        "```",
        '{ "path": "/api/notion/create-pages",',
        '  "body": { "parent": {"database_id": "<Actions db id>"},',
        '            "properties": { "Name": "Email Bob", "Action Status": "Next",',
        '                            "Due": "2026-06-25", "Project": ["<project id>"] } } }',
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
