"""Google API retry wrapper."""

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.connectors.google import _retry


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


def test_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr(_retry.time, "sleep", lambda *_: None)
    req = _Req([503, 503, 503])
    with pytest.raises(HttpError):
        _retry.execute(req, attempts=2)
    assert req.calls == 2
