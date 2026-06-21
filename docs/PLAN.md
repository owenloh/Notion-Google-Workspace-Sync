# Plan: Notion → Google reflection + command-driven write-back (re-architecture)

> This is the approved project plan, recorded verbatim for future sessions.

## Context

I run my life from a Notion workspace (PARA + GTD) and want it voice-reachable
via Gemini, which can only read/write Google Workspace. The first build was a
**two-way content mirror**. Stress-testing it surfaced a fatal flaw: a Notion
page body cannot be faithfully represented in Google Docs (callouts, toggles,
tables, columns, nested blocks, inline/linked databases and *views* have no clean
Google equivalent and are flattened/dropped). With two-way body sync, any Doc
edit — or any non-round-trip-stable conversion — writes that **lossy** version
back and **destroys** the rich Notion structure. Databases/views simply cannot be
made writable through a flat Google surface.

**Decision (this re-architecture):**
- **Notion → Google is the only content direction.** Google is a fast, rich,
  *read-only reflection* for Gemini Live to read. It is never the source of truth.
- **Mutations happen through a command inbox built on Google Tasks** — the surface
  Gemini Live can actually *write* by voice. (Verified: Live can add/edit/complete
  Tasks and Keep notes and Calendar events, but **cannot write Google Sheet cells
  or Doc bodies by voice**, and **Gems do not work in Live** — only Saved Info /
  custom instructions can steer it, best-effort.) Each Task = one command.
- **This service is a thin relay, not a Notion writer.** The user already runs a
  separate **Railway "Notion API"** — a deployed HTTP wrapper exposing the *full*
  Notion connector toolset (every operation, with schemas/rules). The executor
  reads a command task, **relays it as an HTTP call to that existing API**, then
  completes the task and writes a `✓ result / ✗ error` receipt into the task notes.
  So Gemini literally writes the request spec (method/path/body) for that API into
  the task, and we perform the call it can't. Writes therefore use the *full* Notion
  surface; this service's own Notion access stays **read-only** (for the mirror).
  After a successful relay we re-reflect the affected item(s) immediately.

This removes the clobber risk entirely (Google can never silently corrupt Notion)
while keeping fast read access and a precise, auditable write path.

### Answers to the open concerns that shaped this
- **"Does Gemini know the command went through?"** The executor takes the **relay
  API's HTTP response**, writes a `✓ summary` / `✗ error` receipt into the task
  notes, **completes the task**, and re-reflects affected items. Live can't pause
  mid-turn, so confirmation is a **follow-up utterance** ("did that sync?") where
  Live reads the task receipt / the refreshed Docs.
- **"Where does the (large) command how-to live?"** A **tiny pointer in Saved Info**
  ("to change Notion, read the `_Commands` Doc, then write the command task"); the
  **full schema + live name↔id catalog live in the generated `_Commands` Doc**
  (regenerated each sync, always current, Live can read Docs). The schema is sourced
  from the existing Railway API (its OpenAPI/tool descriptions). Optionally the 3–4
  most-used operations are also inlined in Saved Info to avoid a Doc fetch.
- **"How does it target the right Notion item?"** The `_Commands`/`_Dashboard` Docs
  expose every Area/Project/Action as **name + Notion id**, so Gemini puts real ids
  straight into the relayed request — no server-side name resolution needed on the
  write path.

**Scope:** only Notion ⇄ Google Workspace. Calendar and MS To-Do are out. Railway-
hosted FastAPI + in-process scheduler + SQLite ledger (now used for identity and
change-gating; echo/inflight machinery is largely retired with write-back gone).

### Already built and kept (one-way path is sound)
`app/core/{canonical,hashing,markdown}.py`, `app/ledger/*`, `app/connectors/*`
(Notion read/write, Google auth/drive/docs/sheets), `app/engines/{mirror_out,
notion_source,google_mirror,resolve}.py`, `app/runtime.py`, webhook, scheduler,
bootstrap — all reused. The **write-back** pieces are replaced (see below).

## My actual Notion structure (read live via connector)

Relational spine — three linked databases:

