"""_Dashboard / _Commands doc generation (pure)."""

from app.engines.docs_gen import (
    CatalogEntry,
    build_commands_md,
    build_dashboard_md,
    build_intray_md,
)


def test_intray_md_lists_items_and_handles_empty():
    md = build_intray_md([{"title": "buy milk", "status": "notStarted"}, {"title": "call Bob"}])
    assert "Microsoft To-Do" in md
    assert "- buy milk" in md and "- call Bob" in md
    assert "in-tray is empty" in build_intray_md([])

ENTRIES = [
    CatalogEntry("area", "Career", "a1"),
    CatalogEntry("project", "PourDynamics engine", "p1"),
    CatalogEntry("action", "Email Bob", "t1"),
]


def test_dashboard_lists_each_kind_with_ids():
    md = build_dashboard_md(ENTRIES)
    assert "## Areas" in md and "## Projects" in md and "## Actions" in md
    assert "Career  `a1`" in md
    assert "PourDynamics engine  `p1`" in md


def test_commands_doc_has_paths_guardrails_and_points_to_dashboard():
    md = build_commands_md(
        ENTRIES,
        allowed_paths=["/api/notion/create-pages", "/api/notion/update-page"],
    )
    assert "/api/notion/create-pages" in md
    assert "replace_content" in md                 # warns against it
    assert "FORBIDDEN" in md                        # anti-refusal guardrail present
    assert "pages" in md and "Common mistakes" in md   # exact-envelope + anti-improvise
    assert "add_action" in md                       # shows the known wrong shapes
    assert "Properties per database" in md          # per-DB title/property guidance
    assert "title field differs" in md              # Projects use `Project`, not `Name`
    # The full id catalog is NOT embedded here (kept lean) — it points to _Dashboard.
    assert "_Dashboard" in md
    assert "PourDynamics engine → `p1`" not in md


def test_commands_doc_lean_without_skill_dump():
    # Even if skill text is supplied it stays optional; the doc is valid without it.
    md = build_commands_md(ENTRIES, allowed_paths=["/api/notion/create-pages"])
    assert "Target ids" in md and "_Dashboard" in md
