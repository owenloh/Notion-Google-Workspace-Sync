"""Google OAuth (user credentials) and service construction.

A personal Gmail account is used, so a service account is unsuitable (consumer
Drive has no shared-quota for service accounts). Instead we use OAuth *user*
credentials with an offline refresh token obtained once via
``scripts/bootstrap.py``. The client config and refresh token come from the
environment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from google.oauth2.credentials import Credentials

from app.config import Settings, get_settings

# Drive (files + changes), Docs, and Sheets read/write.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]


def build_credentials(settings: Settings | None = None) -> Credentials:
    settings = settings or get_settings()
    if not settings.google_credentials_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON is not set")
    if not settings.google_oauth_refresh_token:
        raise RuntimeError("GOOGLE_OAUTH_REFRESH_TOKEN is not set")

    cfg = json.loads(settings.google_credentials_json)
    # Accept both 'installed' and 'web' OAuth client shapes.
    client = cfg.get("installed") or cfg.get("web") or cfg
    return Credentials(
        token=None,
        refresh_token=settings.google_oauth_refresh_token,
        token_uri=client.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=SCOPES,
    )


@dataclass
class GoogleServices:
    drive: object
    docs: object
    sheets: object


def build_services(settings: Settings | None = None) -> GoogleServices:
    """Construct Drive/Docs/Sheets API clients (imported lazily for testability)."""
    from googleapiclient.discovery import build

    creds = build_credentials(settings)
    return GoogleServices(
        drive=build("drive", "v3", credentials=creds, cache_discovery=False),
        docs=build("docs", "v1", credentials=creds, cache_discovery=False),
        sheets=build("sheets", "v4", credentials=creds, cache_discovery=False),
    )
