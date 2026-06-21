"""_Dashboard / _Commands doc generation (pure)."""

from app.engines.docs_gen import CatalogEntry, build_commands_md, build_dashboard_md

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


def test_commands_doc_has_paths_catalog_and_skill_rules():
    md = build_commands_md(
        ENTRIES,
        allowed_paths=["/api/notion/create-pages", "/api/notion/update-page"],
        skill_texts={"notion-master": "STATUS values: Next/Waiting/Done"},
    )
    assert "/api/notion/create-pages" in md
    assert "PourDynamics engine → `p1`" in md
    assert "STATUS values" in md
    assert "replace_content" in md  # warns against it


def test_commands_doc_without_skills_still_valid():
    md = build_commands_md(ENTRIES, allowed_paths=["/api/notion/create-pages"])
    assert "Catalog" in md and "Email Bob → `t1`" in md
