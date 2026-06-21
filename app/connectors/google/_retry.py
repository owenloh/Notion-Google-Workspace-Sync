"""Retry wrapper for Google API calls (handles 429 rate limits + transient 5xx).

A full sync issues many Sheets/Docs/Drive calls in bursts; the per-user quotas
(e.g. Sheets 60 reads + 60 writes per minute) are easily hit. ``execute`` retries
the request with exponential backoff on 429/5xx so a burst self-throttles instead
of failing.
"""

from __future__ import annotations

import threading
import time

import httplib2
from googleapiclient.errors import HttpError

from app.logging import get_logger

log = get_logger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}
# httplib2 (used by googleapiclient) is NOT thread-safe: concurrent use of one
# service from two threads corrupts the TLS connection ("record layer failure").
# Serialize every Google call process-wide so only one runs at a time.
_GOOGLE_LOCK = threading.Lock()
# Transient transport errors worth retrying. ssl.SSLError/ConnectionError/
# socket.timeout are all subclasses of OSError.
_TRANSPORT_ERRORS = (OSError, httplib2.HttpLib2Error)


def execute(request, attempts: int = 6):
    """Call ``request.execute()`` with a global lock + backoff on 429/5xx/transport errors."""
    delay = 1.0
    for attempt in range(attempts):
        try:
            with _GOOGLE_LOCK:
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
        except _TRANSPORT_ERRORS as exc:
            if attempt < attempts - 1:
                log.warning(
                    "Google API transport error: %s (attempt %d/%d); retry in %.1fs",
                    exc, attempt + 1, attempts, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 32.0)
                continue
            raise
