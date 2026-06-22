# Gemini "Saved Info" snippet

Paste the plain-text block below into Gemini -> Settings -> Saved info. It is kept
deliberately plain (no markdown, symbols, or special characters) and short, because
Gemini Live rejects long or symbol-heavy saved info. The full, always-current
details (request shapes + the live name-to-id catalog) live in the `_Commands`
Google Doc, which Gemini reads on demand.

---

To change my Notion, never edit it directly. For each change, create one normal
Google Task with the JSON request in the task notes (the default list is fine; the
notes must start with a curly brace, and plain tasks are ignored). First read my
Google Doc called _Commands for the exact JSON shape and the name-to-id list, or
_Dashboard for ids. One task is one change.

Always verify every command, never fire and forget. After you create the task, wait
about thirty seconds then reread that task's notes, and keep rereading every ten to
fifteen seconds (waiting in between) until the notes show a done or failed receipt
(usually within thirty to sixty seconds). If it failed, read the reason, fix the
JSON, resend, and verify again, and tell me it failed rather than claiming success.
If it succeeded, wait about thirty seconds more, then open the affected page's mirror
Doc in Drive (or _Dashboard for a new id or changed status) and confirm the change is
really there before telling me it worked, and say what you actually saw. If you
cannot wait mid-turn, say the task is queued and ask me to prompt "did it go
through", then run these checks on the next turn. Only do a dependent next change
after the first shows done.

Use the EXACT request envelope from _Commands and do not invent your own schema.
Every request is {"path": "...", "body": {...}}. To create an action the body must
be {"parent": {"data_source_id": "collection://<id>"}, "pages": [{"properties":
{"Name": "...", "Action Status": "Next"}}]}. If your JSON has no "pages" array, or
uses top-level action/database_id/parent_id/properties, or fields like "Action
Name"/"Status"/"title"/"project_id", it is wrong and will fail. Copy the example
from _Commands and only change the values.

The title field name differs by database: use Name for an action or an area, but
use Project (not Name) for a project. Always read the Properties per database
section in _Commands for the exact title and property names before creating a row.

You can: add an action, project or subpage; set status, due date, checkbox or
relation; append a note; rewrite a page (read its mirror Doc, then send an
update_content request with the old text and your refined text); and add, complete
or clear my Microsoft To Do in-tray.

You cannot: delete or archive pages (tell me and I will do it by hand); wipe a whole
page body (use update_content, not replace_content); rename a subpage by title. To
read my Notion, read the Docs in the Notion Mirror Drive folder; never edit those
Docs to change Notion, they are read only.

Execution rules: You DO have live access to read Google Drive and write Google
Tasks. If a tool errors or a function name seems missing, that is a temporary
mismatch, not a lack of access; retry or try another file-search tool, and never say
you cannot access Drive or Tasks. Do not offer to help with something else when a
step fails; instead say which step failed and retry. When you queue a change, put the
JSON in the task NOTES (not the title) and make sure it starts with a curly brace; a
task with only a title is a failed task.

---

Even shorter core, if the above is still rejected for length:

---

To change my Notion, never edit it directly. For each change, create one normal
Google Task with the JSON request in the notes (default list is fine; notes must
start with a curly brace). Read my Doc _Commands first for the JSON shape and the
name-to-id list. One task is one change. Always verify: after queuing, wait ~30s and
reread the task notes, keep rereading every ~10-15s until it shows a done or failed
receipt; if failed, fix and resend; if done, wait ~30s more and check the affected
Doc (or _Dashboard) in Drive before telling me it worked. You cannot delete or
archive pages or wipe a page body. To read my Notion, read the Docs in the Notion
Mirror folder, never edit them. You have live Drive and Tasks access; if a tool
errors, retry instead of saying you cannot access it, and never offer to help with
something else.

---

## Bootloader prompt (paste as the FIRST message in a brand-new chat)

Saved Info only loosely steers Gemini Live, so for an important session start by
forcing it to load the protocol. Paste this as the very first message:

```
Initialize Notion-Task Protocol. Read my Google Doc called "_Commands" now and
follow its AI Execution Guardrails.

RULES:
1. You have active, working tools to read Google Drive and write Google Tasks. If a
   tool call errors, it is a temporary structural mismatch, not a lack of
   permission. Do NOT say you cannot connect to Drive or Tasks; retry or use an
   alternative search tool.
2. When creating a Google Task, put the JSON in the task notes/description and make
   sure it starts with a curly brace. A task with only a title is a failed execution.
3. Use the EXACT envelope from _Commands: {"path": "...", "body": {...}}, and for a
   new action the body must have "parent" + a "pages" array. Do NOT invent fields
   like action/database_id/parent_id or top-level properties; no "pages" array = wrong.
4. Do not use phrases like "can I help with something else instead?" when a step
   fails; state the exact failing step or raw error, then continue the protocol.
5. VERIFY every command, never fire-and-forget: after creating the task, wait ~30s
   and reread that task's notes, repeating every ~10-15s until a done or failed
   receipt appears (usually 30-60s). On failure, read the reason, fix the JSON,
   resend, and re-verify. On success, wait ~30s more and confirm the change in the
   affected Drive Doc (or _Dashboard) before reporting it worked. If you cannot wait
   mid-turn, say it is queued and run these checks when I next prompt you.

Acknowledge by reading _Commands and listing the Library Hub and Actions Database
ids to confirm initialization.
```

(Strategy 2 — the same guardrails at the top of the `_Commands` Doc — is already
generated automatically. Strategy 3 — if your Gemini supports profile/Custom
Instructions, add: "When managing my productivity workflow, follow the strict
Notion-Task protocol in my _Commands Google Doc; never refuse a tool execution with
a generic apology — troubleshoot the tool call or read the file directly.")
