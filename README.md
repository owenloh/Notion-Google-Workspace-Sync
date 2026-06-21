# Notion ⇄ Google Workspace Sync

A two-way mirror that keeps an entire Notion workspace in step with Google
Workspace (a `_Notion Index` Google Sheet + a Drive folder tree of Google Docs),
so the workspace becomes reachable through Google-native assistants (e.g. Gemini
Live) while edits made on either side flow back to the other.

## Why

Notion is the source of truth for a PARA + GTD system. Google-native assistants
can only read and write Google Workspace, not Notion. This service mirrors the
whole Notion workspace into Google Workspace and keeps both sides synchronized,
so the assistant always sees current data and anything it creates lands back in
Notion.

> Scope: **only** the Notion ⇄ Google Workspace sync. Google Calendar and
> Microsoft To-Do are explicitly out of scope.

## What it mirrors

* **Relational spine** (Areas → Projects → Actions) → three tabs of a single
  Google Sheet (`_Notion Index`). Each row links to its body Google Doc.
* **Rich bodies + nested sub-pages** → Google Docs in a Drive folder tree that
  mirrors the relation hierarchy, recursing into block-level child pages.
* **Loose pages** — the References tray and the Briefing page — as Docs.

## How it works

A single FastAPI service plus an in-process scheduler. A SQLite **ledger** maps
each Notion page to its Google artifacts and stores per-facet content hashes.
Every change — whether it arrives via a Notion webhook or a poll of Notion /
Drive / Sheets — flows through one **echo-suppression pipeline** (canonical hash
compare + short-lived inflight markers) so a write to one side never bounces back
as a phantom edit.

See `app/` for the module layout and the approved plan for the full design.

## Sync cadence

| Job | Default | Scope |
| --- | --- | --- |
| Notion delta poll | 3 min | Spine (Areas/Projects/Actions) + loose pages |
| Google poll | 2 min | Sheet edits + Drive changes feed |
| Full reconcile | 30 min (`FULL_SYNC_SECONDS`) | **Whole workspace, recursing into every child page**; heals drift, sweeps tombstones |
| Notion webhook | real-time | Any page Notion notifies about |

Deep child-page edits don't appear in the delta poll (child pages aren't in any
database), so the 30-minute full reconcile is what guarantees they sync. A Notion
webhook, when it fires for the page, syncs it immediately.

## On-demand full sync

Set `ADMIN_API_KEY`, then trigger a complete reconcile whenever you want:

```bash
curl -X POST "https://<host>/admin/full-sync?key=$ADMIN_API_KEY"
# or: -H "X-Admin-Key: $ADMIN_API_KEY"
```

Returns `{"status":"ok","counts":{...}}`. Without the key the endpoint returns
401; if `ADMIN_API_KEY` is unset it returns 503 (disabled).

## Development

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
uv run pytest
```

Copy `.env.example` to `.env` and fill in the Notion and Google credentials.
`scripts/bootstrap.py` runs the one-time Google OAuth consent flow, creates the
`_Notion Index` sheet, and performs the first full mirror.
