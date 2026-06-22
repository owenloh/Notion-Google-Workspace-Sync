# Gemini "Saved Info" snippet

Paste the text below into Gemini → Settings → **Saved info** (personal context).
It is intentionally tiny — the full, always-current details (request shapes + the
live name→id catalog) live in the `_Commands` Google Doc, which Gemini reads on
demand.

---

## How to change my Notion (areas / projects / actions / notes)

You cannot edit Notion directly. You make every change by writing **one Google
Task** that my sync service relays. Always, in order:

1. Read my Google Doc named **`_Commands`** for the exact request shape and the live
   name→id catalog (and **`_Dashboard`** for a compact list of ids).
2. Create a normal task in my Google Tasks (the default list is fine) with the JSON
   request in the task's **notes**. **One task = one change.** The notes must start
   with `{` (a JSON object); plain tasks are ignored.
3. Tell me you've queued it. Don't assume it worked: if I ask, re-read that task's
   notes for the **`✓`** (done) or **`✗`** (failed) receipt — it appears within ~1
   minute. Only make a follow-up change that *depends* on this one **after** you see
   `✓`.

## What you can do (inside the JSON request)
- Add an action / project / sub-page; set status, due date, checkbox, or relation;
  append a note to a page.
- **Rewrite or clean up a page's text:** read that page's Doc in the `Notion Mirror`
  folder, then send an `update_content` request with the old text as `old_str` and
  your refined text as `new_str`.
- Add / complete / clear items in my **Microsoft To-Do** in-tray.

## What you must NOT do
- **Don't delete or archive** Notion pages — it's not supported; tell me and I'll do
  it by hand.
- **Don't wipe a whole page body** (`replace_content`); use `update_content` instead.
- **Don't rename a sub-page by title**, and **don't edit the `Notion Mirror` Docs to
  change Notion** — those Docs are read-only reflections; use them only to read and
  answer questions about my Notion.

If you're unsure of an id or the exact shape, re-read `_Commands` instead of
guessing.
