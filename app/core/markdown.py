"""Body conversion: Notion blocks ⇄ canonical Markdown ⇄ Google Docs.

Markdown is the interchange format for page bodies. The hard requirement is
*round-trip stability*: converting the same content back and forth must land on
the identical canonical Markdown, otherwise echo suppression (which compares body
hashes) would loop forever. We therefore support a disciplined subset and emit it
in a fixed canonical form.

Supported block types: headings (h1-h3), paragraphs, bulleted/numbered lists,
to-do items, quotes, code blocks, and dividers. Unsupported Notion block types
degrade to a paragraph of their plain text. Inline formatting: bold, italic,
strikethrough, inline code, and links, emitted in a fixed nesting order
(link → bold → italic → strikethrough → code).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Inline rich text
# ---------------------------------------------------------------------------

# Canonical nesting order, outermost first. blocks->md emits in this order and
# the parser peels markers in the same order, guaranteeing stability.
_BOLD = "**"
_ITALIC = "_"
_STRIKE = "~~"
_CODE = "`"


def rich_text_to_md(rich_text: list[dict]) -> str:
    """Convert a Notion ``rich_text`` array to canonical inline Markdown."""
    out: list[str] = []
    for span in rich_text or []:
        text = span.get("text", {}).get("content")
        if text is None:
            text = span.get("plain_text", "")
        link = (span.get("text", {}) or {}).get("link")
        href = link.get("url") if isinstance(link, dict) else None
        ann = span.get("annotations", {}) or {}

        s = text
        # Inner → outer so the final string nests outer → inner.
        if ann.get("code"):
            s = f"{_CODE}{s}{_CODE}"
        if ann.get("strikethrough"):
            s = f"{_STRIKE}{s}{_STRIKE}"
        if ann.get("italic"):
            s = f"{_ITALIC}{s}{_ITALIC}"
        if ann.get("bold"):
            s = f"{_BOLD}{s}{_BOLD}"
        if href:
            s = f"[{s}]({href})"
        out.append(s)
    return "".join(out)


_LINK_RE = re.compile(r"^\[(?P<text>.+?)\]\((?P<url>[^)]*)\)")


def md_to_rich_text(md: str) -> list[dict]:
    """Parse canonical inline Markdown back into a Notion ``rich_text`` array."""
    spans: list[dict] = []
    _parse_inline(md, {}, None, spans)
    return spans or [_make_span("", {}, None)]


def _make_span(text: str, ann: dict, href: str | None) -> dict:
    annotations = {
        "bold": ann.get("bold", False),
        "italic": ann.get("italic", False),
        "strikethrough": ann.get("strikethrough", False),
        "underline": False,
        "code": ann.get("code", False),
        "color": "default",
    }
    text_obj: dict = {"content": text}
    if href:
        text_obj["link"] = {"url": href}
    return {"type": "text", "text": text_obj, "annotations": annotations}


def _parse_inline(s: str, ann: dict, href: str | None, out: list[dict]) -> None:
    """Recursively peel markers in canonical order, appending spans to ``out``."""
    i = 0
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            out.append(_make_span(buf, ann, href))
            buf = ""

    while i < len(s):
        rest = s[i:]
        # Links (only when not already inside a link).
        if href is None and rest.startswith("["):
            m = _LINK_RE.match(rest)
            if m:
                flush()
                _parse_inline(m.group("text"), ann, m.group("url"), out)
                i += m.end()
                continue
        # Bold.
        if not ann.get("bold") and rest.startswith(_BOLD):
            end = s.find(_BOLD, i + len(_BOLD))
            if end != -1:
                flush()
                _parse_inline(s[i + len(_BOLD):end], {**ann, "bold": True}, href, out)
                i = end + len(_BOLD)
                continue
        # Strikethrough.
        if not ann.get("strikethrough") and rest.startswith(_STRIKE):
            end = s.find(_STRIKE, i + len(_STRIKE))
            if end != -1:
                flush()
                _parse_inline(
                    s[i + len(_STRIKE):end], {**ann, "strikethrough": True}, href, out
                )
                i = end + len(_STRIKE)
                continue
        # Italic (single char; checked after bold/strike to avoid clashes).
        if not ann.get("italic") and rest.startswith(_ITALIC):
            end = s.find(_ITALIC, i + len(_ITALIC))
            if end != -1:
                flush()
                _parse_inline(
                    s[i + len(_ITALIC):end], {**ann, "italic": True}, href, out
                )
                i = end + len(_ITALIC)
                continue
        # Inline code (literal contents, no nested parsing).
        if not ann.get("code") and rest.startswith(_CODE):
            end = s.find(_CODE, i + len(_CODE))
            if end != -1:
                flush()
                out.append(_make_span(s[i + len(_CODE):end], {**ann, "code": True}, href))
                i = end + len(_CODE)
                continue
        buf += s[i]
        i += 1
    flush()


# ---------------------------------------------------------------------------
# Notion blocks ⇄ Markdown
# ---------------------------------------------------------------------------

def _plain(block: dict, key: str) -> str:
    return rich_text_to_md(block.get(key, {}).get("rich_text", []))


def _indent(text: str, depth: int) -> str:
    """Indent every line of ``text`` by ``depth`` levels (2 spaces each)."""
    if depth <= 0:
        return text
    pad = "  " * depth
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def notion_blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str:
    """Render Notion blocks to read-optimized Markdown (Notion → Google, one-way).

    Blocks may carry a ``children`` list (populated by the reader for any block
    with ``has_children``); children render recursively, indented one level. This
    direction is one-way, so it is free to be richer than the reverse parser:
    callouts → quotes, toggles → bold summary + indented body, tables → GFM,
    images/bookmarks → links. ``child_page`` blocks are skipped (mirrored as their
    own items).
    """
    chunks: list[str] = []

    def emit(text: str) -> None:
        chunks.append(_indent(text, depth))

    rows: list[dict] = []  # accumulate table_row blocks for a parent table

    for block in blocks or []:
        t = block.get("type")
        children = block.get("children") or []
        child_md = (
            notion_blocks_to_markdown(children, depth + 1)
            if children and t not in {"child_page", "table"}
            else ""
        )

        if t == "heading_1":
            emit(f"# {_plain(block, t)}")
        elif t == "heading_2":
            emit(f"## {_plain(block, t)}")
        elif t == "heading_3":
            emit(f"### {_plain(block, t)}")
        elif t == "bulleted_list_item":
            emit(f"- {_plain(block, t)}")
        elif t == "numbered_list_item":
            emit(f"1. {_plain(block, t)}")
        elif t == "to_do":
            box = "[x]" if block.get(t, {}).get("checked") else "[ ]"
            emit(f"- {box} {_plain(block, t)}")
        elif t == "quote":
            emit(f"> {_plain(block, t)}")
        elif t == "callout":
            icon = (block.get(t, {}).get("icon") or {}).get("emoji", "💡")
            emit(f"> {icon} {_plain(block, t)}")
        elif t == "toggle":
            emit(f"**▸ {_plain(block, t)}**")
        elif t == "code":
            lang = block.get(t, {}).get("language", "") or ""
            lang = "" if lang == "plain text" else lang
            emit(f"```{lang}\n{_plain(block, t)}\n```")
        elif t == "divider":
            emit("---")
        elif t == "paragraph":
            emit(_plain(block, t))
        elif t == "table":
            emit(_render_table(children))
            child_md = ""  # consumed
        elif t == "table_row":
            rows.append(block)  # handled by parent table; ignore standalone
        elif t == "image":
            emit(_render_file_link(block, t, "image", "🖼"))
        elif t in {"bookmark", "embed", "link_preview"}:
            url = block.get(t, {}).get("url", "")
            cap = _plain(block, t) or url
            emit(f"[🔗 {cap}]({url})" if url else cap)
        elif t in {"file", "pdf", "video", "audio"}:
            emit(_render_file_link(block, t, t, "📎"))
        elif t == "equation":
            emit(f"$$ {block.get(t, {}).get('expression', '')} $$")
        elif t == "child_database":
            title = block.get(t, {}).get("title", "Database")
            emit(f"> 📊 *Database: {title}* (open in Notion)")
        elif t == "child_page":
            continue  # mirrored as its own item
        else:
            text = _plain(block, t) if isinstance(block.get(t), dict) else ""
            if text:
                emit(text)
            else:
                child_md = ""

        if child_md:
            chunks.append(child_md)

    return "\n\n".join(c for c in chunks if c).strip("\n")


def _render_table(row_blocks: list[dict]) -> str:
    """Render a Notion table's ``table_row`` children as a GFM table."""
    rows: list[list[str]] = []
    for rb in row_blocks:
        if rb.get("type") != "table_row":
            continue
        cells = rb.get("table_row", {}).get("cells", [])
        rows.append([rich_text_to_md(cell) for cell in cells])
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _render_file_link(block: dict, key: str, label: str, icon: str) -> str:
    data = block.get(key, {}) or {}
    url = (data.get("external") or {}).get("url") or (data.get("file") or {}).get("url", "")
    caption = rich_text_to_md(data.get("caption", [])) or label
    return f"[{icon} {caption}]({url})" if url else f"{icon} {caption}"


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_TODO_RE = re.compile(r"^-\s+\[( |x|X)\]\s+(.*)$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")


