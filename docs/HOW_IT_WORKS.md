# How it works — dense reference

One-glance map of **information flow, timing, the mirror structure, and limits**.
For setup see `README.md`; for the live command schema see the generated `_Commands`
Doc.

## Direction of flow (asymmetric by design)

```
READS   Notion ──(one-way, read-only)──▶ Google Drive Docs        (Gemini reads Docs)
WRITES  Gemini voice ▶ Google Task (JSON) ▶ this relay ▶ Alistair API ▶ Notion
                                                         └▶ then re-reflect that page ▶ Drive
```
- Google is **never** the source of truth. This service's Notion token is **read-only**;
  all writes go through the external Alistair API (allowlisted paths only).
- Write confirmation is a **follow-up turn** ("did that go through?") — Gemini Live
  can't block mid-turn; it reads the `✓`/`✗` receipt written into the task's notes.

## Sync layers & timing

| Layer | Trigger / cadence | Does | Lock |
| --- | --- | --- | --- |
| `poll_commands` | ~30 s | run command tasks across **all** Tasks lists (JSON-only) → relay → receipt → re-reflect the affected page | shared (skips if busy) |
| per-command re-reflect | instant | re-mirror just the page a command changed (any depth); intray cmd → refresh `_Intray` | within above |
| `poll_incremental` | ~2 min | Notion `/search` by `last_edited_time` → reflect **every changed page incl. deep sub-pages**; also regenerates `_Dashboard`/`_Commands` + sweeps **spine** deletions/archives | shared (skips if busy) |
| `full_reconcile` | **daily 04:00 (Europe/London)** | backstop only: **deep-page deletions**, orphan/section **prune**, drift heal, regen all Docs | held for its whole (~minutes) run |
| Notion webhook | optional (off) | near-instant hand-edit reflection | — |

**Latency cheat-sheet:** voice change ≈ 30–60 s · manual edit/rename/move (any depth)
≈ 2 min · deletions / brand-new deep subtree / orphan cleanup ≈ next 04:00.

Everything mirror-side is **serialized by one lock** (the Google client/`httplib2`
isn't thread-safe). So conflicts are about *timing/staleness*, never concurrent
corruption — but a command added while the daily reconcile runs waits until it ends.

## What lives where (Drive structure)

```
Drive: Notion Mirror/
  _Commands  (Doc)                  how-to + allowed paths + name→id catalog
  _Dashboard (Doc)                  compact Areas/Projects/Actions list + ids
  _Intray (Microsoft To-Do) (Doc)   read-only MS To-Do in-tray
  _Notion Index (Sheet)             Areas / Projects / Actions tabs (rows)
  Areas/<Area>/<Area>.gdoc          area body
            <child sub-page>/…       area's Notion child pages (recursed)
            <Project>/<Project>.gdoc projects placed here by their `Area` relation
                 <child sub-page>/…  project's child pages (recursed)
  Briefing/  Horizons/  Library/     loose-root sections (+ their recursed children)
```

| Notion thing | Mirrored as | Where |
| --- | --- | --- |
| Area | folder + body Doc | `Areas/<Area>/` |
| Project | folder + body Doc | under its **Area**'s folder (relation), else `Areas/_Unsorted/` |
| Action | **sheet row only** (no folder/Doc) | `_Notion Index` → `Actions` tab |
| Page / sub-page | folder + body Doc | under its Notion **parent's** folder (recursed) |
| Loose roots (Brief, Horizons, Library) | section folder + Doc | top level; children recursed |
| "Unorganised References" | normal sub-page | **under Library** (it's a Library child, not a section) |

**Placement nuance:** child pages land under their parent by **Notion parent-child**
(recursion); projects land under an area by the **`Area` relation**. Both can coexist
in one `<Area>/` dir.

**Multi-relations:** a Drive folder has one parent, so a project with **two Areas**
is placed under the **first** Area only (not duplicated); an action with **two
Projects** has no folder anyway. The *full* relation is preserved as **data** — the
sheet/`_Dashboard` columns list **all** related names — so nothing is lost, the tree
just picks one primary home.

**Body fidelity:** a page's body Doc renders nested content (callouts, toggles,
tables, **columns**). A sub-page appears in the body as a named marker —
`> 📄 Sub-page: <title> … (id <id>)` — not the sub-page's content (that's its own Doc
in the same folder); the name + id let Gemini locate it via `_Dashboard`.

## Identity & self-healing (addressed by ledger id, not name)

- **Rename** → folder/Doc renamed *in place* (same Drive id, no orphan).
- **Move** (re-parent) → relocated in place.
- **Duplicate names** → distinct folders (new items never collide).
- **Delete/archive** → detected at the daily reconcile (re-fetched to confirm), then
  Doc/folder trashed, sheet row cleared, pair tombstoned.
- **Orphan prune** → anything in Drive no ledger pair points to (depth ≥ 2) is trashed;
  obsolete **empty** top-level section folders are swept too. Root, current sections
  (`Areas`/`Briefing`/`Horizons`/`Library`) and meta Docs are never touched.
- Root folder + index sheet **self-heal** if deleted.

## Limitations (know these)

- **Actions have no body/sub-pages mirrored.** Actions are sheet rows (name, status,
  due, project, checkbox). Notes or sub-pages *inside* an action are **not** reflected
  (the crawl recurses only areas/projects/loose roots). Parentless actions are fine —
  still a row.
- **No delete/archive by voice** — the Alistair API has no such endpoint (`✗
  unsupported`); delete by hand in Notion (reflected next 04:00).
- **No whole-body wipe** (`replace_content` blocked); rewrite text via `update_content`
  (read the Doc, send old→new). **Append** via `insert_content`.
- **Sub-page rename by title** via the relay clears the title (relay quirk); renaming
  database items (areas/projects/actions) is fine.
- **Deep *manual* edits** lag ~2 min (incremental); brand-new manual deep sub-pages and
  deletions wait for the daily reconcile. Command-driven changes are instant.
- Mirror Docs are **read-only reflections** — editing them never changes Notion and is
  overwritten on the next re-mirror.

## Notable internals (gotchas already handled)

- **Large pages** (>100 blocks): block reads paginate; the cursor goes as a **query
  param** for GET (`/blocks/{id}/children`) — sending it in the body 400s. So any big
  page (not just the References tray) now mirrors its full body.
- **Unreadable body**: a page whose blocks 400 degrades to a placeholder Doc rather than
  aborting the reconcile; a newer `Notion-Version` is tried as a fallback.
- **Commands land in any list**: Gemini Live can't reliably target a named list, so the
  poller scans **all** Tasks lists and treats only JSON-shaped tasks as commands —
  personal tasks are never touched.
- **Google 429 / Sheets**: row cache + exponential backoff; jobs serialized; 60 s HTTP
  timeout.
- **Deletion is verify-first**: each vanished page is re-fetched before removal, so a
  partial/failed crawl can't false-delete a live page.
