"""NotionClient pagination: cursor placement differs by verb (GET vs POST)."""

from app.connectors.notion.client import NotionClient


def _client_with_fake_request():
    """A NotionClient whose .request is stubbed to record calls + return 2 pages."""
    c = NotionClient.__new__(NotionClient)  # bypass __init__ (no real httpx client)
    calls = []

    def fake_request(method, path, *, json=None, params=None, version=None):
        calls.append({"params": params, "json": json})
        first = not (params and params.get("start_cursor")) and not (
            json and json.get("start_cursor")
        )
        if first:
            return {"results": ["a"], "has_more": True, "next_cursor": "CUR"}
        return {"results": ["b"], "has_more": False}

    c.request = fake_request
    return c, calls


def test_get_pagination_passes_cursor_as_query_param_not_body():
    c, calls = _client_with_fake_request()
    out = list(c.paginate("GET", "/blocks/x/children"))
    assert out == ["a", "b"]
    # Second page carries the cursor in PARAMS, with no JSON body (Notion 400s on
    # body.start_cursor for GET).
    assert calls[1]["params"] == {"start_cursor": "CUR"}
    assert calls[1]["json"] is None


def test_post_pagination_passes_cursor_in_body():
    c, calls = _client_with_fake_request()
    out = list(c.paginate("POST", "/search", json={"filter": 1}))
    assert out == ["a", "b"]
    assert calls[1]["json"] == {"filter": 1, "start_cursor": "CUR"}
    assert calls[1]["params"] is None
