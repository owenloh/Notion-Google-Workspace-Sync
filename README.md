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

Two parts: a **one-time laptop step** to mint the Google refresh token, then a
**deploy from GitHub** (Railway) that you can do entirely from mobile.

### Prerequisites (Google Cloud + Notion)

- **Google Cloud project**: enable the **Drive, Docs, Sheets, and Tasks** APIs;
  configure the OAuth **consent screen** (User type *External*, add your own Gmail as
  a *Test user*); create an **OAuth Client ID of type "Desktop app"** and download its
  JSON (this becomes `GOOGLE_CREDENTIALS_JSON`). The app requests these scopes:
  `drive`, `documents`, `spreadsheets`, `tasks`.
- **Notion**: a **read-only** internal integration token (`NOTION_API_TOKEN`), and the
  integration must be shared with the PARA databases + the loose pages it mirrors.

### Part A — one-time, on a laptop (needs a browser)

`bootstrap auth` opens a browser for consent and catches a `localhost` redirect, so it
can't run on mobile or in the container. Everything after this is mobile-friendly.

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
uv run pytest
cp .env.example .env   # fill GOOGLE_CREDENTIALS_JSON, NOTION_API_TOKEN, RELAY_API_KEY, ADMIN_API_KEY
uv run python -m scripts.bootstrap auth   # browser consent → prints GOOGLE_OAUTH_REFRESH_TOKEN
uv run python -m scripts.bootstrap init   # prints GOOGLE_DRIVE_MIRROR_FOLDER_ID + GOOGLE_INDEX_SHEET_ID
```

Keep the three printed values — they go into Railway next. (You can also run
`bootstrap mirror` locally for the first reflection, or just let Railway do it via
`/admin/full-sync` below.)

### Part B — deploy from GitHub (Railway; mobile-friendly)

1. Railway → **New Project → Deploy from GitHub repo** → this repo + branch. It builds
   from the provided `Dockerfile` / `railway.json` (start command uses `$PORT`).
2. Add a **Volume mounted at `/data`** (the SQLite ledger lives at `/data/ledger.db`).
3. Set **environment variables** (from `.env.example`):
   - `NOTION_API_TOKEN` (read-only), `ENABLE_NOTION_WEBHOOK=false`
   - `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_OAUTH_REFRESH_TOKEN` (Part A),
     `GOOGLE_DRIVE_MIRROR_FOLDER_ID`, `GOOGLE_INDEX_SHEET_ID` (Part A)
   - `RELAY_API_BASE_URL=https://web-production-2144c.up.railway.app`, `RELAY_API_KEY`
   - `ADMIN_API_KEY` (you choose), `LEDGER_DB_PATH=/data/ledger.db`
   - (`RELAY_ALLOWED_PATHS` / `RELAY_DEFAULT_PATH` / poll cadences keep their defaults)
4. After deploy: `curl https://<host>/health`, then trigger the first reflection:
   `curl -X POST "https://<host>/admin/full-sync?key=$ADMIN_API_KEY"`.

Then paste `docs/SAVED_INFO.md` into Gemini → Settings → **Saved info**.

> **create-pages parent ids** (validated live): use the **database** ids, not the
> internal `collection://` data-source ids — Areas
> `collection://dfa76d06-073b-4493-9f96-319a9f088a5e`, Projects
> `collection://b9c0cd8c-fa6c-46d1-95ed-87d7ef97d971`, Actions
> `collection://2ebc58c5-8617-4748-8021-fcc2a37d3a97`. The generated `_Commands` Doc
> embeds these plus the live name↔id catalog.

