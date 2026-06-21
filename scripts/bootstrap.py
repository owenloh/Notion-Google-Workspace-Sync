"""One-time setup helper.

Subcommands:

* ``auth``   — run the Google OAuth consent flow and print a refresh token.
* ``init``   — create the "Notion Mirror" Drive folder and "_Notion Index" sheet,
               printing the ids to put in ``.env``.
* ``mirror`` — run the first full Notion → Google mirror.

Run locally (``auth`` needs a browser):

    python -m scripts.bootstrap auth
    python -m scripts.bootstrap init
    python -m scripts.bootstrap mirror
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings


def cmd_auth() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    from app.connectors.google.auth import SCOPES

    settings = get_settings()
    if not settings.google_credentials_json:
        sys.exit("Set GOOGLE_CREDENTIALS_JSON first (OAuth client JSON).")
    cfg = json.loads(settings.google_credentials_json)
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(port=0)
    print("\n=== Add this to your .env ===")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")


def cmd_init() -> None:
    from app.connectors.google.auth import build_services
    from app.connectors.google.drive import FOLDER_MIME, SHEET_MIME, find_child

    settings = get_settings()
    services = build_services(settings)
    drive = services.drive

    root_id = settings.google_drive_mirror_folder_id
    if not root_id:
        meta = {"name": "Notion Mirror", "mimeType": FOLDER_MIME}
        root_id = drive.files().create(body=meta, fields="id").execute()["id"]
        print(f"GOOGLE_DRIVE_MIRROR_FOLDER_ID={root_id}")

    sheet_id = settings.google_index_sheet_id
    if not sheet_id:
        existing = find_child(drive, root_id, "_Notion Index", SHEET_MIME)
        if existing:
            sheet_id = existing
        else:
            meta = {
                "name": "_Notion Index",
                "mimeType": SHEET_MIME,
                "parents": [root_id],
            }
            sheet_id = drive.files().create(body=meta, fields="id").execute()["id"]
        print(f"GOOGLE_INDEX_SHEET_ID={sheet_id}")

    from app.connectors.google import sheets as gsheets

    gsheets.ensure_structure(services.sheets, sheet_id)
    print("Index sheet structure ensured (Areas / Projects / Actions tabs).")


def cmd_mirror() -> None:
    from app.engines.mirror_out import MirrorOut
    from app.ledger.db import init_engine, session_scope
    from app.runtime import build_runtime

    settings = get_settings()
    init_engine(settings.ledger_db_path)
    rt = build_runtime(settings)
    with session_scope() as session:
        counts = MirrorOut(session, rt.notion, rt.google, settings).sync_all()
    print(f"Full mirror complete: {counts}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Notion ⇄ Google Workspace bootstrap")
    parser.add_argument("command", choices=["auth", "init", "mirror"])
    args = parser.parse_args()
    {"auth": cmd_auth, "init": cmd_init, "mirror": cmd_mirror}[args.command]()


if __name__ == "__main__":
    main()
