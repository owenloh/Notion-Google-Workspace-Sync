"""Round-trip stability for body conversions.

The hard requirement is that converting content back and forth lands on identical
canonical Markdown, since echo suppression compares body hashes.
"""

from app.core import markdown as md
from app.core.canonical import body_projection

SAMPLE = """# Project Engine

Some intro with **bold**, _italic_, ~~strike~~ and `code` plus a [link](https://example.com).

## Tasks

- first item
- second item
- [ ] todo open
- [x] todo done

1. step one
1. step two

> a quote line

```python
print("hello")
```

---

Closing paragraph."""


def _notion_roundtrip(markdown: str) -> str:
    blocks = md.markdown_to_notion_blocks(markdown)
    return md.notion_blocks_to_markdown(blocks)


def test_markdown_notion_roundtrip_is_stable():
    once = _notion_roundtrip(SAMPLE)
    twice = _notion_roundtrip(once)
    assert once == twice


def test_markdown_notion_roundtrip_preserves_content():
    # The canonical form blank-separates every block, so compare the meaningful
    # (non-blank) lines rather than exact spacing.
    out = _notion_roundtrip(SAMPLE)
    non_blank = lambda s: [ln for ln in s.split("\n") if ln.strip()]  # noqa: E731
    assert non_blank(out) == non_blank(SAMPLE)


def test_inline_formatting_roundtrip():
    cases = [
        "plain text",
        "has **bold** word",
        "has _italic_ word",
        "has ~~strike~~ word",
        "has `code` word",
        "a [link](https://example.com) here",
    ]
    for case in cases:
        rt = md.rich_text_to_md(md.md_to_rich_text(case))
        assert rt == case, f"failed roundtrip for: {case!r} -> {rt!r}"


def test_nested_bold_italic_roundtrip():
    case = "**_both_**"
    rt = md.rich_text_to_md(md.md_to_rich_text(case))
    assert rt == case


def _fake_docs_document(requests: list[dict]) -> dict:
    """Apply insertText + heading-style requests to a fake empty Docs document.

    Models the subset of Docs behavior our converter relies on so we can test the
    Markdown <-> Docs round-trip without the live API.
    """
    insert = next(r for r in requests if "insertText" in r)
    text = insert["insertText"]["text"]
    styles = {
        r["updateParagraphStyle"]["range"]["startIndex"]: r["updateParagraphStyle"][
            "paragraphStyle"
        ]["namedStyleType"]
        for r in requests
        if "updateParagraphStyle" in r
    }
    content = []
    idx = 1
    for line in text.split("\n"):
        if idx > 1 and line == "" and idx - 1 == len(text):
            break
        para_text = line + "\n"
        para = {
            "paragraph": {
                "elements": [{"textRun": {"content": para_text}}],
                "paragraphStyle": {"namedStyleType": styles.get(idx, "NORMAL_TEXT")},
            },
            "endIndex": idx + len(para_text),
        }
        content.append(para)
        idx += len(para_text)
    return {"body": {"content": content}}


def test_markdown_docs_roundtrip_is_stable():
    canonical = _notion_roundtrip(SAMPLE)
    requests = md.markdown_to_docs_requests(canonical)
    doc = _fake_docs_document(requests)
    back = md.docs_document_to_markdown(doc)
    assert body_projection(back) == body_projection(canonical)


def test_headings_use_docs_styles():
    requests = md.markdown_to_docs_requests("# Title\n\nbody")
    styles = [r for r in requests if "updateParagraphStyle" in r]
    assert styles
    assert styles[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
