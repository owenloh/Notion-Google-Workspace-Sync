"""Google -> Notion: new rows, echo de-dup, relation resolution, sub-pages."""

import pytest

from app.connectors.google import drive as gdrive
from app.engines.mirror_in import MirrorIn
from app.engines.mirror_out import MirrorOut
from app.ledger import repo
from tests.fakes import FakeGoogleMirror, FakeNotionSource, make_item


@pytest.fixture
def mirrored(session, settings):
    """A world already mirrored out, so the ledger + sheet are populated."""
    area = make_item(
        "a1", "area", "Career",
        properties={"Name": "Career", "Status": "Active"},
        relations={"Projects": ["p1"]},
    )
    project = make_item(
        "p1", "project", "PourDynamics engine",
        properties={"Project": "PourDynamics engine", "Status": "Active"},
        relations={"Area": ["a1"]},
    )
    items = {"a1": area, "p1": project}
    bodies = {"a1": "# Career", "p1": "## Plan"}
    notion = FakeNotionSource(items, bodies, {}, spine_ids=["a1", "p1"], loose_ids=[])
    google = FakeGoogleMirror()
    MirrorOut(session, notion, google, settings).sync_all()
    # Fresh notion source for the inbound direction (clean write logs).
    notion_in = FakeNotionSource(items, bodies, {}, spine_ids=["a1", "p1"], loose_ids=[])
    return notion_in, google


def test_new_action_row_creates_resolved_action(session, settings, mirrored):
    notion, google = mirrored
    google.seed_row("Actions", {
        "Name": "Call Sam",
        "Action Status": "Next",
        "Project": "PourDynamics engine",
        "_notion_id": "",
    })
    n = MirrorIn(session, notion, google, settings).sync_sheets()
    assert n == 1
    assert len(notion.created) == 1
    created = notion.created[0]
    assert created["parent"]["database_id"]
    # Relation resolved to the project's id.
    assert created["properties"]["Project"]["relation"] == [{"id": "p1"}]
    # notion id written back into the sheet row.
    assert google.read_tab("Actions")[0]["_notion_id"].startswith("new-")


def test_no_duplicate_on_second_pass(session, settings, mirrored):
    notion, google = mirrored
    google.seed_row("Actions", {
        "Name": "Call Sam", "Action Status": "Next",
        "Project": "PourDynamics engine", "_notion_id": "",
    })
    mi = MirrorIn(session, notion, google, settings)
    mi.sync_sheets()
    created_after_first = len(notion.created)
    # Second pass: row now has _notion_id and unchanged hash -> no work.
    mi.sync_sheets()
    assert len(notion.created) == created_after_first
    assert notion.updated == []


def test_edited_row_updates_properties(session, settings, mirrored):
    notion, google = mirrored
    # Flip the project's status in the sheet (as a user would in the cell).
    from app.connectors.google.sheets import record_to_row
    rec = google.read_tab("Projects")[0]
    rec["Status"] = "Complete"
    google.tabs["Projects"][0] = record_to_row("Projects", rec)
    n = MirrorIn(session, notion, google, settings).sync_sheets()
    assert n == 1
    assert notion.updated[0]["id"] == "p1"
    assert notion.updated[0]["properties"]["Status"]["status"]["name"] == "Complete"


def test_new_doc_under_mirrored_folder_creates_subpage(session, settings, mirrored):
    notion, google = mirrored
    # The project p1 has a mirrored folder; drop a new Doc inside it.
    pair = repo.get_pair_by_notion_id(session, "p1")
    google.docs["DOCX"] = "some spec content"
    change = {
        "fileId": "DOCX",
        "file": {
            "id": "DOCX", "name": "Spec", "mimeType": gdrive.DOC_MIME,
            "parents": [pair.drive_folder_id], "trashed": False,
        },
    }
    n = MirrorIn(session, notion, google, settings).sync_drive([change])
    assert n == 1
    assert notion.created[0]["parent"] == {"page_id": "p1"}
    assert notion.created[0]["properties"]["title"]["title"][0]["text"]["content"] == "Spec"


def test_root_level_doc_is_ignored(session, settings, mirrored):
    notion, google = mirrored
    google.docs["DOCR"] = "stray"
    change = {
        "fileId": "DOCR",
        "file": {
            "id": "DOCR", "name": "Stray", "mimeType": gdrive.DOC_MIME,
            "parents": [google.root_folder_id], "trashed": False,
        },
    }
    n = MirrorIn(session, notion, google, settings).sync_drive([change])
    assert n == 0
    assert notion.created == []


def test_trashed_doc_archives_notion_page(session, settings, mirrored):
    notion, google = mirrored
    pair = repo.get_pair_by_notion_id(session, "p1")
    change = {"fileId": pair.gdoc_id, "removed": False,
              "file": {"id": pair.gdoc_id, "trashed": True}}
    n = MirrorIn(session, notion, google, settings).sync_drive([change])
    assert n == 1
    assert "p1" in notion.archived
    assert repo.get_pair_by_notion_id(session, "p1").tombstone is True
