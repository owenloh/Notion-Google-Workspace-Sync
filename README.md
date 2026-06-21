# Notion → Google reflection + command relay

Makes an entire Notion workspace reachable through **Gemini Live voice**, which can
only read/write Google Workspace. Notion is mirrored **one-way** into Google Drive
as a rich, read-only set of Docs (fast retrieval), and changes are made through a
**Google Tasks command inbox** that this service relays to an existing Notion API.

## Why one-way + commands

A Notion page body can't be faithfully represented in a Google Doc (callouts,
toggles, tables, columns, nested blocks, databases/views have no clean equivalent).
A two-way *content* mirror would eventually write a lossy Doc back and destroy the
rich Notion structure. So:

- **Reads:** Notion → Google **Docs** (read-only reflection). Gemini Live reads Docs.
- **Writes:** Gemini Live writes a **Google Task** (the one surface it can write by
  voice). This service reads the task, **relays** it as an HTTP call to the existing
  *Alistair Skills API* (which holds the full Notion write toolset), completes the
  task with a `✓`/`✗` receipt, and re-reflects the affected page. Google can never
  silently corrupt Notion.

> Gemini Live can't call custom HTTP APIs / MCP directly (that's Enterprise/CLI
> only), and **Gems don't work in Live** — only "Saved Info" loosely steers it. The
> Tasks inbox + receipt is the workable write path; confirmation is a follow-up
> turn ("did that go through?").

## Google-side layout

```
Google Tasks "Notion Commands"   ← Gemini writes one JSON request per task
Drive: Notion Mirror/            ← read-only reflection
  _Commands (Doc)                  how to write + allowed paths + name→id catalog
  _Dashboard (Doc)                 compact Areas/Projects/Actions list with ids
  Areas/<Area>/<Project>/….gdoc    rich bodies, recursed into nested blocks
  References/  Briefing/
```

## How it stays in sync

| Layer | Cadence | Purpose |
| --- | --- | --- |
| `poll_commands` | ~30 s | run pending command tasks (Tasks has no push) |
| `poll_notion` | ~3 min | mirror spine + loose pages changed by `last_edited_time` |
| `full_reconcile` | ~30 min | recurse all child pages, heal drift, regenerate Docs |
| per-command re-reflect | instant | refresh the page a command just changed |
| Notion webhook | optional | near-instant reflection of hand edits (off by default) |

Propagation is incremental — only content whose hash changed is rewritten.

## Command format

Gemini puts one JSON request in a task's **notes** (see the generated `_Commands`
Doc for the live schema + ids):

```json
{ "path": "/api/notion/create-pages",
  "body": { "parent": {"database_id": "<Actions db id>"},
            "properties": { "Name": "Email Bob", "Action Status": "Next",
                            "Due": "2026-06-25", "Project": ["<project id>"] } } }
```

The relay is **guarded**: only `RELAY_ALLOWED_PATHS` are callable (so a task can't
reach `github/push-file` or deletes), and `update-page` `replace_content` is blocked
unless `force:true`.

Non-voice clients can call the synchronous endpoint instead:

```bash
curl -X POST "https://<host>/command?key=$ADMIN_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"path":"/api/notion/update-page","body":{...}}'
```

## Setup

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
uv run pytest
cp .env.example .env   # fill in Notion (read-only), Google OAuth, relay key, admin key
python -m scripts.bootstrap auth     # Google consent → refresh token (incl. Tasks)
python -m scripts.bootstrap init     # create the Drive mirror folder + sheet
python -m scripts.bootstrap mirror   # first full reflection (+ _Commands/_Dashboard)
```

Then paste `docs/SAVED_INFO.md` into Gemini → Settings → **Saved info**. Deploy on
Railway with a `/data` volume (Dockerfile + railway.json provided).
