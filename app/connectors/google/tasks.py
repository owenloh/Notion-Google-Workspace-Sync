"""Google Tasks as the command inbox.

Gemini Live can create/edit/complete tasks by voice, so one Tasks list
(``Notion Commands``) is the mutation channel. The executor reads pending (not yet
completed) tasks, runs them, then marks each completed and prepends a receipt to
its notes. A small marker prefix on the notes lets us recognize already-processed
tasks even before completion propagates.
"""

from __future__ import annotations

from app.connectors.google._retry import execute as _exec

RECEIPT_OK = "✓"
RECEIPT_ERR = "✗"
_RECEIPT_MARKERS = (RECEIPT_OK, RECEIPT_ERR)


DEFAULT_TASKLIST = "@default"


def resolve_command_list(tasks, name: str) -> str:
    """Return the tasklist id for the command inbox.

    ``@default`` (or empty) → the user's primary list ("My Tasks"), which is what
    Gemini Live writes to. Any other value is a list *title* to find-or-create
    (legacy dedicated-list mode).
    """
    if not name or name == DEFAULT_TASKLIST:
        return DEFAULT_TASKLIST
    resp = _exec(tasks.tasklists().list(maxResults=100))
    for tl in resp.get("items", []):
        if tl.get("title") == name:
            return tl["id"]
    created = _exec(tasks.tasklists().insert(body={"title": name}))
    return created["id"]


def list_tasklists(tasks) -> list[dict]:
    """All of the user's task lists ([{id, title}, ...])."""
    return _exec(tasks.tasklists().list(maxResults=100)).get("items", [])


def _is_command_shaped(task: dict) -> bool:
    return command_text(task).lstrip().startswith(("{", "["))


def pending_commands_all(tasks) -> list[dict]:
    """Pending command tasks across EVERY task list.

    Gemini Live can't reliably target a named list and may write to any list, so we
    scan them all. Only JSON-shaped tasks (notes/title starting with ``{``/``[``)
    are treated as commands, so personal tasks are never picked up, completed, or
    receipted. Each task carries ``_tasklist`` so its receipt is written back to the
    list it lives in.
    """
    out: list[dict] = []
    for tl in list_tasklists(tasks):
        tid = tl.get("id")
        for task in list_pending(tasks, tid):
            if not _is_command_shaped(task):
                continue
            task["_tasklist"] = tid
            out.append(task)
    return out


def tasklists_overview(tasks) -> list[dict]:
    """Diagnostic: every list with its pending tasks (unfiltered) — shows exactly
    where commands are landing and whether they're JSON-shaped."""
    out: list[dict] = []
    for tl in list_tasklists(tasks):
        resp = _exec(
            tasks.tasks().list(tasklist=tl["id"], showCompleted=False, maxResults=100)
        )
        brief = [
            {
                "title": (t.get("title") or "")[:60],
                "notes": (t.get("notes") or "")[:120],
                "command_shaped": _is_command_shaped(t),
            }
            for t in resp.get("items", [])
        ]
        out.append(
            {"list": tl.get("title"), "id": tl.get("id"), "pending": len(brief), "tasks": brief}
        )
    return out


def all_command_tasks(tasks) -> list[dict]:
    """Diagnostic: command-shaped or already-receipted tasks across all lists,
    including completed (so receipts are visible). Each tagged with its list."""
    out: list[dict] = []
    for tl in list_tasklists(tasks):
        resp = _exec(
            tasks.tasks().list(
                tasklist=tl["id"], showCompleted=True, showHidden=True, maxResults=100
            )
        )
        for task in resp.get("items", []):
            notes = task.get("notes") or ""
            if _is_command_shaped(task) or notes.lstrip().startswith(_RECEIPT_MARKERS):
                task["_tasklist"] = tl["id"]
                task["_tasklist_title"] = tl.get("title")
                out.append(task)
    return out


def create_task(tasks, tasklist_id: str, title: str, notes: str) -> dict:
    """Insert a task into the list (used to test the command inbox path)."""
    return _exec(tasks.tasks().insert(tasklist=tasklist_id, body={"title": title, "notes": notes}))


def list_all(tasks, tasklist_id: str) -> list[dict]:
    """Return all tasks incl. completed/hidden (for inspecting receipts)."""
    resp = _exec(
        tasks.tasks().list(
            tasklist=tasklist_id, showCompleted=True, showHidden=True, maxResults=100
        )
    )
    return resp.get("items", [])


def list_pending(tasks, tasklist_id: str) -> list[dict]:
    """Return tasks in a list that are not completed and not already receipted."""
    resp = _exec(
        tasks.tasks().list(
            tasklist=tasklist_id, showCompleted=False, showHidden=False, maxResults=100
        )
    )
    out = []
    for task in resp.get("items", []):
        if task.get("status") == "completed":
            continue
        notes = task.get("notes") or ""
        if notes.lstrip().startswith(_RECEIPT_MARKERS):
            continue  # already processed, awaiting completion propagation
        out.append(task)
    return out


def complete_with_receipt(tasks, tasklist_id: str, task: dict, receipt: str) -> None:
    """Mark a task completed and prepend the receipt to its notes."""
    original = task.get("notes") or ""
    body = {
        "status": "completed",
        "notes": f"{receipt}\n---\n{original}".strip(),
    }
    _exec(tasks.tasks().patch(tasklist=tasklist_id, task=task["id"], body=body))


def command_text(task: dict) -> str:
    """The text to parse: prefer notes, fall back to the title."""
    return (task.get("notes") or "").strip() or (task.get("title") or "").strip()
