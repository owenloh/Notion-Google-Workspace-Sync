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

    assert counts == {"area": 1, "project": 1, "action": 1, "page": 1, "failed": 0,
                      "removed": 0, "pruned": 0}
    assert google.structure_ready

    # Folder tree: Areas/Career/PourDynamics engine/Spec nesting.
    names = {meta[0] for meta in google.folder_meta.values()}
    assert {"Areas", "Career", "PourDynamics engine", "Spec"} <= names

    # Per-item folders are tracked by ledger id; verify nesting via folder_meta
    # (id -> (name, parent)). The "Areas" section folder is still find-or-create.
    def fid(name):
        return next(i for i, (n, _) in google.folder_meta.items() if n == name)
    areas_id = google.folders[("ROOT", "Areas")]
    assert google.folder_meta[fid("Career")][1] == areas_id
    assert google.folder_meta[fid("PourDynamics engine")][1] == fid("Career")
    assert google.folder_meta[fid("Spec")][1] == fid("PourDynamics engine")

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
    """Loose pages get a body Doc in their section folder, no sheet row (no KeyError)."""
    brief = make_item("b1", "briefing", "Alistair's Brief", properties={}, relations={})
    horizons = make_item("h1", "horizons", "Horizons (drafting)", properties={}, relations={})
    library = make_item("l1", "library", "Library", properties={}, relations={})
    items = {"b1": brief, "h1": horizons, "l1": library}
    bodies = {"b1": "today's brief", "h1": "my vision", "l1": "reading list"}
    notion = FakeNotionSource(items, bodies, {}, spine_ids=[], loose_ids=["b1", "h1", "l1"])
    google = FakeGoogleMirror()

    counts = MirrorOut(session, notion, google, settings).sync_all()

    assert counts["failed"] == 0
    for text in ("today's brief", "my vision", "reading list"):
        assert any(text in v for v in google.docs.values())
    # Each loose page lands in its own section folder.
    names = {meta[0] for meta in google.folder_meta.values()}
    assert {"Briefing", "Horizons", "Library"} <= names
    # No spine sheet rows were written for loose pages.
    assert google.read_tab("Areas") == []
    assert google.read_tab("Actions") == []


def test_rename_in_place_no_orphan(session, settings, world):
    """Renaming a Notion item renames its folder/Doc in place (no new orphan)."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()
    pair = repo.get_pair_by_notion_id(session, "p1")
    folder, doc = pair.drive_folder_id, pair.gdoc_id

    notion.items["p1"].title = "PourDynamics v2"
    MirrorOut(session, notion, google, settings).sync_all()

    pair2 = repo.get_pair_by_notion_id(session, "p1")
    assert pair2.drive_folder_id == folder      # same folder reused
    assert pair2.gdoc_id == doc                  # same Doc reused
    assert google.folder_meta[folder][0] == "PourDynamics v2"  # renamed in place
    assert google.doc_meta[doc][0] == "PourDynamics v2"


def test_duplicate_names_get_distinct_objects(session, settings):
    """Two items with the same title get distinct folders/Docs (no collision)."""
    a = make_item("a1", "area", "Career",
                  properties={"Name": "Career", "Status": "Active", "Type": "Life"},
                  relations={"Projects": ["p1", "p2"]})
    p1 = make_item("p1", "project", "Dup",
                   properties={"Project": "Dup", "Status": "Active"}, relations={"Area": ["a1"]})
    p2 = make_item("p2", "project", "Dup",
                   properties={"Project": "Dup", "Status": "Active"}, relations={"Area": ["a1"]})
    notion = FakeNotionSource({"a1": a, "p1": p1, "p2": p2},
                              {"a1": "", "p1": "b1", "p2": "b2"}, {},
                              spine_ids=["a1", "p1", "p2"], loose_ids=[])
    google = FakeGoogleMirror()
    MirrorOut(session, notion, google, settings).sync_all()

    f1 = repo.get_pair_by_notion_id(session, "p1").drive_folder_id
    f2 = repo.get_pair_by_notion_id(session, "p2").drive_folder_id
    d1 = repo.get_pair_by_notion_id(session, "p1").gdoc_id
    d2 = repo.get_pair_by_notion_id(session, "p2").gdoc_id
    assert f1 != f2 and d1 != d2                  # distinct despite identical names


def test_move_relocates_folder(session, settings):
    """Re-parenting an item moves its folder, reusing the same id (no orphan)."""
    a1 = make_item("a1", "area", "A1",
                   properties={"Name": "A1", "Status": "Active", "Type": "Life", "Standards": ""})
    a2 = make_item("a2", "area", "A2",
                   properties={"Name": "A2", "Status": "Active", "Type": "Life", "Standards": ""})
    p1 = make_item("p1", "project", "P",
                   properties={"Project": "P", "Status": "Active"}, relations={"Area": ["a1"]})
    notion = FakeNotionSource({"a1": a1, "a2": a2, "p1": p1},
                              {"a1": "", "a2": "", "p1": "body"}, {},
                              spine_ids=["a1", "a2", "p1"], loose_ids=[])
    google = FakeGoogleMirror()
    MirrorOut(session, notion, google, settings).sync_all()
    pf = repo.get_pair_by_notion_id(session, "p1").drive_folder_id
    a2f = repo.get_pair_by_notion_id(session, "a2").drive_folder_id

    notion.items["p1"].relations = {"Area": ["a2"]}   # move to A2
    MirrorOut(session, notion, google, settings).sync_all()

    pair = repo.get_pair_by_notion_id(session, "p1")
    assert pair.drive_folder_id == pf                 # same folder
    assert google.folder_meta[pf][1] == a2f           # now under A2


def test_deletion_detection_tombstones_and_trashes(session, settings, world):
    """A page removed from Notion gets its Doc/folder trashed + pair tombstoned."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()
    pair = repo.get_pair_by_notion_id(session, "p1")
    folder, doc = pair.drive_folder_id, pair.gdoc_id
    assert google.is_live(folder) and google.is_live(doc)

    # Delete the project + its child page in Notion.
    notion.spine_ids = ["a1", "t1"]
    notion.children = {}
    del notion.items["p1"]
    del notion.items["c1"]

    counts = MirrorOut(session, notion, google, settings).sync_all()
    assert counts["removed"] >= 1
    assert not google.is_live(folder)                 # Doc/folder trashed
    assert not google.is_live(doc)
    assert repo.get_pair_by_notion_id(session, "p1").tombstone is True


