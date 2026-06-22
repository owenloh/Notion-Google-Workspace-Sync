"""Command executor: relay forwarding, receipts, re-reflect, idempotency."""

import pytest

from app.connectors.relay import RelayResult
from app.engines.commands import CommandExecutor
from app.ledger import repo
from tests.fakes import FakeGoogleMirror, FakeNotionSource, FakeRelay, make_item


@pytest.fixture
def world():
    project = make_item(
        "p1", "project", "PourDynamics engine",
        properties={"Project": "PourDynamics engine", "Status": "Active"},
    )
    notion = FakeNotionSource({"p1": project}, {"p1": "## Plan"}, {}, ["p1"], [])
    google = FakeGoogleMirror()
    return notion, google


def test_pending_command_is_forwarded_and_completed(world, session, settings):
    notion, google = world
    google.add_command(
        '{"path":"/api/notion/create-pages","body":{"Name":"Email Bob"}}', task_id="T1"
    )
    relay = FakeRelay(RelayResult(ok=True, status=200, summary="created", affected_id=None))

    n = CommandExecutor(session, notion, google, relay, settings).run_pending()
    assert n == 1
    # Forwarded with the right path/body.
    assert relay.calls[0].path == "/api/notion/create-pages"
    assert relay.calls[0].body == {"Name": "Email Bob"}
    # Task completed with a ✓ receipt.
    assert google.finished == [("T1", "✓ created")]


def test_intray_command_forwarded_and_refreshes_doc(world, session, settings, monkeypatch):
    notion, google = world
    google.add_command(
        '{"path":"/api/intray","body":{"action":"add","title":"Buy milk"}}', task_id="TI"
    )
    relay = FakeRelay(RelayResult(ok=True, status=200, summary="added", affected_id=None))
    monkeypatch.setattr(
        "app.connectors.relay.fetch_intray", lambda settings=None: [{"title": "Buy milk"}]
    )

    CommandExecutor(session, notion, google, relay, settings).run_pending()

    assert relay.calls[0].path == "/api/intray"
    assert google.finished == [("TI", "✓ added")]
    # The _Intray Doc was (re)generated from the live in-tray, not a Notion page.
    did = next(d for d, (n, _) in google.doc_meta.items() if n.startswith("_Intray"))
    assert "Buy milk" in google.read_doc(did)


def test_relay_error_yields_cross_receipt(world, session, settings):
    notion, google = world
    google.add_command('{"path":"/api/notion/update-page","body":{}}', task_id="T2")
    relay = FakeRelay(RelayResult(ok=False, status=400, summary="bad request"))

    CommandExecutor(session, notion, google, relay, settings).run_pending()
    assert google.finished[0][1].startswith("✗")


def test_non_command_task_is_ignored(world, session, settings):
    """A personal (non-JSON) task on the shared default list is left untouched."""
    notion, google = world
    google.add_command("buy groceries", task_id="T3")
    relay = FakeRelay()

    n = CommandExecutor(session, notion, google, relay, settings).run_pending()
    assert n == 0
    assert relay.calls == [] and google.finished == []  # not forwarded, not receipted


def test_malformed_json_command_gets_error_receipt(world, session, settings):
    """A task that looks like a command (JSON) but is invalid gets a ✗ receipt."""
    notion, google = world
    google.add_command('{"path": oops}', task_id="T3b")
    relay = FakeRelay()

    CommandExecutor(session, notion, google, relay, settings).run_pending()
    assert relay.calls == []  # never forwarded
    assert google.finished[0][1].startswith("✗")


def test_completed_task_not_rerun(world, session, settings):
    notion, google = world
    google.add_command('{"path":"/api/notion/create-pages","body":{"Name":"x"}}', task_id="T4")
    relay = FakeRelay()
    ex = CommandExecutor(session, notion, google, relay, settings)

    assert ex.run_pending() == 1
    # Second pass: the task is completed/receipted, so nothing to do.
    assert ex.run_pending() == 0
    assert len(relay.calls) == 1


def test_success_triggers_reflect(world, session, settings):
    notion, google = world
    google.add_command('{"path":"/api/notion/update-page","body":{}}', task_id="T5")
    relay = FakeRelay(RelayResult(ok=True, status=200, summary="updated", affected_id="p1"))

    CommandExecutor(session, notion, google, relay, settings).run_pending()
    # The affected project was re-reflected → a ledger pair now exists for it.
    assert repo.get_pair_by_notion_id(session, "p1") is not None
