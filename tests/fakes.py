"""In-memory fakes for the Google and Notion adapters used by engine tests."""

from __future__ import annotations

import itertools
from typing import Any

from app.connectors.google.sheets import record_to_row, row_to_record
from app.connectors.notion.read import NotionItem


class FakeGoogleMirror:
    """In-memory stand-in that round-trips sheet rows like the real Sheets API
    (relation lists are joined to strings on write, split on read)."""

    def __init__(self, root_folder_id: str = "ROOT"):
        self.root_folder_id = root_folder_id
        self.index_sheet_id = "SHEET"
        self._ids = itertools.count(1)
        self.folders: dict[tuple[str, str], str] = {}
        self.folder_meta: dict[str, tuple[str, str]] = {}  # id -> (name, parent)
        self.docs: dict[str, str] = {}
        self.doc_meta: dict[str, tuple[str, str]] = {}  # id -> (name, parent)
        # Each tab holds raw row lists, exactly like the Sheets backend.
        self.tabs: dict[str, list[list[str]]] = {"Areas": [], "Projects": [], "Actions": []}
        self.write_doc_calls = 0
        self.append_calls = 0
        self.structure_ready = False
        # Command inbox (Google Tasks) surface.
        self.tasks: list[dict] = []
        self.finished: list[tuple[str, str]] = []  # (task_id, receipt)

    def ensure_index_structure(self) -> None:
        self.structure_ready = True

    def ensure_root(self) -> str:
        return self.root_folder_id

    def ensure_folder(self, name: str, parent_id: str) -> str:
        key = (parent_id, name)
        if key not in self.folders:
            fid = f"F{next(self._ids)}"
            self.folders[key] = fid
            self.folder_meta[fid] = (name, parent_id)
        return self.folders[key]

    def create_folder(self, name: str, parent_id: str) -> str:
        """Always a fresh folder (per-item; no name dedup)."""
        fid = f"F{next(self._ids)}"
        self.folder_meta[fid] = (name, parent_id)
        return fid

    def create_doc(self, name: str, parent_id: str) -> str:
        did = f"D{next(self._ids)}"
        self.doc_meta[did] = (name, parent_id)
        self.docs.setdefault(did, "")
        return did

    def ensure_doc(self, name: str, parent_id: str) -> str:
        for did, (n, p) in self.doc_meta.items():
            if n == name and p == parent_id:
                return did
        return self.create_doc(name, parent_id)

    def is_live(self, file_id: str) -> bool:
        return file_id in self.folder_meta or file_id in self.doc_meta

    def drive_tree(self, folder_id: str | None = None, depth: int = 0, max_depth: int = 6) -> dict:
        folder_id = folder_id or self.root_folder_id
        node: dict = {"id": folder_id, "type": "folder", "children": []}
        if depth >= max_depth:
            return node
        for fid, (nm, parent) in self.folder_meta.items():
            if parent == folder_id:
                sub = self.drive_tree(fid, depth + 1, max_depth)
                sub["name"] = nm
                node["children"].append(sub)
        for did, (nm, parent) in self.doc_meta.items():
            if parent == folder_id:
                node["children"].append({"id": did, "type": "doc", "name": nm})
        return node

    def write_doc(self, doc_id: str, markdown: str) -> None:
        self.docs[doc_id] = markdown
        self.write_doc_calls += 1

    def read_doc(self, doc_id: str) -> str:
        return self.docs.get(doc_id, "")

    def doc_url(self, doc_id: str) -> str:
        return f"https://docs.google.com/document/d/{doc_id}/edit"

    def rename(self, file_id: str, name: str) -> None:
        if file_id in self.folder_meta:
            self.folder_meta[file_id] = (name, self.folder_meta[file_id][1])
        elif file_id in self.doc_meta:
            self.doc_meta[file_id] = (name, self.doc_meta[file_id][1])

    def move(self, file_id: str, parent_id: str) -> None:
        if file_id in self.folder_meta:
            self.folder_meta[file_id] = (self.folder_meta[file_id][0], parent_id)

    def trash(self, file_id: str) -> None:
        self.docs.pop(file_id, None)
        self.doc_meta.pop(file_id, None)
        self.folder_meta.pop(file_id, None)

    def clear_row(self, tab: str, notion_id: str) -> None:
        self.tabs[tab] = [
            r for r in self.tabs[tab] if row_to_record(tab, r).get("_notion_id") != notion_id
        ]

    def read_tab(self, tab: str) -> list[dict[str, Any]]:
        out = []
        for i, row in enumerate(self.tabs[tab], start=2):
            rec = row_to_record(tab, row)
            rec["_row"] = i
            out.append(rec)
        return out

    def seed_row(self, tab: str, record: dict[str, Any]) -> None:
        """Test helper: add a Google-authored row exactly as the UI would."""
        self.tabs[tab].append(record_to_row(tab, record))

    def upsert_row(self, tab: str, notion_id: str, record: dict[str, Any]) -> int:
        new_row = record_to_row(tab, record)
        for i, existing in enumerate(self.tabs[tab]):
            if row_to_record(tab, existing).get("_notion_id") == notion_id:
                self.tabs[tab][i] = new_row
                return i + 2
        self.tabs[tab].append(new_row)
        self.append_calls += 1
        return len(self.tabs[tab]) + 1

    def update_row_at(self, tab: str, row_number: int, record: dict[str, Any]) -> None:
        self.tabs[tab][row_number - 2] = record_to_row(tab, record)

    # --- command inbox ---
    def add_command(self, notes: str, title: str = "cmd", task_id: str | None = None) -> dict:
        task = {"id": task_id or f"t{next(self._ids)}", "title": title, "notes": notes}
        self.tasks.append(task)
        return task

    def pending_commands(self) -> list[dict]:
        out = []
        for t in self.tasks:
            if t.get("status") == "completed":
                continue
            notes = t.get("notes") or ""
            if notes.lstrip().startswith(("✓", "✗")):
                continue
            # Mirror production's default-list behaviour: only JSON-shaped tasks are
            # commands, so personal tasks on the shared list are left untouched.
            text = notes.strip() or (t.get("title") or "").strip()
            if not text.startswith(("{", "[")):
                continue
            out.append(t)
        return out

    def finish_command(self, task: dict, receipt: str) -> None:
        task["status"] = "completed"
        task["notes"] = f"{receipt}\n---\n{task.get('notes') or ''}".strip()
        self.finished.append((task["id"], receipt))


class FakeNotionSource:
    def __init__(self, items: dict[str, NotionItem], bodies: dict[str, str],
                 children: dict[str, list[str]], spine_ids: list[str], loose_ids: list[str]):
        self.items = items
        self.bodies = bodies
        self.children = children
        self.spine_ids = spine_ids
        self.loose_ids = loose_ids
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.replaced: list[dict] = []
        self.archived: list[str] = []
        self._new = itertools.count(1)

    def spine_items(self) -> list[NotionItem]:
        return [self.items[i] for i in self.spine_ids]

    def loose_items(self) -> list[NotionItem]:
        return [self.items[i] for i in self.loose_ids]

    def body_markdown(self, page_id: str) -> str:
        return self.bodies.get(page_id, "")

    def child_page_ids(self, page_id: str) -> list[str]:
        return self.children.get(page_id, [])

    def get_item(self, page_id: str) -> NotionItem:
        return self.items[page_id]

    # --- write surface (mirror_in) ---
    def create_page(self, parent, properties, children=None):
        new_id = f"new-{next(self._new)}"
        self.created.append(
            {"id": new_id, "parent": parent, "properties": properties, "children": children}
        )
        return {"id": new_id}

    def update_properties(self, page_id, properties):
        self.updated.append({"id": page_id, "properties": properties})
        return {"id": page_id}

    def replace_body(self, page_id, markdown):
        self.replaced.append({"id": page_id, "markdown": markdown})

    def archive(self, page_id):
        self.archived.append(page_id)

    @staticmethod
    def build_properties(kind, values, relation_ids):
        from app.connectors.notion.write import build_properties

        return build_properties(kind, values, relation_ids)


class FakeRelay:
    """Records forwarded requests and returns a scripted result."""

    def __init__(self, result=None):
        from app.connectors.relay import RelayResult

        self.calls: list = []
        self.result = result or RelayResult(ok=True, status=200, summary="ok", affected_id=None)

    def execute(self, req):
        self.calls.append(req)
        return self.result


def make_item(notion_id, kind, title, properties=None, relations=None,
              parent_id=None, last_edited="2026-06-21T09:00:00.000Z") -> NotionItem:
    return NotionItem(
        notion_id=notion_id,
        kind=kind,
        title=title,
        properties=properties or {},
        relations=relations or {},
        parent_id=parent_id,
        parent_type=None,
        last_edited_time=last_edited,
        last_edited_by="human-user",
    )