def markdown_to_notion_blocks(markdown: str) -> list[dict]:
    """Convert canonical Markdown into Notion block payloads (for create/update)."""
    blocks: list[dict] = []
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            blocks.append(_code_block("\n".join(body), lang))
            continue

        if stripped == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            blocks.append(_text_block(f"heading_{level}", m.group(2)))
            i += 1
            continue

        m = _TODO_RE.match(stripped)
        if m:
            checked = m.group(1).lower() == "x"
            blk = _text_block("to_do", m.group(2))
            blk["to_do"]["checked"] = checked
            blocks.append(blk)
            i += 1
            continue

        m = _BULLET_RE.match(stripped)
        if m:
            blocks.append(_text_block("bulleted_list_item", m.group(1)))
            i += 1
            continue

        m = _NUMBERED_RE.match(stripped)
        if m:
            blocks.append(_text_block("numbered_list_item", m.group(1)))
            i += 1
            continue

        m = _QUOTE_RE.match(stripped)
        if m:
            blocks.append(_text_block("quote", m.group(1)))
            i += 1
            continue

        blocks.append(_text_block("paragraph", stripped))
        i += 1

    return blocks


def _text_block(block_type: str, text: str) -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": md_to_rich_text(text)},
    }


def _code_block(text: str, lang: str) -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": [_make_span(text, {}, None)],
            "language": lang or "plain text",
        },
    }


