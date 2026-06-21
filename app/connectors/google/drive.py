"""Drive operations: folder tree, Docs files, moves/renames, and the changes feed.

Everything is addressed by Drive file id (never by path) so renames and moves on
either side relocate cleanly.
"""

from __future__ import annotations

from app.logging import get_logger

log = get_logger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def find_child(drive, parent_id: str, name: str, mime: str | None = None) -> str | None:
    """Return the id of a non-trashed child named ``name`` under ``parent_id``."""
    q = f"'{parent_id}' in parents and name = '{_escape(name)}' and trashed = false"
    if mime:
        q += f" and mimeType = '{mime}'"
    resp = (
        drive.files()
        .list(q=q, fields="files(id,name)", pageSize=1, supportsAllDrives=True)
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def ensure_folder(drive, name: str, parent_id: str) -> str:
    """Find-or-create a folder named ``name`` under ``parent_id``."""
    existing = find_child(drive, parent_id, name, FOLDER_MIME)
    if existing:
        return existing
    meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    created = drive.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
    return created["id"]


def create_doc(drive, name: str, parent_id: str) -> str:
    meta = {"name": name, "mimeType": DOC_MIME, "parents": [parent_id]}
    created = drive.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
    return created["id"]


def rename_file(drive, file_id: str, name: str) -> None:
    drive.files().update(fileId=file_id, body={"name": name}, supportsAllDrives=True).execute()


def move_file(drive, file_id: str, new_parent_id: str) -> None:
    meta = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
    prev = ",".join(meta.get("parents", []))
    drive.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=prev,
        supportsAllDrives=True,
    ).execute()


def trash_file(drive, file_id: str) -> None:
    drive.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()


def doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


# --- Changes feed (Google -> Notion polling) -------------------------------

def get_start_page_token(drive) -> str:
    return drive.changes().getStartPageToken(supportsAllDrives=True).execute()["startPageToken"]


def list_changes(drive, page_token: str) -> tuple[list[dict], str]:
    """Return (changes, next_or_new_token). Follows the feed to the end."""
    changes: list[dict] = []
    token = page_token
    while True:
        resp = (
            drive.changes()
            .list(
                pageToken=token,
                spaces="drive",
                fields="newStartPageToken,nextPageToken,changes(fileId,removed,file(id,name,mimeType,parents,trashed,modifiedTime))",
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        changes.extend(resp.get("changes", []))
        if "nextPageToken" in resp:
            token = resp["nextPageToken"]
            continue
        return changes, resp.get("newStartPageToken", token)
