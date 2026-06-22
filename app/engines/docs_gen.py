"""Generate the read-only `_Dashboard` and `_Commands` Docs (pure builders).

`_Dashboard` is a compact, voice-readable list of the spine with Notion ids (all
Areas/Projects + open Actions). `_Commands` teaches Gemini how to issue a write:
the AI execution guardrails, the allowed endpoints, the exact request envelope +
examples, and the property encoding. It stays lean — target ids are looked up in
`_Dashboard`, not duplicated here.
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
    lines = ["# Notion Dashboard", "",
             "_Read-only reflection: all Areas & Projects, plus open Actions (completed "
             "Actions live in the _Notion Index sheet). Source of truth is Notion._", ""]
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
        "## AI Execution Guardrails (read FIRST, anti-refusal protocol)",
        "1. **You DO have live tools** to read Google Drive and write Google Tasks. If a",
        "   Drive/Search/Task tool errors or a function name seems missing, that is a",
        "   TEMPORARY structural mismatch, NOT a lack of access. You are FORBIDDEN from",
        "   saying \"I can't access your files/Drive/Tasks\" or \"the connector isn't live\".",
        "   Retry, or try an alternative file-search/corpus tool.",
        "2. **CRITICAL — payload goes in the task body.** Put the JSON in the task's",
        "   `notes` (a.k.a. `description`/`details`) argument, NOT the title. A task whose",
        "   notes don't start with `{` is a FAILED execution — fix it and retry.",
        "3. **No polite exits.** If a step fails, do NOT pivot to \"can I help with",
        "   something else?\". State exactly which tool step failed (the raw error) and ask",
        "   to retry.",
        "4. **CRITICAL — use the EXACT envelope, do NOT invent your own schema.** Every",
        "   request is `{path, body}`; for `create-pages` the body MUST have `parent` +",
        "   a `pages` array (see the exact example below). If your JSON has **no `pages`",
        "   array**, or top-level `action`/`database_id`/`parent_id`/`properties`, or fields",
        "   like `Action Name`/`Status`/`title`/`project_id` — it is WRONG and 422s. Copy",
        "   the example; change values only.",
        "",
        "To change Notion, create a task in Google Tasks whose **notes** contain a",
        "single JSON request. The sync service forwards it and writes a `✓`/`✗` receipt",
        "back into the task. You will NOT get an answer in the same turn — ask again",
        "('did that go through?') to read the receipt.",
        "",
        "## Rules (follow exactly)",
        "1. A normal task in your default Google Tasks list is fine. The notes MUST",
        "   start with `{` (a JSON object); plain tasks are ignored.",
        "2. The JSON goes in the task **notes**. **One task = one change.**",
        "3. Use ids from the catalog below (or `_Dashboard`). Don't invent ids.",
        "4. Wait for the `✓` receipt before queuing a change that depends on this one.",
        "5. Allowed: create pages, set properties, append notes, edit text",
        "   (`update_content`), and Microsoft To-Do `add`/`done`/`delete`.",
        "6. NOT allowed: deleting/archiving pages (do it by hand), `replace_content`,",
        "   renaming a sub-page by title. To read Notion, read the mirror Docs — never",
        "   edit them to change Notion.",
        "",
        "## VERIFY every command (do NOT fire-and-forget)",
        "Sending the task is only step one. You MUST confirm it actually worked — never",
        "assume success and move on:",
        "1. **Create** the task (JSON in the notes).",
        "2. **Wait ~30s**, then **re-read that exact task's notes**. Repeat the read every",
        "   ~10-15s (keep waiting in between) until the notes contain a receipt: `✓` (done)",
        "   or `✗` (failed). The poller runs about every 30s, so a receipt normally appears",
        "   within 30-60s. Do not give up before ~90s.",
        "3. If the receipt is **`✗`**: read its reason, fix the JSON, send a corrected",
        "   task, and verify again. Do NOT tell me it worked — say what failed and that",
        "   you are retrying.",
        "4. If the receipt is **`✓`**: **wait ~30s more**, then open the affected page's",
        "   mirror Doc in Google Drive (or `_Dashboard` for a new id / changed status) and",
        "   confirm the change is really there. Only then report success — and say what",
        "   you actually saw in Drive, not just that the task said `✓`.",
        "5. If you are Gemini Live and cannot wait mid-turn for the async result, say the",
        "   task is **queued** and ask me to prompt 'did it go through?' — then run steps",
        "   2-4 on the next turn. Never claim a change landed without seeing the receipt",
        "   AND the Drive outcome.",
        "",
        "## Request shape (exact — copy it)",
        "```",
        '{ "path": "/api/notion/create-pages",',
        '  "body": { "parent": { "data_source_id": "collection://<Actions id below>" },',
        '            "pages": [ { "properties": { "Name": "...", "Action Status": "Next" } } ] } }',
        "```",
        "Field names are exact and the **title field differs per database**: it is",
        "`Name` for Actions and Areas, but **`Project`** for Projects (see 'Properties",
        "per database' below). For Actions: `Action Status` (Next/Waiting/Someday/Done —",
        "not `Status`/`To Do`), `Project` = array of ids, `date:Due:start` for the due date.",
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
        "## Properties per database (create-pages) — the TITLE field differs!",
        "Use the exact title key for the target database, or Notion rejects it",
        "(\"<key> is not a property that exists\"):",
        "- **Areas** (`collection://dfa76d06-…`): title `Name`; `Status`",
        "  Active/Paused/Retired; `Type` Life/Career; `Standards` (text).",
        "- **Projects** (`collection://b9c0cd8c-…`): title **`Project`** (NOT `Name`);",
        "  `Status` Active/Someday/Complete/Dropped; `Area` = `[\"<area page id>\"]`;",
        "  `Direction` (text); `Repo` (url).",
        "- **Actions** (`collection://2ebc58c5-…`): title `Name`; `Action Status`",
        "  Next/Waiting/Someday/Done; `date:Due:start`; `Project` = `[\"<project page id>\"]`;",
        "  `Checkbox` `__YES__`/`__NO__`.",
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
        "## Example — add a project (title key is `Project`, NOT `Name`)",
        "```",
        '{ "path": "/api/notion/create-pages",',
        '  "body": {',
        '    "parent": { "data_source_id": "collection://b9c0cd8c-fa6c-46d1-95ed-87d7ef97d971" },',
        '    "pages": [ { "properties": {',
        '        "Project": "Gelato Deployment",',
        '        "Status": "Active",',
        '        "Area": ["<area page id>"] } } ] } }',
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
        "## Example — edit/rewrite text (update-page, targeted search-replace)",
        "First read the page's mirror Doc and copy the **exact** existing text. "
        "`update_content` finds `old_str` and replaces it with `new_str` (put the old "
        "text inside `new_str` to append rather than overwrite; an empty `new_str` "
        "deletes that span). It will NOT delete nested sub-pages — safe, unlike "
        "`replace_content`.",
        "```",
        '{ "path": "/api/notion/update-page",',
        '  "body": {',
        '    "page_id": "<page id>",',
        '    "command": "update_content",',
        '    "old_str": "the exact text that already exists on the page",',
        '    "new_str": "the refined text that replaces it" } }',
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
        "## Common mistakes (these all FAIL with `422 missing 'pages'`)",
        "These invented shapes are WRONG — none have the `path`+`pages` envelope:",
        "```",
        '{ "action": "add_action", "database_id": "...", "properties": {...} }   ✗',
        '{ "action": "create_action", "title": "...", "project_id": null }       ✗',
        '{ "type": "next_action", ... }                                          ✗',
        '{ "action": "add_page", "parent_id": "...", "properties": {...} }       ✗',
        "```",
        "The RIGHT version of all of the above (a new action):",
        "```",
        '{ "path": "/api/notion/create-pages",',
        '  "body": {',
        '    "parent": { "data_source_id": "collection://2ebc58c5-8617-4748-8021-fcc2a37d3a97" },',
        '    "pages": [ { "properties": { "Name": "Next Action", "Action Status": "Next" } } ] } }',
        "```",
        "Also: when creating a **Project**, the title key is `Project`, not `Name` —",
        "`{\"Name\": \"...\"}` on a Project is rejected (\"Name is not a property that exists\").",
        "",
    ]
    for slug, text in skill_texts.items():
        if text:
            lines += [f"## Rules: {slug}", "", text.strip(), ""]
    lines += [
        "## Target ids",
        "Look up the Notion id of any Area / Project / Action in the **_Dashboard** Doc "
        "(format `- <name>  `<id>``). It lists all Areas & Projects plus open Actions; "
        "completed Actions are in the `_Notion Index` sheet. Don't invent ids.",
        "",
    ]
    return "\n".join(lines).strip("\n")
