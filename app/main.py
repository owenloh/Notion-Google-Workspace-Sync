"""FastAPI application entry point.

Initializes the ledger, starts the scheduler, and exposes the Notion webhook plus
a health check and a manual full-sync trigger.
"""

from __future__ import annotations

import hmac
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Header, HTTPException, Query

from app.api.webhooks import notion as notion_webhook
from app.config import get_settings
from app.ledger.db import init_engine
from app.logging import configure_logging, get_logger

log = get_logger(__name__)

# Background full-sync state. A full reconcile of a real workspace can exceed
# Railway's ~5-min HTTP proxy timeout, so /admin/full-sync runs it in a thread
# (same pattern the scheduler uses) and /admin/sync-status reports progress.
_sync_state: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "counts": None, "error": None,
}


def _run_full_reconcile() -> None:
    from app.scheduler.jobs import full_reconcile

    _sync_state.update(
        running=True, started_at=time.time(), finished_at=None, counts=None, error=None
    )
    try:
        _sync_state["counts"] = full_reconcile()
    except Exception as exc:  # noqa: BLE001 — record cause for /admin/sync-status
        log.exception("background full-sync failed")
        _sync_state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _sync_state["running"] = False
        _sync_state["finished_at"] = time.time()


def _sync_state_view() -> dict:
    view = dict(_sync_state)
    if view["started_at"]:
        end = view["finished_at"] or time.time()
        view["elapsed_seconds"] = round(end - view["started_at"], 1)
    return view


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_engine(settings.ledger_db_path)
    log.info("Ledger initialized at %s", settings.ledger_db_path)

    scheduler = None
    try:
        from app.scheduler.scheduler import build_scheduler

        scheduler = build_scheduler()
        scheduler.start()
        log.info("Scheduler started")
    except Exception:  # noqa: BLE001 — allow the app to serve even if jobs can't start
        log.exception("Scheduler failed to start; continuing without polling")

    app.state.scheduler = scheduler
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Notion ⇄ Google Workspace Sync", lifespan=lifespan)
app.include_router(notion_webhook.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _check_admin_key(provided: str | None) -> None:
    """Authorize an admin request; the key is checked before any work is done."""
    settings = get_settings()
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")
    if not provided or not hmac.compare_digest(provided, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="invalid admin key")


@app.post("/admin/full-sync")
async def full_sync(
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
    wait: bool = Query(default=False),
) -> dict[str, object]:
    """Trigger a full Notion → Google reconcile (key required).

    Runs in the background by default and returns immediately (a full reconcile
    can exceed Railway's HTTP timeout); poll ``GET /admin/sync-status``. Pass
    ``?wait=true`` to run synchronously and get the counts in the response.

        curl -X POST "https://<host>/admin/full-sync?key=$ADMIN_API_KEY"
    """
    _check_admin_key(x_admin_key or key)
    from app.scheduler.jobs import full_reconcile

    if wait:
        try:
            counts = full_reconcile()
        except Exception as exc:  # noqa: BLE001 — surface the cause to the caller
            log.exception("full-sync failed")
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        return {"status": "ok", "counts": counts}

    if _sync_state["running"]:
        return {"status": "already_running", **_sync_state_view()}
    threading.Thread(target=_run_full_reconcile, daemon=True).start()
    return {"status": "started", "poll": "/admin/sync-status"}


@app.get("/admin/sync-status")
async def sync_status(
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Report the most recent background full-sync's progress/result (key required)."""
    _check_admin_key(x_admin_key or key)
    return _sync_state_view()


@app.post("/admin/reset-ledger")
async def reset_ledger(
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Clear the ledger so the next full-sync rebuilds everything from scratch.

    Use after manually deleting the Drive mirror contents: otherwise the hash
    gate thinks Docs are unchanged and recreates them empty. (key required.)
    """
    _check_admin_key(x_admin_key or key)
    from app.ledger import repo
    from app.ledger.db import session_scope

    with session_scope() as session:
        counts = repo.reset_all(session)
    return {"status": "ok", "cleared": counts}


@app.get("/admin/drive-tree")
async def drive_tree(
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Return the live Drive mirror folder structure (diagnostic; key required)."""
    _check_admin_key(x_admin_key or key)
    from app.runtime import get_runtime

    rt = get_runtime()
    try:
        tree = rt.google.drive_tree()
    except Exception as exc:  # noqa: BLE001 — surface the cause to the caller + logs
        log.exception("drive-tree failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"status": "ok", "root_folder_id": rt.google.root_folder_id, "tree": tree}


@app.post("/admin/test-command")
async def test_command(
    payload: dict | None = Body(default=None),
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Insert a command task into the Notion Commands list (tests the inbox path).

    The JSON body is written into the task's notes exactly as Gemini would; the
    next ``poll_commands`` cycle (~30s) picks it up, relays it, and writes a
    receipt. Defaults to creating a labeled test Action if no body is given.
    """
    _check_admin_key(x_admin_key or key)
    import json as _json

    from app.runtime import get_runtime

    cmd = payload or {
        "path": "/api/notion/create-pages",
        "body": {
            "parent": {"data_source_id": "collection://2ebc58c5-8617-4748-8021-fcc2a37d3a97"},
            "pages": [{"properties": {
                "Name": "TEST inbox command — delete me", "Action Status": "Next",
            }}],
        },
    }
    rt = get_runtime()
    task = rt.google.create_command("TEST command (delete me)", _json.dumps(cmd))
    return {"status": "ok", "task_id": task.get("id"), "notes": task.get("notes")}


@app.get("/admin/list-commands")
async def list_commands(
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """List all tasks in the command inbox with their status + receipt notes."""
    _check_admin_key(x_admin_key or key)
    from app.runtime import get_runtime

    rt = get_runtime()
    items = rt.google.list_commands()
    return {"status": "ok", "tasks": [
        {"id": t.get("id"), "title": t.get("title"),
         "status": t.get("status"), "notes": t.get("notes")}
        for t in items
    ]}


@app.get("/admin/read-tab")
async def read_tab(
    tab: str = Query(...),
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Read rows of an index-sheet tab (Areas/Projects/Actions) to verify writes."""
    _check_admin_key(x_admin_key or key)
    from app.runtime import get_runtime

    rt = get_runtime()
    try:
        rows = rt.google.read_tab(tab)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"status": "ok", "tab": tab, "count": len(rows), "rows": rows}


@app.get("/admin/read-doc")
async def read_doc(
    id: str = Query(...),
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Read a mirror Doc's markdown by Google Doc id (verify reflected content)."""
    _check_admin_key(x_admin_key or key)
    from app.runtime import get_runtime

    rt = get_runtime()
    try:
        md = rt.google.read_doc(id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"status": "ok", "id": id, "length": len(md), "markdown": md}


@app.post("/command")
async def command(
    payload: dict,
    x_admin_key: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> dict[str, object]:
    """Execute one command synchronously (desktop/automation; key required).

    Body is a relay request, e.g.
    ``{"path":"/api/notion/update-page","body":{...}}``. Returns the relay result.
    Gemini Live can't call this — it uses the Google Tasks inbox — but it gives a
    synchronous path for non-voice clients.
    """
    _check_admin_key(x_admin_key or key)
    import json as _json

    from app.engines.command_schema import CommandError, parse_command
    from app.engines.commands import CommandExecutor
    from app.ledger.db import session_scope
    from app.runtime import get_runtime

    rt = get_runtime()
    parsed = parse_command(_json.dumps(payload), default_path=rt.settings.relay_default_path)
    if isinstance(parsed, CommandError):
        raise HTTPException(status_code=400, detail=parsed.message)
    with session_scope() as session:
        result = CommandExecutor(
            session, rt.notion, rt.google, rt.relay, rt.settings
        ).execute_one(parsed)
    return {
        "status": "ok" if result.ok else "error",
        "http_status": result.status,
        "summary": result.summary,
        "affected_id": result.affected_id,
    }
