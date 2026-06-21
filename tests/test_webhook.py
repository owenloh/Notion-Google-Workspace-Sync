"""Notion webhook signature verification and handshake handling."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.api.webhooks.notion import extract_page_ids, verify_signature
from app.main import app


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_roundtrip():
    body = b'{"hello":"world"}'
    sig = _sign("topsecret", body)
    assert verify_signature("topsecret", body, sig) is True
    assert verify_signature("topsecret", body, "sha256=deadbeef") is False
    assert verify_signature("", body, sig) is False
    assert verify_signature("topsecret", body, None) is False


def test_extract_page_ids():
    event = {"entity": {"type": "page", "id": "p1"}}
    assert extract_page_ids(event) == ["p1"]
    assert extract_page_ids({"entity": {"type": "database", "id": "d1"}}) == []


def test_handshake_returns_200(monkeypatch):
    client = TestClient(app)
    resp = client.post("/webhooks/notion", json={"verification_token": "abc123"})
    assert resp.status_code == 200


def test_bad_signature_rejected(monkeypatch):
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", "topsecret")
    # get_settings is cached; patch the loaded value directly.
    from app.config import get_settings
    get_settings.cache_clear()
    client = TestClient(app)
    resp = client.post(
        "/webhooks/notion",
        content=b'{"entity":{"type":"page","id":"p1"}}',
        headers={"X-Notion-Signature": "sha256=wrong"},
    )
    assert resp.status_code == 401
    get_settings.cache_clear()


def test_admin_full_sync_requires_key(monkeypatch):
    from app.config import get_settings
    get_settings.cache_clear()  # ADMIN_API_KEY unset -> endpoint disabled
    client = TestClient(app)
    assert client.post("/admin/full-sync").status_code == 503

    monkeypatch.setenv("ADMIN_API_KEY", "letmein")
    get_settings.cache_clear()
    assert client.post("/admin/full-sync").status_code == 401
    assert client.post("/admin/full-sync?key=wrong").status_code == 401
    assert client.post(
        "/admin/full-sync", headers={"X-Admin-Key": "nope"}
    ).status_code == 401
    get_settings.cache_clear()


def test_command_requires_key(monkeypatch):
    from app.config import get_settings
    get_settings.cache_clear()  # ADMIN_API_KEY unset -> disabled
    client = TestClient(app)
    body = {"path": "/api/notion/update-page", "body": {}}
    assert client.post("/command", json=body).status_code == 503

    monkeypatch.setenv("ADMIN_API_KEY", "letmein")
    get_settings.cache_clear()
    assert client.post("/command", json=body).status_code == 401
    assert client.post("/command?key=wrong", json=body).status_code == 401
    get_settings.cache_clear()


def test_valid_signature_with_no_pages_acknowledged(monkeypatch):
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", "topsecret")
    from app.config import get_settings
    get_settings.cache_clear()
    body = json.dumps({"entity": {"type": "database", "id": "d1"}}).encode()
    client = TestClient(app)
    resp = client.post(
        "/webhooks/notion",
        content=body,
        headers={"X-Notion-Signature": _sign("topsecret", body)},
    )
    assert resp.status_code == 200
    get_settings.cache_clear()
