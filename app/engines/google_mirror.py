"""High-level Google-side operations the engines depend on.

This adapter wraps the Drive/Docs/Sheets connectors behind verbs the engines
speak ("ensure a folder", "upsert a sheet row by notion id", "write a doc"). The
engines depend only on this surface, so tests can substitute an in-memory fake.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError

from app.connectors.google import docs as gdocs
from app.connectors.google import drive as gdrive
from app.connectors.google import sheets as gsheets
from app.connectors.google import tasks as gtasks
from app.connectors.google.auth import GoogleServices
from app.logging import get_logger

log = get_logger(__name__)


class GoogleMirror:
    def __init__(
        self,
        services: GoogleServices,
        root_folder_id: str,
        index_sheet_id: str,
        command_tasklist_name: str = "Notion Commands",
    ):
        self.services = services
        self.root_folder_id = root_folder_id
        self.index_sheet_id = index_sheet_id
        self.command_tasklist_name = command_tasklist_name
        self._command_list_id: str | None = None
        # Per-sync sheet row index ({tab: {notion_id: row}}) + next free row, so
        # upserts don't read the whole tab per row (which blows the Sheets API
        # 60-reads/min quota during a full sync). Reset at the start of sync_all.
        self._tab_index: dict[str, dict[str, int]] = {}
        self._tab_next_row: dict[str, int] = {}

    # --- self-healing root folder + index sheet ---
    def _is_live(self, file_id: str) -> bool:
        """True if the id exists and isn't trashed."""
        if not file_id:
            return False
        try:
            meta = self.services.drive.files().get(
                fileId=file_id, fields="id,trashed", supportsAllDrives=True
            ).execute()
            return not meta.get("trashed", False)
        except HttpError:
            return False

    def ensure_root(self) -> str:
        """Return a live mirror root folder id, recreating it if missing/trashed.

        If the configured ``GOOGLE_DRIVE_MIRROR_FOLDER_ID`` was deleted, fall back
        to find-or-create "Notion Mirror" at My Drive root (idempotent by name).
        """
        if self._is_live(self.root_folder_id):
            return self.root_folder_id
        new_id = gdrive.ensure_folder(self.services.drive, "Notion Mirror", "root")
        if new_id != self.root_folder_id:
            log.warning(
                "mirror folder %s missing/trashed; using 'Notion Mirror' folder %s "
                "(update GOOGLE_DRIVE_MIRROR_FOLDER_ID to persist)",
                self.root_folder_id, new_id,
            )
            self.root_folder_id = new_id
        return new_id

    def ensure_index_sheet(self) -> str:
        """Return a live index-sheet id, recreating it under the root if needed."""
        if self._is_live(self.index_sheet_id):
            return self.index_sheet_id
        root = self.ensure_root()
        existing = gdrive.find_child(self.services.drive, root, "_Notion Index", gdrive.SHEET_MIME)
        new_id = existing or self.services.drive.files().create(
            body={"name": "_Notion Index", "mimeType": gdrive.SHEET_MIME, "parents": [root]},
            fields="id",
        ).execute()["id"]
        if new_id != self.index_sheet_id:
            log.warning(
                "index sheet %s missing/trashed; using %s "
                "(update GOOGLE_INDEX_SHEET_ID to persist)",
                self.index_sheet_id, new_id,
            )
            self.index_sheet_id = new_id
        return new_id

    # --- folders / docs ---
    def ensure_folder(self, name: str, parent_id: str) -> str:
        return gdrive.ensure_folder(self.services.drive, name, parent_id)

    def ensure_doc(self, name: str, parent_id: str) -> str:
        existing = gdrive.find_child(self.services.drive, parent_id, name, gdrive.DOC_MIME)
        return existing or gdrive.create_doc(self.services.drive, name, parent_id)

    def write_doc(self, doc_id: str, markdown: str) -> None:
        gdocs.write_markdown(self.services.docs, doc_id, markdown)

    def read_doc(self, doc_id: str) -> str:
        return gdocs.read_markdown(self.services.docs, doc_id)

    def doc_url(self, doc_id: str) -> str:
        return gdrive.doc_url(doc_id)

    def drive_tree(self, folder_id: str | None = None, depth: int = 0, max_depth: int = 6) -> dict:
        """Walk the mirror folder into a nested {name,type,id,children} tree (diagnostic)."""
        folder_id = folder_id or self.ensure_root()
        node: dict[str, Any] = {"id": folder_id, "type": "folder", "children": []}
        if depth >= max_depth:
            return node
        for child in gdrive.list_children(self.services.drive, folder_id):
            is_folder = child.get("mimeType") == gdrive.FOLDER_MIME
            if is_folder:
                sub = self.drive_tree(child["id"], depth + 1, max_depth)
                sub["name"] = child["name"]
                node["children"].append(sub)
            else:
                kind = "doc" if child.get("mimeType") == gdrive.DOC_MIME else "file"
                node["children"].append(
                    {"name": child["name"], "type": kind, "id": child["id"]}
                )
        return node

    def rename(self, file_id: str, name: str) -> None:
        gdrive.rename_file(self.services.drive, file_id, name)

    def move(self, file_id: str, parent_id: str) -> None:
        gdrive.move_file(self.services.drive, file_id, parent_id)

    def trash(self, file_id: str) -> None:
        gdrive.trash_file(self.services.drive, file_id)

    # --- index sheet ---
    def ensure_index_structure(self) -> None:
        self.ensure_root()  # heal the root first (sheet is created under it)
        sheet_id = self.ensure_index_sheet()
        gsheets.ensure_structure(self.services.sheets, sheet_id)
        self.reset_sheet_cache()  # force a fresh single read per tab this sync

    def read_tab(self, tab: str) -> list[dict[str, Any]]:
        return gsheets.read_records(self.services.sheets, self.index_sheet_id, tab)

    def reset_sheet_cache(self) -> None:
        self._tab_index = {}
        self._tab_next_row = {}

    def _prime_tab(self, tab: str) -> None:
        """Read a tab once and build a {notion_id: row} index + next free row."""
        records = self.read_tab(tab)
        self._tab_index[tab] = {
            r["_notion_id"]: r["_row"] for r in records if r.get("_notion_id")
        }
        self._tab_next_row[tab] = max((r["_row"] for r in records), default=1) + 1

    def upsert_row(self, tab: str, notion_id: str, record: dict[str, Any]) -> int:
        """Update the row matching ``notion_id``, else write a new one. Returns row #.

        Uses a cached per-tab index so a full sync does ONE read per tab instead
        of one (or two) per row — otherwise the Sheets 60-reads/min quota is hit.
        """
        if tab not in self._tab_index:
            self._prime_tab(tab)
        index = self._tab_index[tab]
        row = index.get(notion_id)
        if row is None:
            row = self._tab_next_row[tab]
            self._tab_next_row[tab] = row + 1
            index[notion_id] = row
        gsheets.update_record(self.services.sheets, self.index_sheet_id, tab, row, record)
        return row

    def update_row_at(self, tab: str, row_number: int, record: dict[str, Any]) -> None:
        gsheets.update_record(self.services.sheets, self.index_sheet_id, tab, row_number, record)

    # --- command inbox (Google Tasks) ---
    def command_list_id(self) -> str:
        if self._command_list_id is None:
            self._command_list_id = gtasks.ensure_command_list(
                self.services.tasks, self.command_tasklist_name
            )
        return self._command_list_id

    def pending_commands(self) -> list[dict]:
        return gtasks.list_pending(self.services.tasks, self.command_list_id())

    def finish_command(self, task: dict, receipt: str) -> None:
        gtasks.complete_with_receipt(self.services.tasks, self.command_list_id(), task, receipt)
