"""Google Tasks as the command inbox.

Gemini Live can create/edit/complete tasks by voice, so one Tasks list
(``Notion Commands``) is the mutation channel. The executor reads pending (not yet
completed) tasks, runs them, then marks each completed and prepends a receipt to
its notes. A small marker prefix on the notes lets us recognize already-processed
tasks even before completion propagates.
"""

from __future__ import annotations

RECEIPT_OK = "✓"
RECEIPT_ERR = "✗"
_RECEIPT_MARKERS = (RECEIPT_OK, RECEIPT_ERR)


def ensure_command_list(tasks, name: str) -> str:
    """Find-or-create the command tasklist; return its id."""
    resp = tasks.tasklists().list(maxResults=100).execute()
    for tl in resp.get("items", []):
        if tl.get("title") == name:
            return tl["id"]
    created = tasks.tasklists().insert(body={"title": name}).execute()
    return created["id"]


def list_pending(tasks, tasklist_id: str) -> list[dict]:
    """Return tasks that are not completed and not already receipted."""
    resp = (
        tasks.tasks()
        .list(tasklist=tasklist_id, showCompleted=False, showHidden=False, maxResults=100)
        .execute()
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
    tasks.tasks().patch(tasklist=tasklist_id, task=task["id"], body=body).execute()


def command_text(task: dict) -> str:
    """The text to parse: prefer notes, fall back to the title."""
    return (task.get("notes") or "").strip() or (task.get("title") or "").strip()