def test_deletion_skips_existing_page_on_partial_crawl(session, settings, world):
    """A page merely missing from this crawl (still exists) is NOT deleted."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()
    pair = repo.get_pair_by_notion_id(session, "p1")
    folder = pair.drive_folder_id

    # p1 not enumerated this run, but get_item still returns it (it exists).
    notion.spine_ids = ["a1", "t1"]
    notion.children = {}
    counts = MirrorOut(session, notion, google, settings).sync_all()
    assert counts["removed"] == 0
    assert google.is_live(folder)                     # preserved
    assert repo.get_pair_by_notion_id(session, "p1").tombstone is False


def test_root_page_not_duplicated_under_a_parent_root(session, settings):
    """A page that is both a loose root and a child of another root is mirrored
    once (as its own section), not duplicated under the parent."""
    parent = make_item("P", "library", "Library")
    refs = make_item("L", "reference", "Refs")  # also a child of Library
    notion = FakeNotionSource({"P": parent, "L": refs}, {"P": "", "L": ""},
                              {"P": ["L"]}, spine_ids=[], loose_ids=["P", "L"])
    google = FakeGoogleMirror()
    MirrorOut(session, notion, google, settings).sync_all()

    refs_folders = [fid for fid, (nm, _) in google.folder_meta.items() if nm == "Refs"]
    assert len(refs_folders) == 1                       # mirrored once, not twice
    lpair = repo.get_pair_by_notion_id(session, "L")
    ppair = repo.get_pair_by_notion_id(session, "P")
    assert google.folder_meta[lpair.drive_folder_id][1] != ppair.drive_folder_id  # not under P


def test_prune_removes_untracked_orphans(session, settings, world):
    """An orphan Drive object no ledger pair references gets pruned; tracked
    items and section folders survive."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()
    proj = repo.get_pair_by_notion_id(session, "p1")
    orphan = google.create_folder("ORPHAN", proj.drive_folder_id)  # deep + untracked
    google.create_doc("note", orphan)
    assert google.is_live(orphan)

    pruned = MirrorOut(session, notion, google, settings)._prune_untracked()
    assert pruned >= 1
    assert not google.is_live(orphan)                  # orphan trashed
    assert google.is_live(proj.drive_folder_id)        # tracked project kept