| DB (data source) | Properties | Body |
|---|---|---|
| **Areas of Focus** `54816fca-6f6c-4588-8c1a-1cdfcc6c9092` | Name(title), Status(Active/Paused/Retired), Type(Life/Career), Standards(text), Projects(→Projects) | Rich, nested child pages |
| **Projects** `f0ea8841-ca74-47b7-a28a-0b367bca8c41` | Project(title), Area(→Areas), Direction(text), Status(Active/Someday/Complete/Dropped), Repo(url), Next actions(→Actions), Last edited time | Rich: callouts, toggles, child pages |
| **Actions** `1d3eb1dd-2803-4692-a4d5-6ca9709ae570` | Name(title), Action Status(Next/Waiting/Someday/Done), Due(date), Project(→Projects, multi), Checkbox, Created time | Usually title-only/blank |

Loose pages: **Briefing** "Alistair's Brief" `3806f0cc-dd76-80bb-9e16-fcce720de5ee`
(under *Mission Control* `34f6f0cc-dd76-801d-b0ec-de6c10685d10`); **References tray**
"Unorganised References" `37e6f0cc-dd76-8086-a07d-f6704b0c25df` (under *Library*
`1fa6f0cc-dd76-809e-8bcb-e5db5ae28237`) — a flat page of `####`-headed snippets.

Two kinds of containment to mirror: **relations** (Area→Project→Action) and
**block-level child pages** (a page body can nest sub-pages recursively).

## Target Google Workspace structure (reevaluated for Gemini Live)

Read lives in **Drive (Docs)** — what Live can read/search by voice. Write lives in
**Google Tasks** — what Live can create by voice. The Sheet is kept only as a
secondary structured view for the desktop (not the Live path).

```
Google Tasks
  "Notion Commands" list                 ← Gemini WRITES here; each task = one command
                                           (server executes → completes task → receipt in notes)

Drive: Notion Mirror/                     ← READ-ONLY reflection (overwritten each sync)
  _Commands (Doc)                          command schema + verbs + LIVE catalog of names
  _Dashboard (Doc)                         flat lists of Areas/Projects/Actions for quick voice read
  _Notion Index (Sheet, optional)          structured spine tabs for desktop browsing
  Areas/<Area>/<Area>.gdoc                 rich body reflection (recursed into nested blocks)
            <Project>/<Project>.gdoc       (projects nested under their Area)
              <child subpage>…             recursive subtree of read-only Docs
  References/…   Briefing/…
```

**Command (Google Tasks).** One list, `Notion Commands`. Gemini creates a task whose
**notes** hold a request spec for the Alistair Skills API — a JSON object the relay
forwards verbatim (with `X-API-Key`), e.g.:
```
{ "path": "/api/notion/create-pages",
  "body": { "parent": {"database_id": "1d3eb1dd-…(Actions)"},
            "properties": { "Name": "Email Bob",
                            "Action Status": "Next",
                            "Due": "2026-06-25",
                            "Project": ["f0ea8841-…(PourDynamics)"] } } }
```
(Exact `body` shape follows `skill/notion-master`; the relay is schema-agnostic and just
forwards `method`+`path`+`body`, but **only** for allowlisted paths.) The parser is
tolerant (accepts a bare body / `key: value` lines); a malformed task is completed with a
`✗` receipt echoing the expected shape.

**`_Commands` Doc.** Generated each sync from (a) the allowed **write** endpoints + the
`skill/notion-master`/`notion-references-tray` rules and (b) the live **name↔Notion-id
catalog** from the ledger, so Gemini knows both *how* to call and *which ids* to use.
**Only executable write ops are listed** (`create-pages`, `update-page`); read/query ops
are excluded (a Task command returns no data to Gemini — reads come from the mirror), and
**Calendar/To-Do commands are omitted for now** (To-Do added once the Notion path is done).
The **Saved Info snippet** (for the user to paste into Gemini) is just the pointer: "to
change my Notion, read the `_Commands` Doc, then create a task in *Notion Commands* with
the request."

**`_Dashboard` Doc + Drive Doc tree.** Read-only. The Doc tree carries rich bodies,
**recursed into nested blocks** with richer rendering (callouts→quotes, toggles→heading
+ indented content, tables→GFM, images/bookmarks→links). `_Dashboard` is a compact
text list of the spine for fast voice queries. Tracked by Google file id.

**`_Notion Index` Sheet (optional).** The three spine tabs as a structured read mirror
for desktop use; **not** read or written by Live. Can be dropped to simplify.

## Architecture (changes from the built two-way version)

One FastAPI process + `AsyncIOScheduler` + SQLite ledger. The ledger keeps its role
as **identity + change-gating** (notion_id ↔ doc/folder/row + content hashes, so we
only rewrite what changed). Echo/inflight/conflict logic is no longer needed for
content (there is no content write-back) and is retired from the active paths.

