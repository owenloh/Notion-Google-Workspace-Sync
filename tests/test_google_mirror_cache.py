"""GoogleMirror sheet row-index cache (the Sheets-429 fix).

upsert_row must read each tab at most once per sync (priming the cache) and then
update/append by tracked row number — never a read per row.
"""

import types

from app.engines import google_mirror as gm


def _mirror(monkeypatch):
    reads = {"n": 0}
    writes = []

    def fake_read(_sheets, _sid, _tab):
        reads["n"] += 1
        return []  # empty sheet (header only) → next free row = 2

    def fake_update(_sheets, _sid, _tab, row, record):
        writes.append((row, record.get("_notion_id")))

    monkeypatch.setattr(gm.gsheets, "read_records", fake_read)
    monkeypatch.setattr(gm.gsheets, "update_record", fake_update)
    g = gm.GoogleMirror(types.SimpleNamespace(sheets=None), root_folder_id="r", index_sheet_id="s")
    g.reset_sheet_cache()
    return g, reads, writes


def test_upsert_row_primes_once_and_tracks_rows(monkeypatch):
    g, reads, writes = _mirror(monkeypatch)
    r1 = g.upsert_row("Actions", "n1", {"_notion_id": "n1"})
    r2 = g.upsert_row("Actions", "n2", {"_notion_id": "n2"})
    r1b = g.upsert_row("Actions", "n1", {"_notion_id": "n1"})  # update existing

    assert reads["n"] == 1            # ONE read for the whole tab, not per row
    assert (r1, r2) == (2, 3)         # appended at successive rows (header is row 1)
    assert r1b == 2                   # existing id reuses its row
    assert writes == [(2, "n1"), (3, "n2"), (2, "n1")]


def test_reset_sheet_cache_forces_reread(monkeypatch):
    g, reads, _ = _mirror(monkeypatch)
    g.upsert_row("Actions", "n1", {"_notion_id": "n1"})
    assert reads["n"] == 1
    g.reset_sheet_cache()             # next sync
    g.upsert_row("Actions", "n2", {"_notion_id": "n2"})
    assert reads["n"] == 2            # re-primed once after reset
