"""Relation resolution between Notion page ids and human-readable names.

* mirror-out (Notion → Google): relation values are page ids that must be shown
  as the related item's *name* in the Sheet.
* mirror-in (Google → Notion): a Sheet relation cell holds names that must be
  resolved back to Notion page ids.

The ledger is the resolution source (every mirrored item has a row with its
``notion_id`` and ``title``). A caller mid-full-sync may pass an extra in-memory
index so items not yet persisted still resolve.
"""

from __future__ import annotations

from sqlmodel import Session

from app.ledger import repo


def _norm(notion_id: str | None) -> str:
    return (notion_id or "").replace("-", "")


def build_indexes(session: Session, extra: dict[str, tuple[str, str]] | None = None):
    """Return (id_to_title, title_to_id).

    ``id_to_title`` maps normalized notion id → title. ``title_to_id`` maps
    ``(kind, title_lower)`` → notion id. ``extra`` optionally injects
    ``{notion_id: (kind, title)}`` entries (e.g. items from the current crawl).
    """
    id_to_title: dict[str, str] = {}
    title_to_id: dict[tuple[str, str], str] = {}

    def add(notion_id: str, kind: str, title: str) -> None:
        id_to_title[_norm(notion_id)] = title
        if title:
            title_to_id[(kind, title.strip().lower())] = notion_id

    for pair in repo.all_pairs(session):
        add(pair.notion_id, pair.kind, pair.title or "")
    for notion_id, (kind, title) in (extra or {}).items():
        add(notion_id, kind, title)

    return id_to_title, title_to_id


def names_for(relation_ids: list[str], id_to_title: dict[str, str]) -> list[str]:
    """Map a list of related page ids to their names (unknown ids dropped)."""
    out = []
    for rid in relation_ids:
        title = id_to_title.get(_norm(rid))
        if title:
            out.append(title)
    return out


def ids_for(
    names: list[str],
    related_kind: str,
    title_to_id: dict[tuple[str, str], str],
) -> tuple[list[str], list[str]]:
    """Resolve relation names to page ids. Returns (ids, unresolved_names)."""
    ids: list[str] = []
    unresolved: list[str] = []
    for name in names:
        rid = title_to_id.get((related_kind, name.strip().lower()))
        if rid:
            ids.append(rid)
        else:
            unresolved.append(name)
    return ids, unresolved


# Which related kind each relation column points at.
RELATION_TARGET_KIND = {
    ("area", "Projects"): "project",
    ("project", "Area"): "area",
    ("project", "Next actions"): "action",
    ("action", "Project"): "project",
}
