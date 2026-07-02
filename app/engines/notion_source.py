"""High-level Notion-side operations the engines depend on.

Wraps the Notion read/write connectors behind the verbs the engines use. Tests
substitute an in-memory fake exposing the same surface.
"""

from __future__ import annotations

from typing import Any

from app.config import ACTIONS_DS_ID, AREAS_DS_ID, PROJECTS_DS_ID, Settings, get_settings
from app.connectors.notion import read as nread
from app.connectors.notion import write as nwrite
from app.connectors.notion.client import NotionClient
from app.connectors.notion.read import NotionItem

# Loose pages mirrored as their own items, with the kind to assign. Each kind
# maps to a top-level section folder (see mirror_out._SECTION_FOLDER); their child
# pages are recursed under them.
LOOSE_PAGES = {
    "3806f0cc-dd76-80bb-9e16-fcce720de5ee": "briefing",   # Alistair's Brief
    "3806f0cc-dd76-803e-a35d-c9878567e4cc": "horizons",   # Horizons (drafting)
    "1fa6f0cc-dd76-809e-8bcb-e5db5ae28237": "library",    # Library hub
    # "Unorganised References" (37e6f0cc) is intentionally NOT a top-level root: it
    # lives under Library in Notion, so it mirrors there via Library's recursion.
}


class NotionSource:
    def __init__(self, client: NotionClient, settings: Settings | None = None):
        self.client = client
        self.settings = settings or get_settings()

    def spine_items(self) -> list[NotionItem]:
        """All Areas, Projects, and Actions as NotionItems."""
        items: list[NotionItem] = []
        for ds in (AREAS_DS_ID, PROJECTS_DS_ID, ACTIONS_DS_ID):
            items.extend(nread.query_data_source(self.client, ds))
        return items

    def loose_items(self) -> list[NotionItem]:
        out = []
        for page_id, kind in LOOSE_PAGES.items():
            item = nread.get_page(self.client, page_id)
            item.kind = kind
            out.append(item)
        return out

    def body_markdown(self, page_id: str) -> str:
        return nread.get_body_markdown(self.client, page_id)

    def child_page_ids(self, page_id: str) -> list[str]:
        return nread.get_child_page_ids(self.client, page_id)

    def body_and_child_ids(self, page_id: str) -> tuple[str, list[str]]:
        """Body Markdown + nested child-page ids from ONE block-tree fetch."""
        return nread.get_body_and_child_page_ids(self.client, page_id)

    def get_item(self, page_id: str) -> NotionItem:
        return nread.get_page(self.client, page_id)

    def changed_since(self, watermark: str) -> list[NotionItem]:
        """All pages edited since ``watermark`` (newest-first), incl. deep sub-pages.

        Spine pages are reclassified by their database so they mirror as rows;
        loose roots keep their configured kind. Everything else stays a "page".
        """
        items = nread.search_pages_changed_since(self.client, watermark)
        for it in items:
            loose = LOOSE_PAGES.get(it.notion_id) or LOOSE_PAGES.get(
                it.notion_id.replace("-", "")
            )
            if loose:
                it.kind = loose
        return items

    # --- writes (mirror_in) ---
    def create_page(
        self, parent: dict, properties: dict, children: list[dict] | None = None
    ) -> dict:
        return nwrite.create_page(self.client, parent, properties, children)

    def update_properties(self, page_id: str, properties: dict) -> dict:
        return nwrite.update_page_properties(self.client, page_id, properties)

    def replace_body(self, page_id: str, markdown: str) -> None:
        nwrite.replace_body(self.client, page_id, markdown)

    def archive(self, page_id: str) -> None:
        nwrite.archive_page(self.client, page_id)

    @staticmethod
    def build_properties(
        kind: str, values: dict[str, Any], relation_ids: dict[str, list[str]]
    ) -> dict:
        return nwrite.build_properties(kind, values, relation_ids)
