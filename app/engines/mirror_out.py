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

        counts = {"area": 0, "project": 0, "action": 0, "page": 0, "failed": 0,
                  "removed": 0, "pruned": 0}

        # Every notion id the crawl enumerates; pairs NOT in here at the end are
        # candidates for deletion (verified before removal).
        seen: set[str] = {_norm(it.notion_id) for it in spine + loose}
        # Top-level ids are mirrored independently (spine rows / loose-root sections);
        # if recursion meets one as a child, skip it so it isn't duplicated.
        top_level: set[str] = set(seen)

        by_kind = {"area": [], "project": [], "action": []}
        for it in spine:
            by_kind.setdefault(it.kind, []).append(it)

        # Each item is isolated: a transient error (SSL/network) or a single bad
        # page must not abort the whole reconcile. Skipped items are counted and
        # healed by the next poll/reconcile.
        for area in by_kind["area"]:
            try:
                self._mirror_body_item(area, areas_root, id_to_title)
                counts["area"] += 1
            except Exception:  # noqa: BLE001
                log.exception("mirror failed for area %s; skipping", area.title)
                counts["failed"] += 1
        for project in by_kind["project"]:
            try:
                parent = self._area_folder_for(project, areas_root, id_to_title)
                self._mirror_body_item(project, parent, id_to_title)
                counts["project"] += 1
            except Exception:  # noqa: BLE001
                log.exception("mirror failed for project %s; skipping", project.title)
                counts["failed"] += 1
        for action in by_kind["action"]:
            try:
                self._mirror_action(action, id_to_title)
                counts["action"] += 1
            except Exception:  # noqa: BLE001
                log.exception("mirror failed for action %s; skipping", action.title)
                counts["failed"] += 1

        for page in loose:
            section = _SECTION_FOLDER.get(page.kind, "Pages")
            parent = self.google.ensure_folder(section, self.google.root_folder_id)
            try:
                self._mirror_body_item(page, parent, id_to_title)
            except Exception:  # noqa: BLE001
                log.exception("mirror failed for loose page %s; skipping", page.title)
                counts["failed"] += 1

        counts["page"] = self._recurse_all_children(
            [it.notion_id for it in spine if it.kind in {"area", "project"}]
            + [it.notion_id for it in loose],
            id_to_title,
            seen,
            top_level,
        )
        try:
            self._write_reference_docs(spine)
        except Exception:  # noqa: BLE001
            log.exception("writing _Dashboard/_Commands docs failed; continuing")
        counts["removed"] = self._remove_unseen(seen)
        counts["pruned"] = self._prune_untracked()
        return counts

    def _prune_untracked(self) -> int:
        """Trash mirror objects no ledger pair points to (untracked orphans).

        Deletion-detection only covers *tracked* pairs; orphans left by older code
        (e.g. a page that was once mirrored under two parents) have no pair and
        would linger forever. We trash any Drive object at depth >= 2 (i.e. *inside*
        a section folder) that isn't referenced by a live pair. Depth 0/1 — the
        root, section folders (Areas/References/…) and meta Docs (_Commands/…) — are
        never touched, and any genuinely-tracked item is in the keep set, so a
        transient mirror failure can't cause a false prune.
        """
        keep: set[str] = set()
        for pair in repo.all_pairs(self.session):
            if pair.drive_folder_id:
                keep.add(pair.drive_folder_id)
            if pair.gdoc_id:
                keep.add(pair.gdoc_id)
        try:
            tree = self.google.drive_tree(self.google.root_folder_id)
        except Exception:  # noqa: BLE001
            log.exception("prune: could not read drive tree; skipping")
            return 0
        victims: list[str] = []
        # Depth-1 folders that legitimately exist even when momentarily empty.
        protected = {"Areas", "Pages"} | set(_SECTION_FOLDER.values())

        def visit(node: dict, depth: int) -> bool:
            """Mark orphans; return True if this node's whole subtree is orphan.

            A depth>=2 folder is trashed only when it isn't tracked AND every
            descendant is orphan too — so a container that *holds* tracked items
            (e.g. ``Areas/_Unsorted`` with area-less projects) is never removed.
            """
            nid = node.get("id")
            kids = node.get("children", []) or []
            is_folder = node.get("type") == "folder"
            if depth == 0:
                for child in kids:
                    visit(child, 1)
                return False
            if depth == 1:
                # Section/meta level: only sweep an obsolete, empty, unknown section.
                if is_folder and not kids and node.get("name") not in protected:
                    victims.append(nid)
                    return True
                for child in kids:
                    visit(child, 2)
                return False
            # depth >= 2
            if not is_folder:
                if nid not in keep:
                    victims.append(nid)
                    return True
                return False
            children_orphan = [visit(child, depth + 1) for child in kids]
            if nid not in keep and all(children_orphan):
                victims.append(nid)
                return True
            return False

        visit(tree, 0)
        victims = list(dict.fromkeys(victims))  # dedupe (orphan child + its orphan parent)
        for nid in victims:
            try:
                self.google.trash(nid)
            except Exception:  # noqa: BLE001
                log.exception("prune: trashing %s failed", nid)
        if victims:
            log.info("pruned %d untracked orphan(s)", len(victims))
        return len(victims)

    def _remove_unseen(self, seen: set[str]) -> int:
        """Tombstone + trash mirror objects whose Notion page is gone.

        Only runs after a FULL crawl (sync_all), where ``seen`` is every enumerated
        id. Each candidate is re-fetched first so a partial/failed crawl can't
        false-delete a page that actually still exists.
        """
        removed = 0
        for pair in repo.all_pairs(self.session):
            if _norm(pair.notion_id) in seen:
                continue
            try:
                item = self.notion.get_item(pair.notion_id)
                if not getattr(item, "archived", False):
                    continue  # still exists, just not crawled this run — keep it
            except Exception:  # noqa: BLE001 — 404/gone → proceed to remove
                pass
            try:
                if pair.gdoc_id and self.google.is_live(pair.gdoc_id):
                    self.google.trash(pair.gdoc_id)
                if pair.drive_folder_id and self.google.is_live(pair.drive_folder_id):
                    self.google.trash(pair.drive_folder_id)
                if pair.gsheet_tab and pair.gsheet_row_key:
                    self.google.clear_row(pair.gsheet_tab, pair.gsheet_row_key)
            except Exception:  # noqa: BLE001
                log.exception("removing mirror for %s failed", pair.notion_id)
            repo.tombstone_pair(self.session, pair)
            removed += 1
            log.info("removed mirror for deleted/archived %s (%s)", pair.kind, pair.notion_id)
        return removed

    def _write_reference_docs(self, spine: list[NotionItem]) -> None:
        """(Re)generate the read-only `_Dashboard` and `_Commands` Docs.

        Change-gated by content hash so an unchanged catalog isn't rewritten.
        """
        from app.engines.docs_gen import (
            CatalogEntry,
            build_commands_md,
            build_dashboard_md,
        )

        # Only ACTIVE items go on the voice surface, so the Docs stay lean and don't
        # grow unbounded with completed actions; the full set lives in the index sheet.
        entries = [
            CatalogEntry(kind=it.kind, name=it.title, notion_id=it.notion_id)
            for it in spine
            if it.kind in {"area", "project", "action"} and _is_active(it)
        ]
        root = self.google.root_folder_id
        self._write_doc_if_changed("_Dashboard", root, build_dashboard_md(entries))
        self._write_doc_if_changed(
            "_Commands", root,
            build_commands_md(entries, self.settings.allowed_relay_paths),
        )
        # Microsoft To-Do in-tray mirror (read-only). Skip on fetch failure so a
        # transient error doesn't clobber the last good copy.
        self.refresh_intray()

    def refresh_intray(self) -> bool:
        """(Re)generate the `_Intray (Microsoft To-Do)` Doc from the live in-tray.

        Returns True if written, False if the in-tray couldn't be fetched (so a
        transient error never clobbers the last good copy). Called by the full
        reconcile and after a successful intray command.
        """
        from app.connectors.relay import fetch_intray
        from app.engines.docs_gen import build_intray_md

        items = fetch_intray(self.settings)
        if items is None:
            return False
        self._write_doc_if_changed(
            "_Intray (Microsoft To-Do)", self.google.root_folder_id, build_intray_md(items)
        )
        return True

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
        title = item.title or "Untitled"
        pair = repo.get_pair_by_notion_id(self.session, item.notion_id)
        folder_id = self._resolve_folder(pair, title, parent_folder_id)
        doc_id = self._resolve_doc(pair, title, folder_id)
        self._folder_of[_norm(item.notion_id)] = folder_id

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

    def _resolve_folder(self, pair: SyncPair | None, title: str, parent_id: str) -> str:
        """Reuse the ledger-tracked folder (rename/move in place) or create a fresh one.

        Addressing by stored id (not by name) means a renamed item keeps its folder,
        a moved item relocates instead of orphaning, and two same-named items get
        distinct folders instead of colliding.
        """
        if pair and pair.drive_folder_id and self.google.is_live(pair.drive_folder_id):
            fid = pair.drive_folder_id
            if (pair.title or "Untitled") != title:
                self.google.rename(fid, title)
            if pair.drive_parent_id and pair.drive_parent_id != parent_id:
                self.google.move(fid, parent_id)
            return fid
        return self.google.create_folder(title, parent_id)

    def _resolve_doc(self, pair: SyncPair | None, title: str, folder_id: str) -> str:
        if pair and pair.gdoc_id and self.google.is_live(pair.gdoc_id):
            if (pair.title or "Untitled") != title:
                self.google.rename(pair.gdoc_id, title)
            return pair.gdoc_id
        return self.google.create_doc(title, folder_id)

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

    def _recurse_all_children(
        self, parent_ids: list[str], id_to_title, seen: set[str] | None = None,
        skip_ids: set[str] | None = None,
    ) -> int:
        count = 0
        stack = list(parent_ids)
        while stack:
            pid = stack.pop()
            parent_folder = self._folder_of.get(_norm(pid))
            if not parent_folder:
                continue
            try:
                child_ids = self.notion.child_page_ids(pid)
            except Exception as exc:  # noqa: BLE001 — transient/SSL or bad page
                log.warning("could not list child pages of %s; skipping subtree: %s", pid, exc)
                continue
            for child_id in child_ids:
                if seen is not None:
                    seen.add(_norm(child_id))  # enumerated → not a deletion candidate
                # A child that is itself a top-level root is mirrored independently
                # (its own section/row) — don't duplicate it under this parent.
                if skip_ids and _norm(child_id) in skip_ids:
                    continue
                try:
                    child = self.notion.get_item(child_id)
                    child.kind = "page"
                    self._mirror_body_item(child, parent_folder, id_to_title)
                except Exception as exc:  # noqa: BLE001 — transient/SSL or bad page
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
        if item.kind == "area":
            # Areas live under the "Areas" folder, same as in sync_all. Without
            # this, the delta-poll's mirror_item re-created area folders at the
            # mirror root, duplicating them.
            return self.google.ensure_folder("Areas", self.google.root_folder_id)
        section = _SECTION_FOLDER.get(item.kind)
        if section:
            return self.google.ensure_folder(section, self.google.root_folder_id)
        # Generic child page whose parent folder is recorded.
        pair = repo.get_pair_by_notion_id(self.session, item.parent_id or "")
        if pair and pair.drive_folder_id:
            return pair.drive_folder_id
        return self.google.root_folder_id


_TAB = {"area": "Areas", "project": "Projects", "action": "Actions"}

# Loose-page kinds → their top-level section folder under the mirror root.
_SECTION_FOLDER = {
    "briefing": "Briefing",
    "horizons": "Horizons",
    "library": "Library",
    # "reference" removed: "Unorganised References" now mirrors under Library
    # (not as a top-level section), so no item maps to a "References" section.
}


def _norm(notion_id: str | None) -> str:
    return (notion_id or "").replace("-", "")


def _is_active(item: NotionItem) -> bool:
    """Whether an item belongs on the voice surface (_Dashboard/_Commands).

    Completed/archived items are excluded to keep the Docs lean (they still live in
    the _Notion Index sheet): Actions that are Done, Projects Complete/Dropped, Areas
    Retired.
    """
    props = item.properties or {}
    if item.kind == "action":
        return (props.get("Action Status") or "") != "Done"
    if item.kind == "project":
        return (props.get("Status") or "") not in {"Complete", "Dropped"}
    if item.kind == "area":
        return (props.get("Status") or "") != "Retired"
    return True
