"""Google → Notion mirroring.

Two inbound channels:

* **Sheets** — each tab is diffed against the ledger. New rows become Notion
  pages (relation names resolved to ids); edited rows update Notion properties.
* **Drive changes** — an edited Doc updates its Notion page body; a *new* Doc
  created under a mirrored folder becomes a new Notion sub-page; a trashed Doc
  archives its Notion page. Docs at the Drive root (unmirrored) are ignored.

Everything passes through the echo pipeline so our own writes do not bounce back.
"""

from __future__ import annotations

from sqlmodel import Session

from app.config import (
    ACTIONS_DS_ID,
    AREAS_DS_ID,
    PROJECTS_DS_ID,
    Settings,
    get_settings,
)
from app.connectors.google import drive as gdrive
from app.connectors.google.sheets import (
    KIND_TO_TAB,
    RELATION_COLUMNS,
    TAB_TO_KIND,
    split_relation,
)
from app.core import echo
from app.core.hashing import body_hash, property_hash
from app.engines import resolve
from app.engines.google_mirror import GoogleMirror
from app.engines.notion_source import NotionSource
from app.ledger import repo
from app.logging import get_logger

log = get_logger(__name__)

_KIND_DS = {"area": AREAS_DS_ID, "project": PROJECTS_DS_ID, "action": ACTIONS_DS_ID}


class MirrorIn:
    def __init__(
        self,
        session: Session,
        notion: NotionSource,
        google: GoogleMirror,
        settings: Settings | None = None,
    ):
        self.session = session
        self.notion = notion
        self.google = google
        self.settings = settings or get_settings()

    # --- Sheets (structured facet) ------------------------------------------

    def sync_sheets(self) -> int:
        """Reconcile all three tabs against Notion. Returns rows propagated."""
        _, title_to_id = resolve.build_indexes(self.session)
        propagated = 0
        for tab in ("Areas", "Projects", "Actions"):
            kind = TAB_TO_KIND[tab]
            for record in self.google.read_tab(tab):
                if self._sync_row(kind, tab, record, title_to_id):
                    propagated += 1
        return propagated

    def _resolve_relations(self, kind: str, record: dict, title_to_id) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for col in RELATION_COLUMNS[KIND_TO_TAB[kind]]:
            names = split_relation(record.get(col, ""))
            target = resolve.RELATION_TARGET_KIND.get((kind, col))
            if target is None:
                continue
            ids, _unresolved = resolve.ids_for(names, target, title_to_id)
            out[col] = ids
        return out

    def _sync_row(self, kind: str, tab: str, record: dict, title_to_id) -> bool:
        notion_id = (record.get("_notion_id") or "").strip()
        incoming_hash = property_hash(kind, record)

        if notion_id:
            pair = repo.get_pair_by_notion_id(self.session, notion_id)
            ev = echo.SyncEvent("google", "property", incoming_hash)
            if not echo.should_propagate(self.session, pair, ev, self.settings).propagate:
                return False
            relation_ids = self._resolve_relations(kind, record, title_to_id)
            properties = self.notion.build_properties(kind, record, relation_ids)
            self.notion.update_properties(notion_id, properties)
            echo.record_source(self.session, pair, ev)
            echo.mark_propagated(
                self.session, pair, "notion", "property", incoming_hash, self.settings
            )
            log.info("updated Notion %s from sheet row (%s)", kind, notion_id)
            return True

        # New row authored on the Google side (e.g. by the assistant).
        title = self._row_title(kind, record)
        if not title:
            return False
        relation_ids = self._resolve_relations(kind, record, title_to_id)
        properties = self.notion.build_properties(kind, record, relation_ids)
        created = self.notion.create_page({"database_id": _KIND_DS[kind]}, properties)
        new_id = created["id"]

        pair = repo.upsert_pair(
            self.session, new_id, kind=kind, title=title,
            gsheet_tab=tab, gsheet_row_key=new_id,
        )
        echo.record_source(
            self.session, pair, echo.SyncEvent("google", "property", incoming_hash)
        )
        # The create will echo back on the next Notion poll; pre-arm suppression.
        echo.mark_propagated(
            self.session, pair, "notion", "property", incoming_hash, self.settings
        )
        # Write the new notion id back into the *same* sheet row so future edits
        # match (updating in place, not appending a duplicate).
        record["_notion_id"] = new_id
        record["_hash"] = incoming_hash
        self.google.update_row_at(tab, record["_row"], record)
        log.info("created Notion %s from new sheet row (%s)", kind, title)
        return True

    @staticmethod
    def _row_title(kind: str, record: dict) -> str:
        col = {"area": "Name", "project": "Project", "action": "Name"}[kind]
        return (record.get(col) or "").strip()

    # --- Drive (body facet + new sub-pages) ---------------------------------

    def sync_drive(self, changes: list[dict]) -> int:
        """Process Drive change records. Returns the number propagated to Notion."""
        propagated = 0
        for change in changes:
            if self._sync_change(change):
                propagated += 1
        return propagated

    def _sync_change(self, change: dict) -> bool:
        file = change.get("file") or {}
        file_id = change.get("fileId") or file.get("id")
        removed = change.get("removed") or file.get("trashed")

        if removed:
            return self._handle_removed(file_id)

        mime = file.get("mimeType")
        if mime != gdrive.DOC_MIME:
            return False  # folders/sheets handled elsewhere or ignored

        pair = repo.get_pair_by_gdoc_id(self.session, file_id)
        if pair is not None:
            return self._update_body(pair, file_id)
        return self._maybe_new_subpage(file)

    def _handle_removed(self, file_id: str) -> bool:
        pair = repo.get_pair_by_gdoc_id(self.session, file_id)
        if pair is None:
            return False
        self.notion.archive(pair.notion_id)
        repo.tombstone_pair(self.session, pair)
        log.info("archived Notion page for trashed Doc (%s)", pair.notion_id)
        return True

    def _update_body(self, pair, file_id: str) -> bool:
        markdown = self.google.read_doc(file_id)
        b_hash = body_hash(markdown)
        ev = echo.SyncEvent("google", "body", b_hash)
        if not echo.should_propagate(self.session, pair, ev, self.settings).propagate:
            return False
        self.notion.replace_body(pair.notion_id, markdown)
        echo.record_source(self.session, pair, ev)
        echo.mark_propagated(self.session, pair, "notion", "body", b_hash, self.settings)
        log.info("updated Notion body from Doc (%s)", pair.notion_id)
        return True

    def _maybe_new_subpage(self, file: dict) -> bool:
        parents = file.get("parents") or []
        for parent_folder in parents:
            parent_pair = repo.get_pair_by_drive_folder(self.session, parent_folder)
            if parent_pair is None:
                continue
            title = file.get("name") or "Untitled"
            markdown = self.google.read_doc(file["id"])
            from app.core.markdown import markdown_to_notion_blocks

            created = self.notion.create_page(
                {"page_id": parent_pair.notion_id},
                {"title": {"title": [{"text": {"content": title}}]}},
                markdown_to_notion_blocks(markdown),
            )
            new_id = created["id"]
            b_hash = body_hash(markdown)
            pair = repo.upsert_pair(
                self.session, new_id, kind="page", title=title,
                gdoc_id=file["id"], drive_parent_id=parent_folder,
            )
            echo.record_source(self.session, pair, echo.SyncEvent("google", "body", b_hash))
            echo.mark_propagated(self.session, pair, "notion", "body", b_hash, self.settings)
            log.info("created Notion sub-page from new Doc under %s", parent_pair.notion_id)
            return True
        # Doc at an unmirrored location (e.g. the Drive root): ignore.
        return False
