"""FastAPI application entry point.

Initializes the ledger, starts the scheduler, and exposes the Notion webhook plus
a health check and a manual full-sync trigger.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks import notion as notion_webhook
from app.config import get_settings
from app.ledger.db import init_engine
from app.logging import configure_logging, get_logger

log = get_logger(__name__)


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


@app.post("/admin/full-sync")
async def full_sync() -> dict[str, object]:
    """Trigger a full Notion → Google mirror on demand."""
    from app.engines.mirror_out import MirrorOut
    from app.ledger.db import session_scope
    from app.runtime import get_runtime

    rt = get_runtime()
    with session_scope() as session:
        counts = MirrorOut(session, rt.notion, rt.google, rt.settings).sync_all()
    return {"status": "ok", "counts": counts}
