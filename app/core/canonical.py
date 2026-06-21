"""Canonical projections of a synced item.

The same logical content can arrive from Notion (page properties / blocks) or
from Google (a Sheet row / a Doc body). To suppress echoes we must be able to
say "these two representations mean the same thing". We do that by reducing each
side to a *canonical projection* — a normalized, order-independent structure —
and hashing it (see :mod:`app.core.hashing`).

There are two facets per item:

* **properties** — the structured fields that live in a Sheet row.
* **body** — the rich page body, carried as canonical Markdown.

Both Notion connectors and Google connectors are responsible for producing these
projections; this module owns the normalization rules so both sides agree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Property keys that hold Notion relations (lists of related item names). These
# are normalized as sorted name lists rather than scalars.
RELATION_KEYS = {"area", "projects", "project", "next actions", "next_actions"}


def _norm_scalar(value: object) -> str:
    """Normalize a scalar property value to a stable string.

    Strips surrounding whitespace and collapses internal whitespace runs to a
    single space so that cosmetic differences between Notion and Sheets (e.g. a
    cell that gained a trailing space) do not change the hash.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _norm_date(value: object) -> str:
    """Normalize a date value to ``YYYY-MM-DD`` (date-only)."""
    s = _norm_scalar(value)
    if not s:
        return ""
    # Accept full ISO timestamps and keep only the date part.
    return s[:10]


def _norm_relation(values: object) -> list[str]:
    """Normalize a relation value to a sorted, de-duplicated list of names."""
    if values is None:
        return []
    if isinstance(values, str):
        # Sheets stores relations as a comma-separated string of names.
        items: Iterable[str] = values.split(",")
    elif isinstance(values, Iterable):
        items = [str(v) for v in values]
    else:
        items = [str(values)]
    cleaned = {_norm_scalar(v) for v in items}
    cleaned.discard("")
    return sorted(cleaned)


def property_projection(kind: str, props: dict[str, object]) -> dict[str, object]:
    """Reduce a raw property dict to its canonical projection.

    ``props`` keys are matched case-insensitively. Unknown keys are dropped so
    that incidental columns (bookkeeping ``_*`` columns, computed values) never
    affect equality.

    Returns a plain dict; :func:`app.core.hashing.canonical_hash` serializes it
    deterministically.
    """
    lowered = {str(k).strip().lower(): v for k, v in props.items()}
    out: dict[str, object] = {"kind": kind}

    for key, value in lowered.items():
        if key.startswith("_") or key in {"doc", "checkbox_raw"}:
            continue
        if key in RELATION_KEYS:
            out[key] = _norm_relation(value)
        elif key in {"due", "due date", "date"}:
            out[key] = _norm_date(value)
        elif key in {"checkbox"}:
            out[key] = _norm_scalar(value) in {"true", "1", "yes", "✓", "x"}
        else:
            out[key] = _norm_scalar(value)
    return out


# --- Body (Markdown) normalization ----------------------------------------

_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_MULTI_BLANK = re.compile(r"\n{3,}")


def body_projection(markdown: str | None) -> str:
    """Normalize a Markdown body so trivial differences do not change the hash.

    Rules: unify line endings, drop trailing whitespace on each line, collapse
    3+ consecutive blank lines to a single blank line, and trim leading/trailing
    blank lines. This is the canonical form round-tripped between Notion blocks
    and Google Docs; the conversion must land back here stably.
    """
    if not markdown:
        return ""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip("\n")
