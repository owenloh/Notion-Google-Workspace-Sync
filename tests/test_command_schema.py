"""Command parsing + relay guard behavior."""

import httpx

from app.config import Settings
from app.connectors.relay import RelayClient, _extract_affected_id
from app.engines.command_schema import CommandError, RelayRequest, parse_command


def test_parse_full_envelope():
    req = parse_command('{"path":"/api/notion/update-page","method":"POST","body":{"x":1}}')
    assert isinstance(req, RelayRequest)
    assert req.path == "/api/notion/update-page"
    assert req.method == "POST"
    assert req.body == {"x": 1}


def test_parse_bare_body_uses_default_path():
    req = parse_command('{"Name":"Email Bob"}', default_path="/api/notion/create-pages")
    assert isinstance(req, RelayRequest)
    assert req.path == "/api/notion/create-pages"
    assert req.body == {"Name": "Email Bob"}


def test_parse_bare_body_without_default_errors():
    res = parse_command('{"Name":"x"}')
    assert isinstance(res, CommandError)


def test_parse_key_value_lines():
    text = "path: /api/notion/create-pages\nName: Email Bob\nstatus: Next"
    req = parse_command(text)
    assert isinstance(req, RelayRequest)
    assert req.path == "/api/notion/create-pages"
    assert req.body == {"name": "Email Bob", "status": "Next"}


def test_parse_invalid_json_errors():
    res = parse_command("{not json")
    assert isinstance(res, CommandError)


def test_parse_empty_errors():
    assert isinstance(parse_command(""), CommandError)
    assert isinstance(parse_command(None), CommandError)


# --- relay guards ---

def _client(transport, **over):
    settings = Settings(
        relay_api_base_url="https://api.test",
        relay_api_key="k",
        **over,
    )
    return RelayClient(settings=settings, client=httpx.Client(transport=transport, base_url="https://api.test"))


def test_relay_rejects_disallowed_path():
    rc = _client(httpx.MockTransport(lambda r: httpx.Response(200)))
    res = rc.execute(RelayRequest(path="/api/github/push-file", body={}))
    assert res.ok is False and "not allowed" in res.summary


def test_relay_blocks_replace_content_without_force():
    rc = _client(httpx.MockTransport(lambda r: httpx.Response(200)))
    res = rc.execute(
        RelayRequest(path="/api/notion/update-page", body={"command": "replace_content"})
    )
    assert res.ok is False and "replace_content" in res.summary


def test_relay_forwards_allowed_and_extracts_id():
    def handler(request):
        assert request.url.path == "/api/notion/create-pages"
        return httpx.Response(200, json={"id": "new-page-123"})

    rc = _client(httpx.MockTransport(handler))
    res = rc.execute(RelayRequest(path="/api/notion/create-pages", body={"Name": "x"}))
    assert res.ok is True and res.affected_id == "new-page-123"


def test_relay_reports_error_status():
    rc = _client(httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad"})))
    res = rc.execute(
        RelayRequest(path="/api/notion/update-page", body={"command": "update_properties"})
    )
    assert res.ok is False and "400" in res.summary


def test_extract_affected_id_from_url():
    assert _extract_affected_id({"url": "https://notion.so/Title-abc123def"}) == "abc123def"
