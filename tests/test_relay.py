"""Relay response parsing (affected-id extraction + skill text)."""

from app.connectors.relay import _extract_affected_id, _skill_text


def test_extract_id_from_create_pages_list_response():
    # The live API returns create-pages as {"created": [{"id", "url", ...}], ...}.
    resp = {
        "created": [
            {
                "id": "3866f0cc-dd76-81ec-b8ed-ec34df48a99e",
                "url": "https://app.notion.com/p/TEST-3866f0ccdd7681ecb8edec34df48a99e",
                "title": "TEST",
            }
        ],
        "count": 1,
    }
    assert _extract_affected_id(resp) == "3866f0cc-dd76-81ec-b8ed-ec34df48a99e"


def test_extract_id_from_top_level_and_nested():
    assert _extract_affected_id({"id": "abc"}) == "abc"
    assert _extract_affected_id({"page": {"page_id": "xyz"}}) == "xyz"
    assert _extract_affected_id({"results": [{"id": "r1"}]}) == "r1"


def test_extract_id_from_app_notion_url():
    resp = {"url": "https://app.notion.com/p/Name-deadbeef"}
    assert _extract_affected_id(resp) == "deadbeef"


def test_extract_id_none_when_absent():
    assert _extract_affected_id({"count": 1}) is None
    assert _extract_affected_id("plain text") is None


def test_skill_text_extracts_instructions_from_json():
    raw = '{"skill": "notion-master", "instructions": "# Rules\\nbody"}'
    assert _skill_text(raw) == "# Rules\nbody"


def test_skill_text_falls_back_to_raw():
    assert _skill_text("not json") == "not json"
    assert _skill_text('{"no_instructions": true}') == '{"no_instructions": true}'
