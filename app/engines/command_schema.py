"""Parsing a Google Tasks command into a relay request.

Gemini writes a command into a task's notes/title. To tolerate best-effort
adherence from Live voice, ``parse_command`` accepts three shapes:

1. A JSON object with ``path`` (and optional ``method``/``body``) — preferred.
2. A bare JSON object — treated as the ``body`` for the configured default path.
3. ``key: value`` lines — ``path:``/``method:`` plus body fields.

The result is a :class:`RelayRequest`; anything unparseable is a :class:`CommandError`
whose message is written back as the task receipt so the user/Gemini can retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class RelayRequest:
    path: str
    method: str = "POST"
    body: dict = field(default_factory=dict)


@dataclass
class CommandError:
    message: str


def _looks_like_json(text: str) -> bool:
    t = text.lstrip()
    return t.startswith("{") or t.startswith("[")


def _from_obj(obj: dict, default_path: str | None) -> RelayRequest | CommandError:
    if not isinstance(obj, dict):
        return CommandError("command must be a JSON object")
    # Shape 1: explicit request envelope.
    if "path" in obj:
        path = str(obj["path"]).strip()
        method = str(obj.get("method", "POST")).strip().upper() or "POST"
        body = obj.get("body", {})
        if not isinstance(body, dict):
            return CommandError("'body' must be an object")
        return RelayRequest(path=path, method=method, body=body)
    # Shape 2: bare body for the default path.
    if default_path:
        return RelayRequest(path=default_path, method="POST", body=obj)
    return CommandError("missing 'path' and no default path configured")


def _from_kv_lines(text: str, default_path: str | None) -> RelayRequest | CommandError:
    path = default_path
    method = "POST"
    body: dict = {}
    explicit_path = False
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "path":
            path = value
            explicit_path = True
        elif key == "method":
            method = value.upper() or "POST"
        elif key == "body":
            try:
                body = json.loads(value)
            except json.JSONDecodeError:
                return CommandError("'body' line must be valid JSON")
        else:
            body[key] = value
    if not path:
        return CommandError("missing 'path' and no default path configured")
    # Nothing parsed (no explicit path and no fields) → treat as unparseable.
    if not explicit_path and not body:
        return CommandError(
            "could not parse command; expected JSON {path,body} or 'key: value' lines"
        )
    return RelayRequest(path=path, method=method, body=body)


def parse_command(text: str | None, default_path: str | None = None) -> RelayRequest | CommandError:
    """Parse a command string into a :class:`RelayRequest` or :class:`CommandError`."""
    if not text or not text.strip():
        return CommandError("empty command")
    text = text.strip()
    if _looks_like_json(text):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            return CommandError(f"invalid JSON: {exc.msg}")
        return _from_obj(obj, default_path)
    return _from_kv_lines(text, default_path)
