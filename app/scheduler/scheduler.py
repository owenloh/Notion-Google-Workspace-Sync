"""APScheduler wiring for the poll jobs."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.logging import get_logger
from app.scheduler import jobs

log = get_logger(__name__)


def _guard(fn):
    """Wrap a job so an exception never kills the scheduler thread."""

    def wrapped():
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("Scheduled job %s failed", fn.__name__)

    wrapped.__name__ = fn.__name__
    return wrapped


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _guard(jobs.poll_notion),
        IntervalTrigger(seconds=settings.notion_poll_seconds),
        id="poll_notion",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _guard(jobs.poll_google),
        IntervalTrigger(seconds=settings.google_poll_seconds),
        id="poll_google",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _guard(jobs.nightly_reconcile),
        CronTrigger(hour=4, minute=0),
        id="nightly_reconcile",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