def test_reconcile_spine_regenerates_dashboard(session, settings, world):
    """The incremental path refreshes the catalog without a full reconcile."""
    notion, google = world
    MirrorOut(session, notion, google, settings).reconcile_spine()
    dash = next(google.docs[d] for d, (n, _) in google.doc_meta.items() if n == "_Dashboard")
    assert "Career" in dash and "`a1`" in dash      # spine listed with ids


def test_reconcile_spine_removes_deleted_spine_item(session, settings, world):
    """A spine item gone from Notion is dropped by the incremental spine pass
    (no waiting for the daily reconcile)."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()
    proj = repo.get_pair_by_notion_id(session, "p1")
    folder = proj.drive_folder_id
    assert google.is_live(folder)

    # Project deleted in Notion: gone from the spine query AND get_item.
    notion.spine_ids = ["a1", "t1"]
    del notion.items["p1"]

    removed = MirrorOut(session, notion, google, settings).reconcile_spine()
    assert removed >= 1
    assert not google.is_live(folder)                       # mirror dropped ~a poll later
    assert repo.get_pair_by_notion_id(session, "p1").tombstone is True


def test_dashboard_keeps_areas_projects_drops_done_actions(session, settings):
    """_Dashboard lists all Areas/Projects but omits Done / checkboxed Actions."""
    area = make_item("a1", "area", "Retired Area",
                     properties={"Name": "Retired Area", "Status": "Retired"})
    proj = make_item("p1", "project", "Done Project",
                     properties={"Project": "Done Project", "Status": "Complete"},
                     relations={"Area": ["a1"]})
    open_act = make_item("t1", "action", "Open task",
                         properties={"Name": "Open task", "Action Status": "Next",
                                     "Checkbox": False}, relations={})
    done_act = make_item("t2", "action", "Done task",
                         properties={"Name": "Done task", "Action Status": "Done"}, relations={})
    ticked_act = make_item("t3", "action", "Ticked task",
                           properties={"Name": "Ticked task", "Action Status": "Next",
                                       "Checkbox": True}, relations={})
    items = {"a1": area, "p1": proj, "t1": open_act, "t2": done_act, "t3": ticked_act}
    notion = FakeNotionSource(items, {k: "" for k in items}, {},
                              spine_ids=["a1", "p1", "t1", "t2", "t3"], loose_ids=[])
    google = FakeGoogleMirror()
    MirrorOut(session, notion, google, settings).sync_all()

    dash = next(google.docs[d] for d, (n, _) in google.doc_meta.items() if n == "_Dashboard")
    assert "Retired Area" in dash and "Done Project" in dash   # areas/projects always kept
    assert "Open task" in dash                                  # open action kept
    assert "Done task" not in dash and "Ticked task" not in dash  # completed dropped


def test_prune_keeps_unsorted_container_holding_tracked_projects(session, settings):
    """A non-ledger container folder (Areas/_Unsorted) that holds tracked items
    must NOT be pruned, even though it has no pair of its own."""
    proj = make_item("p1", "project", "Homeless Project",
                     properties={"Project": "Homeless Project", "Status": "Active"},
                     relations={})  # no Area → parked under _Unsorted
    notion = FakeNotionSource({"p1": proj}, {"p1": "body"}, {},
                              spine_ids=["p1"], loose_ids=[])
    google = FakeGoogleMirror()
    MirrorOut(session, notion, google, settings).sync_all()
    unsorted = next(i for i, (n, _) in google.folder_meta.items() if n == "_Unsorted")
    proj_folder = repo.get_pair_by_notion_id(session, "p1").drive_folder_id

    MirrorOut(session, notion, google, settings)._prune_untracked()
    assert google.is_live(unsorted)        # container preserved (holds a tracked project)
    assert google.is_live(proj_folder)     # the project itself preserved


def test_prune_sweeps_obsolete_empty_section_folder(session, settings, world):
    """An empty, de-configured top-level section (e.g. old References) is removed;
    current sections and meta are left alone."""
    notion, google = world
    MirrorOut(session, notion, google, settings).sync_all()
    root = google.root_folder_id
    obsolete = google.create_folder("References", root)   # empty, not a current section
    keep_empty = google.create_folder("Briefing", root)   # current section, empty here

    MirrorOut(session, notion, google, settings)._prune_untracked()
    assert not google.is_live(obsolete)                   # obsolete empty section swept
    assert google.is_live(keep_empty)                     # current section preserved


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
