"""Notion → Google mirroring.

Builds and keeps current the ``_Notion Index`` sheet and the Drive folder tree of
Docs. Each item flows through the echo pipeline: we compute the Notion-side
property and body hashes, skip when nothing changed, and on a real change write
to Google and arm inflight markers so the bounce-back is suppressed.

Folder layout produced:

    <root>/Areas/<Area>/<Area>.doc              (+ recursive child pages)
    <root>/Areas/<Area>/<Project>/<Project>.doc (+ recursive child pages)
    <root>/References/<page>.doc
    <root>/Briefing/<page>.doc
"""

from __future__ import annotations

import httpx
from sqlmodel import Session

from app.config import Settings, get_settings
from app.connectors.notion.read import NotionItem
from app.core import echo
from app.core.hashing import body_hash, property_hash
from app.engines import resolve
from app.engines.google_mirror import GoogleMirror
from app.engines.notion_source import NotionSource
from app.ledger import repo
from app.ledger.models import SyncPair
from app.logging import get_logger

log = get_logger(__name__)


class MirrorOut:
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
        # notion_id -> drive folder id, for nesting projects under areas and
        # recursing child pages under their parent.
        self._folder_of: dict[str, str] = {}

    # --- public entry points -------------------------------------------------

    def sync_all(self) -> dict[str, int]:
        """Full crawl + mirror of the entire workspace. Returns counts by kind."""
        self.google.ensure_index_structure()
        spine = self.notion.spine_items()
        loose = self.notion.loose_items()

        extra = {it.notion_id: (it.kind, it.title) for it in spine + loose}
        id_to_title, _ = resolve.build_indexes(self.session, extra)

        areas_root = self.google.ensure_folder("Areas", self.google.root_folder_id)
        refs_root = self.google.ensure_folder("References", self.google.root_folder_id)
        brief_root = self.google.ensure_folder("Briefing", self.google.root_folder_id)

        counts = {"area": 0, "project": 0, "action": 0, "page": 0}

        by_kind = {"area": [], "project": [], "action": []}
        for it in spine:
            by_kind.setdefault(it.kind, []).append(it)

        for area in by_kind["area"]:
            self._mirror_body_item(area, areas_root, id_to_title)
            counts["area"] += 1
        for project in by_kind["project"]:
            parent = self._area_folder_for(project, areas_root, id_to_title)
            self._mirror_body_item(project, parent, id_to_title)
            counts["project"] += 1
        for action in by_kind["action"]:
            self._mirror_action(action, id_to_title)
            counts["action"] += 1

        for page in loose:
            root = brief_root if page.kind == "briefing" else refs_root
            self._mirror_body_item(page, root, id_to_title)

        counts["page"] = self._recurse_all_children(
            [it.notion_id for it in spine if it.kind in {"area", "project"}]
            + [it.notion_id for it in loose],
            id_to_title,
        )
        self._write_reference_docs(spine)
        return counts

    def _write_reference_docs(self, spine: list[NotionItem]) -> None:
        """(Re)generate the read-only `_Dashboard` and `_Commands` Docs.

        Change-gated by content hash so an unchanged catalog isn't rewritten.
        """
        from app.connectors.relay import fetch_skill_docs
        from app.engines.docs_gen import (
            CatalogEntry,
            build_commands_md,
            build_dashboard_md,
        )

        entries = [
            CatalogEntry(kind=it.kind, name=it.title, notion_id=it.notion_id)
            for it in spine
            if it.kind in {"area", "project", "action"}
        ]
        root = self.google.root_folder_id
        skills = fetch_skill_docs(self.settings)
        self._write_doc_if_changed("_Dashboard", root, build_dashboard_md(entries))
        self._write_doc_if_changed(
            "_Commands", root,
            build_commands_md(entries, self.settings.allowed_relay_paths, skills),
        )

    def _write_doc_if_changed(self, name: str, parent_id: str, markdown: str) -> None:
        doc_id = self.google.ensure_doc(name, parent_id)
        key = f"doc_hash:{name}"
        new_hash = body_hash(markdown)
        if repo.get_state(self.session, key) == new_hash:
            return
        self.google.write_doc(doc_id, markdown)
        repo.set_state(self.session, key, new_hash)

    def mirror_item(self, item: NotionItem) -> SyncPair | None:
        """Mirror a single item (event-driven path)."""
        id_to_title, _ = resolve.build_indexes(self.session)
        if item.kind == "action":
            return self._mirror_action(item, id_to_title)
        # Determine parent folder from the ledger (area for projects, else section).
        areas_root = self.google.ensure_folder("Areas", self.google.root_folder_id)
        parent = (
            self._area_folder_for(item, areas_root, id_to_title)
            if item.kind == "project"
            else self._section_folder_for(item)
        )
        return self._mirror_body_item(item, parent, id_to_title)

    # --- per-kind handlers ---------------------------------------------------

    def _mirror_body_item(
        self, item: NotionItem, parent_folder_id: str, id_to_title: dict[str, str]
    ) -> SyncPair:
        """Mirror an item that has a folder + body Doc (area/project/page)."""
        folder_id = self.google.ensure_folder(item.title or "Untitled", parent_folder_id)
        doc_id = self.google.ensure_doc(item.title or "Untitled", folder_id)
        self._folder_of[_norm(item.notion_id)] = folder_id

        pair = repo.get_pair_by_notion_id(self.session, item.notion_id)
        record = self._build_record(item, id_to_title, doc_id)
        # Only spine kinds (area/project/action) get a sheet row; loose pages
        # (briefing/reference) and generic child pages are body-only Docs.
        prop_hash = property_hash(item.kind, record) if item.kind in _TAB else None
        # Body fetch is best-effort: a page whose body can't be read must not
        # abort the whole reconcile, so degrade to a placeholder and carry on.
        try:
            markdown = self.notion.body_markdown(item.notion_id)
        except httpx.HTTPError as exc:
            log.warning("body unavailable for %s (%s); mirroring metadata only: %s",
                        item.title, item.notion_id, exc)
            markdown = f"_(body could not be read from Notion: {exc})_"
        b_hash = body_hash(markdown)

        pair = repo.upsert_pair(
            self.session,
            item.notion_id,
            kind=item.kind,
            title=item.title,
            drive_folder_id=folder_id,
            gdoc_id=doc_id,
            drive_parent_id=parent_folder_id,
        )

        # Body facet.
        if pair.notion_body_hash != b_hash:
            self.google.write_doc(doc_id, markdown)
            echo.record_source(
                self.session, pair,
                echo.SyncEvent("notion", "body", b_hash, edited_at=item.last_edited_time),
            )
            echo.mark_propagated(self.session, pair, "google", "body", b_hash, self.settings)

        # Property facet (spine items get a sheet row; generic pages do not).
        if prop_hash is not None:
            tab = _TAB[item.kind]
            if pair.notion_prop_hash != prop_hash or pair.gsheet_row_key != item.notion_id:
                row = self.google.upsert_row(tab, item.notion_id, record)
                repo.upsert_pair(
                    self.session, item.notion_id,
                    gsheet_tab=tab, gsheet_row_key=item.notion_id,
                )
                pair = repo.get_pair_by_notion_id(self.session, item.notion_id)
                echo.record_source(
                    self.session, pair,
                    echo.SyncEvent("notion", "property", prop_hash,
                                   edited_at=item.last_edited_time),
                )
                echo.mark_propagated(
                    self.session, pair, "google", "property", prop_hash, self.settings
                )
                log.info("mirrored %s row %s (%s)", item.kind, row, item.title)
        return pair

    def _mirror_action(self, item: NotionItem, id_to_title: dict[str, str]) -> SyncPair:
        """Actions are title-only: a sheet row, no folder/doc."""
        record = self._build_record(item, id_to_title, doc_id=None)
        prop_hash = property_hash("action", record)
        pair = repo.get_pair_by_notion_id(self.session, item.notion_id)
        if (
            pair is not None
            and pair.notion_prop_hash == prop_hash
            and pair.gsheet_row_key == item.notion_id
        ):
            return pair
        row = self.google.upsert_row("Actions", item.notion_id, record)
        pair = repo.upsert_pair(
            self.session, item.notion_id, kind="action", title=item.title,
            gsheet_tab="Actions", gsheet_row_key=item.notion_id,
        )
        echo.record_source(
            self.session, pair,
            echo.SyncEvent("notion", "property", prop_hash, edited_at=item.last_edited_time),
        )
        echo.mark_propagated(self.session, pair, "google", "property", prop_hash, self.settings)
        log.info("mirrored action row %s (%s)", row, item.title)
        return pair

    # --- recursion -----------------------------------------------------------

    def _recurse_all_children(self, parent_ids: list[str], id_to_title) -> int:
        count = 0
        stack = list(parent_ids)
        while stack:
            pid = stack.pop()
            parent_folder = self._folder_of.get(_norm(pid))
            if not parent_folder:
                continue
            try:
                child_ids = self.notion.child_page_ids(pid)
            except httpx.HTTPError as exc:
                log.warning("could not list child pages of %s; skipping subtree: %s", pid, exc)
                continue
            for child_id in child_ids:
                try:
                    child = self.notion.get_item(child_id)
                    child.kind = "page"
                    self._mirror_body_item(child, parent_folder, id_to_title)
                except httpx.HTTPError as exc:
                    log.warning("could not mirror child page %s; skipping: %s", child_id, exc)
                    continue
                count += 1
                stack.append(child_id)
        return count

    # --- helpers -------------------------------------------------------------

    def _build_record(self, item: NotionItem, id_to_title, doc_id) -> dict:
        from app.connectors.google.sheets import hyperlink

        record = dict(item.properties)
        for col, ids in item.relations.items():
            record[col] = resolve.names_for(ids, id_to_title)
        record["Doc"] = hyperlink(self.google.doc_url(doc_id)) if doc_id else ""
        record["_notion_id"] = item.notion_id
        record["_last_edited"] = item.last_edited_time or ""
        # Projection ignores Doc/_* columns, so hashing the record is correct.
        record["_hash"] = property_hash(item.kind, record)
        return record

    def _area_folder_for(self, project: NotionItem, areas_root: str, id_to_title) -> str:
        area_ids = project.relations.get("Area", [])
        for aid in area_ids:
            folder = self._folder_of.get(_norm(aid))
            if folder:
                return folder
            pair = repo.get_pair_by_notion_id(self.session, aid)
            if pair and pair.drive_folder_id:
                return pair.drive_folder_id
        # No resolvable area: park under an "_Unsorted" area folder.
        return self.google.ensure_folder("_Unsorted", areas_root)

    def _section_folder_for(self, item: NotionItem) -> str:
        if item.kind == "briefing":
            return self.google.ensure_folder("Briefing", self.google.root_folder_id)
        if item.kind == "reference":
            return self.google.ensure_folder("References", self.google.root_folder_id)
        # Generic child page whose parent folder is recorded.
        pair = repo.get_pair_by_notion_id(self.session, item.parent_id or "")
        if pair and pair.drive_folder_id:
            return pair.drive_folder_id
        return self.google.root_folder_id


_TAB = {"area": "Areas", "project": "Projects", "action": "Actions"}


def _norm(notion_id: str | None) -> str:
    return (notion_id or "").replace("-", "")
