"""The ``_Notion Index`` spreadsheet: three tabs for the relational spine.

Row mapping helpers (:func:`record_to_row`, :func:`row_to_record`) are pure and
unit-tested. Each row carries a ``Doc`` ``=HYPERLINK`` cell to its body Google
Doc and hidden bookkeeping columns (``_notion_id``, ``_last_edited``, ``_hash``).
"""

from __future__ import annotations

from typing import Any

# Ordered headers per tab. Bookkeeping columns are prefixed with "_".
_BOOKKEEPING = ["Doc", "_notion_id", "_last_edited", "_hash"]
TAB_COLUMNS: dict[str, list[str]] = {
    "Areas": ["Name", "Status", "Type", "Standards", "Projects", *_BOOKKEEPING],
    "Projects": [
        "Project", "Area", "Direction", "Status", "Repo", "Next actions", *_BOOKKEEPING
    ],
    "Actions": ["Name", "Action Status", "Due", "Project", "Checkbox", *_BOOKKEEPING],
}

KIND_TO_TAB = {"area": "Areas", "project": "Projects", "action": "Actions"}
TAB_TO_KIND = {v: k for k, v in KIND_TO_TAB.items()}

# Columns holding relations (comma-joined names) per tab.
RELATION_COLUMNS = {
    "Areas": ["Projects"],
    "Projects": ["Area", "Next actions"],
    "Actions": ["Project"],
}


def hyperlink(url: str, label: str = "open") -> str:
    safe = label.replace('"', '""')
    return f'=HYPERLINK("{url}","{safe}")'


def _join(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


def record_to_row(tab: str, record: dict[str, Any]) -> list[str]:
    """Project a record dict onto the tab's ordered column values."""
    return [_join(record.get(col, "")) for col in TAB_COLUMNS[tab]]


def row_to_record(tab: str, row: list[str]) -> dict[str, str]:
    """Build a record dict from a raw row (missing trailing cells tolerated)."""
    cols = TAB_COLUMNS[tab]
    padded = list(row) + [""] * (len(cols) - len(row))
    return {col: padded[i] for i, col in enumerate(cols)}


def split_relation(value: str) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


# --- API-backed helpers ----------------------------------------------------

def ensure_structure(sheets, spreadsheet_id: str) -> None:
    """Create any missing tabs and write header rows."""
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = [
        {"addSheet": {"properties": {"title": tab}}}
        for tab in TAB_COLUMNS
        if tab not in existing
    ]
    if requests:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()
    for tab, cols in TAB_COLUMNS.items():
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            body={"values": [cols]},
        ).execute()


def read_records(sheets, spreadsheet_id: str, tab: str) -> list[dict[str, Any]]:
    """Return data rows as records, each annotated with its 1-based ``_row``."""
    resp = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A2:Z")
        .execute()
    )
    out = []
    for offset, row in enumerate(resp.get("values", []), start=2):
        record = row_to_record(tab, row)
        record["_row"] = offset
        out.append(record)
    return out


def append_record(sheets, spreadsheet_id: str, tab: str, record: dict[str, Any]) -> None:
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [record_to_row(tab, record)]},
    ).execute()


def update_record(
    sheets, spreadsheet_id: str, tab: str, row_number: int, record: dict[str, Any]
) -> None:
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A{row_number}",
        valueInputOption="USER_ENTERED",
        body={"values": [record_to_row(tab, record)]},
    ).execute()
