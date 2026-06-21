"""Process-wide runtime: the live Notion and Google adapters.

Building Google API clients is relatively expensive, so the runtime is created
once and reused by the scheduler jobs and the webhook handler.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.connectors.notion.client import NotionClient
from app.engines.google_mirror import GoogleMirror
from app.engines.notion_source import NotionSource

_runtime: Runtime | None = None


@dataclass
class Runtime:
    settings: Settings
    notion: NotionSource
    google: GoogleMirror


def build_runtime(settings: Settings | None = None) -> Runtime:
    from app.connectors.google.auth import build_services

    settings = settings or get_settings()
    client = NotionClient(settings)
    services = build_services(settings)
    google = GoogleMirror(
        services,
        root_folder_id=settings.google_drive_mirror_folder_id,
        index_sheet_id=settings.google_index_sheet_id,
    )
    return Runtime(settings, NotionSource(client, settings), google)


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime
