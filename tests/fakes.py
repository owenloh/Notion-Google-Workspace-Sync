"""In-memory fakes for the Google and Notion adapters used by engine tests."""

from __future__ import annotations

import itertools
from typing import Any

from app.connectors.notion.read import NotionItem


class FakeGoogleMirror:
    def __init__(self, root_folder_id: str = "ROOT"):
        self.root_folder_id = root_folder_id
        self.index_sheet_id = "SHEET"
        self._ids = itertools.count(1)
        self.folders: dict[tuple[str, str], str] = {}
        self.folder_meta: dict[str, tuple[str, str]] = {}  # id -> (name, parent)
        self.docs: dict[str, str] = {}
        self.doc_meta: dict[str, tuple[str, str]] = {}  # id -> (name, parent)
        self.tabs: dict[str, list[dict[str, Any]]] = {"Areas": [], "Projects": [], "Actions": []}
        self.write_doc_calls = 0
        self.append_calls = 0
        self.structure_ready = False

    def ensure_index_structure(self) -> None:
        self.structure_ready = True

    def ensure_folder(self, name: str, parent_id: str) -> str:
        key = (parent_id, name)
        if key not in self.folders:
            fid = f"F{next(self._ids)}"
            self.folders[key] = fid
            self.folder_meta[fid] = (name, parent_id)
        return self.folders[key]

    def ensure_doc(self, name: str, parent_id: str) -> str:
        for did, (n, p) in self.doc_meta.items():
            if n == name and p == parent_id:
                return did
        did = f"D{next(self._ids)}"
        self.doc_meta[did] = (name, parent_id)
        self.docs.setdefault(did, "")
        return did

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

    def move(self, file_id: str, parent_id: str) -> None:
        if file_id in self.folder_meta:
            self.folder_meta[file_id] = (self.folder_meta[file_id][0], parent_id)

    def trash(self, file_id: str) -> None:
        self.docs.pop(file_id, None)
        self.doc_meta.pop(file_id, None)
        self.folder_meta.pop(file_id, None)

    def read_tab(self, tab: str) -> list[dict[str, Any]]:
        out = []
        for i, rec in enumerate(self.tabs[tab], start=2):
            r = dict(rec)
            r["_row"] = i
            out.append(r)
        return out

    def upsert_row(self, tab: str, notion_id: str, record: dict[str, Any]) -> int:
        for i, rec in enumerate(self.tabs[tab]):
            if rec.get("_notion_id") == notion_id:
                self.tabs[tab][i] = dict(record)
                return i + 2
        self.tabs[tab].append(dict(record))
        self.append_calls += 1
        return len(self.tabs[tab]) + 1


class FakeNotionSource:
    def __init__(self, items: dict[str, NotionItem], bodies: dict[str, str],
                 children: dict[str, list[str]], spine_ids: list[str], loose_ids: list[str]):
        self.items = items
        self.bodies = bodies
        self.children = children
        self.spine_ids = spine_ids
        self.loose_ids = loose_ids

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
