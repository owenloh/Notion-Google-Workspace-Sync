"""FastAPI application entry point.

Initializes the ledger, starts the scheduler, and exposes the Notion webhook plus
a health check and a manual full-sync trigger.
"""

from __future__ import annotations

import hmac
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query

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
