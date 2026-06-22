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
_Dashboard for ids. One task is one change. After queuing, tell me; if I ask whether
it worked, reread that task's notes for the done or failed receipt (about a minute).
Only do a dependent next change after it shows done.

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
name-to-id list. One task is one change. If I ask whether it worked, reread the task
notes for a done or failed receipt. You cannot delete or archive pages or wipe a
page body. To read my Notion, read the Docs in the Notion Mirror folder, never edit
them. You have live Drive and Tasks access; if a tool errors, retry instead of
saying you cannot access it, and never offer to help with something else.
