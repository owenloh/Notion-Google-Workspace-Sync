"""High-level Google-side operations the engines depend on.

This adapter wraps the Drive/Docs/Sheets connectors behind verbs the engines
speak ("ensure a folder", "upsert a sheet row by notion id", "write a doc"). The
engines depend only on this surface, so tests can substitute an in-memory fake.
"""

from __future__ import annotations

from typing import Any

from app.connectors.google import docs as gdocs
from app.connectors.google import drive as gdrive
from app.connectors.google import sheets as gsheets
from app.connectors.google.auth import GoogleServices


class GoogleMirror:
    def __init__(self, services: GoogleServices, root_folder_id: str, index_sheet_id: str):
        self.services = services
        self.root_folder_id = root_folder_id
        self.index_sheet_id = index_sheet_id

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

    def rename(self, file_id: str, name: str) -> None:
        gdrive.rename_file(self.services.drive, file_id, name)

    def move(self, file_id: str, parent_id: str) -> None:
        gdrive.move_file(self.services.drive, file_id, parent_id)

    def trash(self, file_id: str) -> None:
        gdrive.trash_file(self.services.drive, file_id)

    # --- index sheet ---
    def ensure_index_structure(self) -> None:
        gsheets.ensure_structure(self.services.sheets, self.index_sheet_id)

    def read_tab(self, tab: str) -> list[dict[str, Any]]:
        return gsheets.read_records(self.services.sheets, self.index_sheet_id, tab)

    def upsert_row(self, tab: str, notion_id: str, record: dict[str, Any]) -> int:
        """Update the row whose ``_notion_id`` matches, else append. Returns row #."""
        for existing in self.read_tab(tab):
            if existing.get("_notion_id") == notion_id:
                row = existing["_row"]
                gsheets.update_record(self.services.sheets, self.index_sheet_id, tab, row, record)
                return row
        gsheets.append_record(self.services.sheets, self.index_sheet_id, tab, record)
        # The appended row number is the next after current data rows.
        return len(self.read_tab(tab)) + 1

    def update_row_at(self, tab: str, row_number: int, record: dict[str, Any]) -> None:
        gsheets.update_record(self.services.sheets, self.index_sheet_id, tab, row_number, record)