# ---------------------------------------------------------------------------
# Canonical Markdown ⇄ Google Docs
# ---------------------------------------------------------------------------
#
# Headings are rendered with native Docs heading styles (the "#" prefix is
# stripped on the way in and reconstructed from the paragraph style on the way
# out). Everything else — including inline ``**bold**`` markers — is stored as
# literal text. This keeps the most visible richness (headings) while remaining
# perfectly round-trip stable, which is what echo suppression depends on.

_DOCS_HEADING_STYLE = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}
_STYLE_TO_LEVEL = {v: k for k, v in _DOCS_HEADING_STYLE.items()}


def _line_heading_level(line: str) -> tuple[int, str]:
    m = _HEADING_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2)
    return 0, line


def clear_doc_requests(document: dict) -> list[dict]:
    """Requests that delete all existing body content of a Docs document."""
    content = document.get("body", {}).get("content", [])
    end = 1
    for el in content:
        end = max(end, el.get("endIndex", 1))
    if end <= 2:
        return []
    # Leave the final newline (Docs requires the body to end with one).
    return [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}}]


def markdown_to_docs_requests(markdown: str) -> list[dict]:
    """Build Docs ``batchUpdate`` requests that render canonical Markdown.

    The caller is expected to first clear the document (see
    :func:`clear_doc_requests`). Returns an insertText request plus one
    updateParagraphStyle request per heading line.
    """
    lines = (markdown or "").split("\n")
    rendered: list[tuple[int, str]] = [_line_heading_level(ln) for ln in lines]
    text = "\n".join(t for _, t in rendered) + "\n"

    requests: list[dict] = [{"insertText": {"location": {"index": 1}, "text": text}}]

    idx = 1
    for level, t in rendered:
        para_len = len(t) + 1  # include the newline
        if level:
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": idx, "endIndex": idx + para_len},
                        "paragraphStyle": {
                            "namedStyleType": _DOCS_HEADING_STYLE[level]
                        },
                        "fields": "namedStyleType",
                    }
                }
            )
        idx += para_len
    return requests


def _paragraph_text(paragraph: dict) -> str:
    parts: list[str] = []
    for el in paragraph.get("elements", []):
        run = el.get("textRun")
        if run and "content" in run:
            parts.append(run["content"])
    return "".join(parts)


def docs_document_to_markdown(document: dict) -> str:
    """Extract canonical Markdown from a Google Docs document resource."""
    lines: list[str] = []
    for el in document.get("body", {}).get("content", []):
        paragraph = el.get("paragraph")
        if not paragraph:
            continue
        text = _paragraph_text(paragraph).replace("\n", "")
        style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
        level = _STYLE_TO_LEVEL.get(style)
        if level:
            lines.append(f"{'#' * level} {text}".rstrip())
        else:
            lines.append(text)
    return "\n".join(lines).strip("\n")
