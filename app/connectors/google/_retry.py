"""Retry wrapper for Google API calls (handles 429 rate limits + transient 5xx).

A full sync issues many Sheets/Docs/Drive calls in bursts; the per-user quotas
(e.g. Sheets 60 reads + 60 writes per minute) are easily hit. ``execute`` retries
the request with exponential backoff on 429/5xx so a burst self-throttles instead
of failing.
"""

from __future__ import annotations

import time

from googleapiclient.errors import HttpError

from app.logging import get_logger

log = get_logger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}


def execute(request, attempts: int = 6):
    """Call ``request.execute()`` with exponential backoff on rate-limit/5xx."""
    delay = 1.0
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as exc:
            status = exc.resp.status if getattr(exc, "resp", None) else None
            if status in _RETRY_STATUSES and attempt < attempts - 1:
                log.warning(
                    "Google API %s (attempt %d/%d); retry in %.1fs",
                    status, attempt + 1, attempts, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 32.0)
                continue
            raise
