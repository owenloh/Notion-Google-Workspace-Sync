"""Google API retry wrapper."""

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.connectors.google import _retry, health


class _Req:
    """Fake googleapiclient request: fails with `statuses` then returns `result`."""

    def __init__(self, statuses, result="ok"):
        self._statuses = list(statuses)
        self._result = result
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self._statuses:
            status = self._statuses.pop(0)
            raise HttpError(httplib2.Response({"status": status}), b"err")
        return self._result


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)  # no real backoff
    req = _Req([429, 429])
    assert _retry.execute(req) == "ok"
    assert req.calls == 3


def test_raises_on_non_retryable(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)
    req = _Req([404])
    with pytest.raises(HttpError):
        _retry.execute(req)
    assert req.calls == 1


class _SSLReq:
    """Fake request that raises a transport error N times then succeeds."""

    def __init__(self, fails):
        self.fails = fails
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.calls <= self.fails:
            raise OSError("[SSL] record layer failure")
        return "ok"


def test_retries_on_transport_error(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)
    req = _SSLReq(fails=2)
    assert _retry.execute(req) == "ok"
    assert req.calls == 3


def test_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)
    req = _Req([503, 503, 503])
    with pytest.raises(HttpError):
        _retry.execute(req, attempts=2)
    assert req.calls == 2


class _RefreshReq:
    """Fake request whose execute() fails token refresh (expired/revoked token)."""

    def __init__(self):
        self.calls = 0

    def execute(self):
        self.calls += 1
        raise RefreshError("invalid_grant: Token has been expired or revoked.")


@pytest.fixture(autouse=True)
def _reset_auth_health():
    health.mark_ok()
    yield
    health.mark_ok()


def test_refresh_error_raises_google_auth_error_without_retrying(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)
    req = _RefreshReq()
    with pytest.raises(health.GoogleAuthError):
        _retry.execute(req)
    assert req.calls == 1  # not retried — a bad token never recovers on retry


def test_refresh_error_marks_auth_health_down(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)
    assert health.status()["ok"] is True
    with pytest.raises(health.GoogleAuthError):
        _retry.execute(_RefreshReq())
    snap = health.status()
    assert snap["ok"] is False
    assert snap["since"] is not None
    assert "remediation" in snap


def test_success_clears_prior_auth_failure():
    health.mark_auth_error("boom")
    assert health.status()["ok"] is False
    assert _retry.execute(_Req([])) == "ok"  # a clean call recovers the signal
    assert health.status()["ok"] is True
