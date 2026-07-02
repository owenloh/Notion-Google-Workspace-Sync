"""Process-wide Google auth health signal.

Every Google API call flows through :func:`app.connectors.google._retry.execute`.
When the OAuth refresh token is expired or revoked, Google raises
``google.auth.exceptions.RefreshError`` there (``invalid_grant``). That is not
retryable — a new refresh token has to be minted with ``scripts/bootstrap.py
auth`` and pushed to ``GOOGLE_OAUTH_REFRESH_TOKEN`` — so instead of dumping the
same multi-frame traceback on every ~30s poll cycle, we record the failure here
so ``/health`` can report it and the scheduler can log one clear line.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_state: dict[str, object] = {
    "ok": True,        # False once a refresh-token failure is seen
    "detail": None,    # str: the underlying error message
    "since": None,     # epoch seconds of the first failure in the current outage
    "last_error_at": None,  # epoch seconds of the most recent failure
}

# Actionable one-liner reused by the raised error and the /health payload.
REMEDIATION = (
    "Google OAuth refresh token expired or revoked (invalid_grant). Re-run "
    "`python -m scripts.bootstrap auth`, set the new GOOGLE_OAUTH_REFRESH_TOKEN "
    "in the environment, and redeploy. To stop the 7-day expiry, publish the "
    "OAuth consent screen from Testing to In production."
)


class GoogleAuthError(RuntimeError):
    """The Google OAuth refresh token is expired or revoked (``invalid_grant``).

    Raised in place of the raw ``RefreshError`` so callers/logs get a single
    actionable message rather than a token-refresh traceback.
    """


def mark_auth_error(detail: str) -> None:
    """Record that a token refresh failed (idempotent; keeps the first ``since``)."""
    now = time.time()
    with _lock:
        if _state["ok"]:
            _state["since"] = now
        _state["ok"] = False
        _state["detail"] = detail
        _state["last_error_at"] = now


def mark_ok() -> None:
    """Record that a Google call succeeded, clearing any prior auth failure."""
    with _lock:
        if not _state["ok"]:
            _state.update(ok=True, detail=None, since=None, last_error_at=None)


def status() -> dict[str, object]:
    """A JSON-serializable snapshot of the current Google auth health."""
    with _lock:
        snap = dict(_state)
    if not snap["ok"]:
        snap["remediation"] = REMEDIATION
    return snap
