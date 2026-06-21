"""Notion -> Google full mirror: folder tree, sheet rows, idempotency, echo."""

import pytest

from app.core.echo import Decision, SyncEvent, should_propagate
from app.core.hashing import property_hash
from app.engines.mirror_out import MirrorOut
from app.ledger import repo
from tests.fakes import FakeGoogleMirror, FakeNotionSource, make_item


@pytest.fixture
def world():
    area = make_item(
        "a1", "area", "Career",
        properties={"Name": "Career", "Status": "Active", "Type": "Career", "Standards": "high"},
        relations={"Projects": ["p1"]},
    )
    project = make_item(
        "p1", "project", "PourDynamics engine",
        properties={"Project": "PourDynamics engine", "Direction": "build", "Status": "Active",
                    "Repo": "https://github.com/x/y"},
        relations={"Area": ["a1"], "Next actions": ["t1"]},
    )
    action = make_item(
        "t1", "action", "Email Bob",
        properties={"Name": "Email Bob", "Action Status": "Next", "Checkbox": False},
        relations={"Project": ["p1"]},
    )
    child = make_item("c1", "page", "Spec", parent_id="p1")
    items = {"a1": area, "p1": project, "t1": action, "c1": child}
    bodies = {
        "a1": "# Career\n\nfocus here",
        "p1": "## Plan\n\n- step one",
        "c1": "spec details",
        "t1": "",
    }
    children = {"p1": ["c1"]}
    notion = FakeNotionSource(items, bodies, children, spine_ids=["a1", "p1", "t1"], loose_ids=[])
    google = FakeGoogleMirror()
    return notion, google


def test_full_mirror_builds_tree_and_rows(session, settings, world):
    notion, google = world
    counts = MirrorOut(session, notion, google, settings).sync_all()

    assert counts == {"area": 1, "project": 1, "action": 1, "page": 1}
    assert google.structure_ready

    # Folder tree: Areas/Career/PourDynamics engine/Spec nesting.
    names = {meta[0] for meta in google.folder_meta.values()}
    assert {"Areas", "Career", "PourDynamics engine", "Spec"} <= names

    # Project folder is nested under the Career area folder.
    areas_id = google.folders[("ROOT", "Areas")]
    career_folder = google.folders[(areas_id, "Career")]
    assert (career_folder, "PourDynamics engine") in google.folders

    # Sheet rows (read back as the Sheets API would: relations joined to text).
    areas = google.read_tab("Areas")
    assert len(areas) == 1 and areas[0]["Name"] == "Career"
    assert areas[0]["Projects"] == "PourDynamics engine"
    assert areas[0]["Doc"].startswith("=HYPERLINK(")

    projects = google.read_tab("Projects")
    assert projects[0]["Area"] == "Career"
    assert projects[0]["Next actions"] == "Email Bob"

    actions = google.read_tab("Actions")
    assert actions[0]["Name"] == "Email Bob"
    assert actions[0]["Project"] == "PourDynamics engine"

    # Docs written for bodies (area, project, child) but not the action.
    assert any("focus here" in v for v in google.docs.values())
    assert any("step one" in v for v in google.docs.values())

    # Reference docs generated at the root with the live catalog.
    assert ("_Dashboard", "ROOT") in {(n, p) for n, p in google.doc_meta.values()}
    assert ("_Commands", "ROOT") in {(n, p) for n, p in google.doc_meta.values()}
    assert any("PourDynamics engine" in v and "`p1`" in v for v in google.docs.values())


def test_loose_pages_mirror_body_only(session, settings):
    """Briefing/reference loose pages get a body Doc but no sheet row (no KeyError)."""
    brief = make_item("b1", "briefing", "Alistair's Brief", properties={}, relations={})
    refs = make_item("r1", "reference", "Unorganised References", properties={}, relations={})
    items = {"b1": brief, "r1": refs}
    bodies = {"b1": "today's brief", "r1": "a saved link"}
    notion = FakeNotionSource(items, bodies, {}, spine_ids=[], loose_ids=["b1", "r1"])
    google = FakeGoogleMirror()

    counts = MirrorOut(session, notion, google, settings).sync_all()

    assert counts["page"] == 0  # loose pages aren't counted as recursed children
    assert any("today's brief" in v for v in google.docs.values())
    assert any("a saved link" in v for v in google.docs.values())
    # No spine sheet rows were written for loose pages.
    assert google.read_tab("Areas") == []
    assert google.read_tab("Actions") == []


def test_mirror_is_idempotent(session, settings, world):
    notion, google = world
    mo = MirrorOut(session, notion, google, settings)
    mo.sync_all()
    writes_after_first = google.write_doc_calls
    appends_after_first = google.append_calls

    # Second run: nothing changed, so no new doc writes or row appends.
    mo2 = MirrorOut(session, notion, google, settings)
    mo2.sync_all()
    assert google.write_doc_calls == writes_after_first
    assert google.append_calls == appends_after_first


def test_echo_suppressed_on_google_readback(session, settings, world):
    """After mirroring, the Google poll re-reading the action row is an echo."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()

    pair = repo.get_pair_by_notion_id(session, "t1")
    row = google.read_tab("Actions")[0]
    observed_hash = property_hash("action", row)

    ev = SyncEvent(system="google", facet="property", incoming_hash=observed_hash)
    res = should_propagate(session, pair, ev, settings)
    assert res.decision in (Decision.DROP_ECHO, Decision.DROP_UNCHANGED)