**Reads are decoupled from the Railway API.** The mirror uses *our own*
**read-only** Notion integration (the connectors already built), not the Railway API's
`fetch`/`query`. The read mirror is the core value, so it must not break if that API has
downtime or changes its markdown rendering, and we want full control over body fidelity.
The Railway API is the **write** path only. (Two Notion integrations — ours read-only,
theirs for writes — is intentional isolation.)

### Existing API (Alistair Skills API v1) — mapping & gaps
Auth: `X-API-Key` header on every `/api/*`. Model: *function* APIs act, *skill* APIs
(`/api/skill/{slug}`) describe rules. The relay targets only these:

- add action / project / sub-page → `POST /api/notion/create-pages`
- complete / set status / due / field → `POST /api/notion/update-page` (`update_properties`)
- append a note → `POST /api/notion/update-page` (`insert_content` — **never** `replace_content`)
- reads for the `_Dashboard`/`_Commands` catalog use our **own** Notion token (the API's
  `query`/`query-database`/`fetch` mirror the same data but we don't need them).

Write convention: the API says "consult `skill/notion-master` first → fetch → update-page",
so **`skill/notion-master` is the authoritative write format** (property names, status
enum values, relation shape). The mirror fetches it (+ `skill/notion-references-tray`) at
sync time and embeds it into the `_Commands` Doc so Gemini composes correct bodies.

Gaps / risks handled in the design:
- **No archive/delete endpoint** (only create/update/move/duplicate; `get-teams`,
  `create-view`, `update-view` return 501). `archive` is supported only if `update-page`
  accepts `archived:true` (to confirm from `skill/notion-master`); otherwise the executor
  returns a `✗ unsupported` receipt.
- **`replace_content` footgun**: the relay rejects `update-page` calls using
  `replace_content` unless an explicit `force:true` is present; the `_Commands` Doc only
  documents `insert_content`/`update_properties`.
- **Open-proxy risk**: the API also exposes `github/push-file` (a `GITHUB_TOKEN` write)
  and full Notion writes. A command task is creatable by anyone with the user's Google
  account, so the relay enforces `RELAY_ALLOWED_PATHS` (default: `/api/notion/create-pages`,
  `/api/notion/update-page`, `/api/notion/create-comment`); any other path → `✗` receipt.
  Never a blind passthrough.
- **Re-reflect**: parse the page id/url from the `create-pages`/`update-page` response and
  `mirror_item` that page; if absent, fall back to a `poll_notion` delta.
- **Overlap ignored**: the API's `intray` (MS-To-Do-backed, out of scope) and its Notion
  read endpoints duplicate things we already have; we keep our own read mirror.

### What changes
**1. Google Tasks connector** — new `app/connectors/google/tasks.py` (Tasks API v1):
list tasklists, find/create the `Notion Commands` list, list tasks, update a task
(complete + edit notes). Add the Tasks scope to `app/connectors/google/auth.py:SCOPES`
and re-run bootstrap consent.

**2. Enrich the read projection** — `app/connectors/notion/read.py:get_body_markdown`
recurses into child blocks (currently top-level only) so nested content is captured;
`app/core/markdown.py:notion_blocks_to_markdown` gains read-only rendering for callout,
toggle (heading + indented children), table (GFM), image/bookmark (link), and indented
list nesting. One-way, so no reverse parser and round-trip stability no longer
constrains richness. Add `_Dashboard` + `_Commands` Doc generation in `mirror_out`.

**3. Relay client** — new `app/connectors/relay.py`: a thin httpx client to the Alistair
Skills API. Config: `RELAY_API_BASE_URL`, `RELAY_API_KEY` (sent as `X-API-Key`).
`relay(method, path, body) -> (ok, status, summary, affected_id)`. **Guarded, not a blind
proxy**: rejects any `path` not in `RELAY_ALLOWED_PATHS`, and rejects `update-page` bodies
using `replace_content` unless `force:true`. Parses the response for the affected page
id/url so the executor can re-reflect precisely.

**4. Command parser** — new `app/engines/command_schema.py`:
`parse_command(text) -> RelayRequest | error`. Accepts a JSON object with
`method`/`path`/`body` (preferred), a bare JSON body (defaults to `POST` to a configured
default path), or tolerant `key: value` lines. Pure and unit-tested.

**5. Command executor** — new `app/engines/commands.py`:
- `CommandExecutor.run_pending()`: read tasks in `Notion Commands` not yet completed;
  `parse_command(task.notes or task.title)` → `relay.relay(...)`; then **complete the
  task** and prepend a `✓ <summary>` / `✗ <error>` receipt to its notes.
- On success, re-reflect: if the relay response carries an affected page id, call
  `MirrorOut.mirror_item(get_item(id))`; otherwise trigger a `poll_notion` delta so the
  mirror catches up quickly.
- `execute_one(request)` is shared with the HTTP endpoint.

**6. `_Commands` + `_Dashboard` Docs** — `mirror_out` generates: `_Dashboard` (compact
Areas/Projects/Actions list with **name + Notion id**) and `_Commands` (the relay request
format + the allowed endpoints + the **`skill/notion-master` and
`skill/notion-references-tray` rules fetched from the API at sync time** + the same
name↔id catalog). Plus a `docs/SAVED_INFO.md` pointer snippet for the user to paste.

**7. HTTP command endpoint** — `app/main.py` `POST /command?key=` (reuses `ADMIN_API_KEY`)
accepts a `RelayRequest` JSON and returns the relay result **synchronously** (desktop/
automation; Live still uses Tasks). Same `execute_one` underneath.

**8. Retire content write-back + demote webhook** — delete `app/engines/mirror_in.py` +
its tests; drop Drive-changes-feed + sheet-diff polling from `scheduler/jobs.py` (replaced
by `poll_commands`); retire echo/inflight/conflict from active paths (ledger keeps identity
+ change-gating). Make the **Notion webhook optional** (`ENABLE_NOTION_WEBHOOK`, default
off) — the two poll layers + per-command re-reflect are the required path. This service's
Notion connector is now **read-only**; `notion/write.py` is no longer on any active path
(all writes go via the relay). Keep Notion→Google deletes (archived page → remove Doc) via
the existing tombstone path in `mirror_out`/reconcile.

### Scheduler (revised)
**Two reflection layers (the webhook is dropped from the required path):**
```
*/30 s    poll_commands   → CommandExecutor.run_pending      (interactive write path; Tasks API has no push, so we poll)
*/3 min   poll_notion     → mirror_out delta (spine + loose by last_edited_time)
*/30 min  full_reconcile  → mirror_out.sync_all (recurse all child pages; regen _Commands/_Dashboard; heal drift)
```
Plus each command's **immediate targeted re-reflect** (covers the "I just did that" case),
and `POST /command` (synchronous writes). The **Notion webhook is optional/off by default**
— webhooks are best-effort (missed/delayed, unreliable for child pages), and its only
unique value (instant reflection of edits made *by hand in Notion*) isn't time-critical for
a read mirror. Code stays; enable later via env if desired.

### Propagation, reads & ack (how it actually behaves)
- **Notion → Google is incremental, never a full rewrite**, via **two layers**: the 3-min
  delta poll (spine/loose by `last_edited_time`) and the 30-min reconcile (recurses
  everything, writes only hash-changed facets). A change made **by a command** is
  re-reflected within seconds (targeted). A **new sub-page made by hand in Notion** appears
  within 30 min (reconcile). The optional webhook (off by default) would make hand edits
  near-instant but isn't required.
- **Reads come only from the Google mirror.** Confirmed by research: consumer **Gemini
  Live voice cannot call custom HTTP APIs / MCP / connectors** — that exists only in Gemini
  Enterprise / Gemini CLI / Cloud Assist, not the phone assistant. So "read Notion via an
  HTTP response" is not available in Live; the Docs/`_Dashboard` mirror is the read path
  (and it is the whole point — independent of the write-ack problem).
- **Write ack is deferred, not synchronous.** Gemini writes a Task; *our server* makes the
  HTTP call, so Gemini never sees the HTTP response. Live is turn-based and **cannot be told
  to wait**; confirmation is a follow-up turn where Live reads the task receipt / refreshed
  Docs. Fine for task capture; for instant confirmation use `POST /command` from a non-Live
  client.
- **Complementary path (optional):** the Alistair API can be called *directly/synchronously*
  from Gemini CLI, a custom GPT, or Gemini Enterprise (as an MCP server). Those contexts
  don't need this mirror+relay; this service exists specifically to make the **phone Live
  voice** context work within its Workspace-only limits.

### Config / secrets
Add Tasks scope; `RELAY_API_BASE_URL` (the Alistair Skills API), `RELAY_API_KEY` (sent as
`X-API-Key`), `RELAY_ALLOWED_PATHS` (default the two Notion write endpoints + comment);
`ENABLE_NOTION_WEBHOOK` (default off); reuse `ADMIN_API_KEY` for `/command`. Drop
`SYNC_BOT_NOTION_USER_ID`. The mirror's Notion read uses a **read-only** integration token.
Same deps; same Railway/Docker deploy. **Build-time input still needed:** the `skill/notion-master` (and
`skill/notion-references-tray`) doc text — to confirm the exact `create-pages`/`update-page`
body shape (property names, status values, relation format, whether `archived` is settable)
and to embed in the `_Commands` Doc. The service also fetches these at runtime.

## Build order

1. **Read fidelity** — recursive block fetch + richer one-way rendering in
   `read.py`/`markdown.py`; update `test_markdown.py`.
2. **Relay client + command parser** — `connectors/relay.py`, `command_schema.py` (+ tests).
3. **Google Tasks connector** — `tasks.py` + Tasks scope; `GoogleMirror` task helpers.
4. **Command executor + `poll_commands`** — relay dispatch, completion + receipt,
   immediate re-reflect; tests with fakes.
5. **`_Commands` + `_Dashboard` Docs + Saved Info snippet** — generated in `mirror_out`.
6. **HTTP `/command` endpoint** — synchronous relay + auth; test.
7. **Tear down write-back** — remove `mirror_in.py` + drive/sheet-diff polling +
   echo/conflict tests; mark `notion/write.py` inactive; keep Notion→Google deletes.
8. **Docs** — README: new model, relay request format, the Saved Info snippet, ack behavior.

## Verification

Unit (temp SQLite + fakes; add a fake Tasks surface to `tests/fakes.py`):
- `test_markdown.py` — callout/toggle/table/image and **nested** lists render to the
  expected read Markdown; deep nesting captured.
- `test_command_schema.py` — parses `{method,path,body}` JSON, a bare body, and
  `key: value` lines; malformed input → error with the expected shape.
- `test_commands.py` (fake relay + fake Tasks) — a pending task is forwarded to the relay
  with the right method/path/body; the task is then **completed** with a `✓` receipt; a
  relay error yields a `✗` receipt; a completed task is **not** re-run; success triggers a
  re-reflect (mirror_item or poll_notion).
- `test_mirror_out.py` — still builds the tree; `_Commands` + `_Dashboard` Docs list the
  relay request format and the live name↔id catalog.
- `test_webhook.py` — `/command` rejects without key (401/503); with key returns the
  result synchronously (executor monkeypatched).

End-to-end (after deploy with real creds + Tasks scope):
- Bootstrap → mirror appears; `_Commands`/`_Dashboard` Docs populated; paste the Saved
  Info snippet into Gemini.
- By voice: "add an action, email Bob, project PourDynamics engine, due Friday" → Gemini
  creates a `Notion Commands` task → within ~30s the Notion Action exists, the task is
  completed with a `✓` receipt, and `_Dashboard`/Docs reflect it.
- Ask "did that go through?" → Gemini reads the task receipt / refreshed Docs.
- `curl -X POST ".../command?key=$ADMIN_API_KEY" -d '{"method":"POST","path":"…","body":{…}}'`
  → relays to the existing API, returns its result, and Notion changes.
- Editing a reflected Doc by hand does **not** change Notion (next sync overwrites it).

All work on branch `claude/new-session-ows5lf`, committed and pushed. No PR unless asked.

## Open risks
- **Live schema adherence is best-effort** (Gems unavailable in Live; only Saved Info
  steers it). Mitigated by the tolerant parser + clear `✗` receipts echoing the schema;
  optional LLM fallback later if needed.
- **Google Tasks has a tiny schema** (title/notes/due/done) — it is only the *command
  transport*, not a data mirror; all real reading happens in Docs. We deliberately do
  **not** reflect Notion Actions into Tasks (decided: command-inbox only).
- **No Tasks push** → ~30s poll latency on commands; acceptable for the confirm-on-next-
  turn UX.
- **Name ambiguity**: duplicate names → `✗` receipt with candidate ids rather than guessing.
- **Inline databases in bodies**: rendered read-only (link / optional table); never writable.
