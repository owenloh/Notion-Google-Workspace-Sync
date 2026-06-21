"""Standalone Google bootstrap for Notion-Google-Workspace-Sync.

Self-contained: no dependency on the app package, so you can run it from any
folder (just this file + the three pip deps) to mint the Google refresh token
and create the Drive mirror folder + index sheet.

Usage
-----
    pip install -r requirements.txt
    # (deps: google-api-python-client google-auth google-auth-oauthlib)

    # 1) Consent (opens a browser; needs a desktop with a browser):
    python bootstrap_standalone.py auth --credentials /path/to/oauth_client.json

    # 2) Create the Drive mirror folder + "_Notion Index" sheet:
    python bootstrap_standalone.py init \
        --credentials /path/to/oauth_client.json \
        --refresh-token "1//0g....the token from step 1...."

Each step prints values to put into your Railway environment variables:
    GOOGLE_OAUTH_REFRESH_TOKEN, GOOGLE_DRIVE_MIRROR_FOLDER_ID, GOOGLE_INDEX_SHEET_ID

Credentials can also be supplied via env vars instead of flags:
    GOOGLE_CREDENTIALS_JSON   (the full OAuth client JSON, as a string)
    GOOGLE_OAUTH_REFRESH_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The exact scopes the sync service requests. Must match the deployed app.
SCOPES = [
    "https://www.googleapis.com/auth/drive",  # create/update the mirror Docs tree
    "https://www.googleapis.com/auth/documents",  # write Doc bodies
    "https://www.googleapis.com/auth/spreadsheets",  # the optional _Notion Index sheet
    "https://www.googleapis.com/auth/tasks",  # the command inbox (write path)
]

FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

# _Notion Index sheet structure (mirrors app/connectors/google/sheets.py).
_BOOKKEEPING = ["Doc", "_notion_id", "_last_edited", "_hash"]
TAB_COLUMNS: dict[str, list[str]] = {
    "Areas": ["Name", "Status", "Type", "Standards", "Projects", *_BOOKKEEPING],
    "Projects": ["Project", "Area", "Direction", "Status", "Repo", "Next actions", *_BOOKKEEPING],
    "Actions": ["Name", "Action Status", "Due", "Project", "Checkbox", *_BOOKKEEPING],
}


def _load_client_config(path: str | None) -> dict:
    """Load the OAuth client JSON from --credentials, else GOOGLE_CREDENTIALS_JSON."""
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not raw:
        sys.exit("Provide --credentials <file> or set GOOGLE_CREDENTIALS_JSON.")
    return json.loads(raw)


def cmd_auth(args: argparse.Namespace) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    cfg = _load_client_config(args.credentials)
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(port=0)
    print("\n=== Put this in your Railway environment ===")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")


def _build_credentials(client_cfg: dict, refresh_token: str):
    from google.oauth2.credentials import Credentials

    # Accept both 'installed' (Desktop) and 'web' OAuth client shapes.
    client = client_cfg.get("installed") or client_cfg.get("web") or client_cfg
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=client.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=SCOPES,
    )


def _find_child(drive, parent_id: str, name: str, mime: str | None) -> str | None:
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"'{parent_id}' in parents and name = '{safe}' and trashed = false"
    if mime:
        q += f" and mimeType = '{mime}'"
    resp = drive.files().list(q=q, fields="files(id,name)", pageSize=1).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def _ensure_sheet_structure(sheets, spreadsheet_id: str) -> None:
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


def cmd_init(args: argparse.Namespace) -> None:
    from googleapiclient.discovery import build

    cfg = _load_client_config(args.credentials)
    refresh_token = args.refresh_token or os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if not refresh_token:
        sys.exit(
            "Provide --refresh-token <token> or set GOOGLE_OAUTH_REFRESH_TOKEN "
            "(run 'auth' first)."
        )

    creds = _build_credentials(cfg, refresh_token)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    print("\n=== Put these in your Railway environment ===")

    # Drive mirror folder (find-or-create at My Drive root).
    root_id = _find_child(drive, "root", "Notion Mirror", FOLDER_MIME)
    if not root_id:
        meta = {"name": "Notion Mirror", "mimeType": FOLDER_MIME}
        root_id = drive.files().create(body=meta, fields="id").execute()["id"]
    print(f"GOOGLE_DRIVE_MIRROR_FOLDER_ID={root_id}")

    # _Notion Index sheet (find-or-create inside the folder).
    sheet_id = _find_child(drive, root_id, "_Notion Index", SHEET_MIME)
    if not sheet_id:
        meta = {"name": "_Notion Index", "mimeType": SHEET_MIME, "parents": [root_id]}
        sheet_id = drive.files().create(body=meta, fields="id").execute()["id"]
    print(f"GOOGLE_INDEX_SHEET_ID={sheet_id}")

    _ensure_sheet_structure(sheets, sheet_id)
    print("# Index sheet structure ensured (Areas / Projects / Actions tabs).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Google bootstrap (auth/init).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="browser consent -> refresh token")
    p_auth.add_argument("--credentials", help="path to the OAuth client JSON (Desktop app)")
    p_auth.set_defaults(func=cmd_auth)

    p_init = sub.add_parser("init", help="create Drive mirror folder + index sheet")
    p_init.add_argument("--credentials", help="path to the OAuth client JSON (Desktop app)")
    p_init.add_argument("--refresh-token", help="refresh token from the 'auth' step")
    p_init.set_defaults(func=cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
