"""Ledger repo helpers."""

from app.ledger import repo


def test_reset_all_clears_pairs_and_state(session):
    repo.upsert_pair(session, "n1", kind="area", title="X", drive_folder_id="f1")
    repo.set_state(session, "watermark", "2026-06-21")
    assert repo.get_pair_by_notion_id(session, "n1") is not None

    cleared = repo.reset_all(session)
    assert cleared["sync_pairs"] >= 1
    assert cleared["sync_state"] >= 1

    assert repo.get_pair_by_notion_id(session, "n1") is None
    assert repo.get_state(session, "watermark") == ""
