"""Deterministic content hashing used for echo suppression and drift detection.

A hash is taken over a canonical projection (see :mod:`app.core.canonical`). Two
representations that mean the same thing produce the same hash regardless of
which system they came from or the order their fields happened to be in.
"""

from __future__ import annotations

import hashlib
import json

from app.core import canonical


def _stable_dumps(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_hash(obj: object) -> str:
    """Hash any JSON-serializable canonical projection (dict or string)."""
    payload = obj if isinstance(obj, str) else _stable_dumps(obj)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def property_hash(kind: str, props: dict[str, object]) -> str:
    """Hash of the structured (Sheet-row) facet of an item."""
    return canonical_hash(canonical.property_projection(kind, props))


def body_hash(markdown: str | None) -> str:
    """Hash of the body (Doc) facet of an item."""
    return canonical_hash(canonical.body_projection(markdown))
