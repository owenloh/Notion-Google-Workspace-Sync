"""Application settings loaded from environment / .env.

Everything the service needs to reach Notion and Google, plus the cadences and
identifiers that drive the sync, lives here. Secrets are never hard-coded; they
come from the environment (see ``.env.example``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# The three core databases plus the two loose-page parents. Used as the default
# crawl roots when ``NOTION_ROOT_IDS`` is not set. Kept here (not in .env) so the
# known structure is version-controlled and discoverable.
#
# IMPORTANT: the classic Notion REST endpoint we query (`databases/{id}/query`,
# API version 2022-06-28) addresses each spine database by its **database id**.
# The newer multi-source "data source" / collection ids (54816fca / f0ea8841 /
# 1d3eb1dd) return 404 on that endpoint, so the roots MUST be the database ids.
DEFAULT_NOTION_ROOTS: tuple[str, ...] = (
    "dfa76d06-073b-4493-9f96-319a9f088a5e",  # Areas of Focus (database)
    "b9c0cd8c-fa6c-46d1-95ed-87d7ef97d971",  # Projects (database)
    "2ebc58c5-8617-4748-8021-fcc2a37d3a97",  # Actions (database)
    "34f6f0cc-dd76-801d-b0ec-de6c10685d10",  # Mission Control (page → Briefing)
    "1fa6f0cc-dd76-809e-8bcb-e5db5ae28237",  # Library (page → References tray)
)

# Database ids for the relational spine, so engines can classify a page by which
# database it belongs to and query its rows.
AREAS_DS_ID = DEFAULT_NOTION_ROOTS[0]
PROJECTS_DS_ID = DEFAULT_NOTION_ROOTS[1]
ACTIONS_DS_ID = DEFAULT_NOTION_ROOTS[2]

# Legacy data-source / collection ids for the same three databases. A page's
# ``parent`` may report either the database id or the data-source id depending on
# the API version, so classification (kind_for_parent) accepts both.
LEGACY_SPINE_DS_IDS: dict[str, str] = {
    "54816fca-6f6c-4588-8c1a-1cdfcc6c9092": "area",
    "f0ea8841-ca74-47b7-a28a-0b367bca8c41": "project",
    "1d3eb1dd-2803-4692-a4d5-6ca9709ae570": "action",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Notion ---
    notion_api_token: str = ""
    notion_webhook_secret: str = ""
    sync_bot_notion_user_id: str = ""
    notion_root_ids: str = ""  # comma-separated; blank → DEFAULT_NOTION_ROOTS

    # --- Google ---
    google_credentials_json: str = ""
    google_oauth_refresh_token: str = ""
    google_drive_mirror_folder_id: str = ""
    google_index_sheet_id: str = ""

    # --- Service ---
    ledger_db_path: str = "/data/ledger.db"
    log_level: str = "INFO"
    # Cadence for the incremental reflect (poll_incremental): reflects every page
    # changed since the watermark via Notion /search, deep sub-pages included.
    notion_poll_seconds: int = 120
    google_poll_seconds: int = 120  # legacy (Google->Notion direction removed); unused
    # Full re-crawl runs ONCE A DAY (cron), since incremental now handles all edits;
    # the full crawl only backstops what /search can't report — deletions, orphan
    # pruning, drift healing. Pick a low-activity hour + your timezone.
    full_sync_hour: int = 4
    scheduler_timezone: str = "Europe/London"
    inflight_ttl_seconds: int = 300
    tombstone_grace_seconds: int = 86400
    # Cadence for the command inbox poll (Google Tasks has no push).
    command_poll_seconds: int = 30
    # Shared secret required to trigger POST /admin/full-sync and POST /command.
    admin_api_key: str = ""
    # The Notion webhook is optional; the two poll layers + per-command re-reflect
    # are the required path.
    enable_notion_webhook: bool = False

    # --- Relay to the existing Alistair Skills API (the write path) ---
    relay_api_base_url: str = ""
    relay_api_key: str = ""  # sent as X-API-Key
    # Comma-separated allowlist of API paths the relay may call. Anything else is
    # rejected, so a command task can never reach github/push-file or deletes.
    relay_allowed_paths: str = (
        "/api/notion/create-pages,/api/notion/update-page,/api/notion/create-comment,"
        "/api/intray"
    )
    # Google Tasks list used as the command inbox. "@default" = the user's primary
    # list ("My Tasks"), because Gemini Live can't reliably target a named list.
    # On the shared default list only JSON-shaped tasks are treated as commands, so
    # personal tasks are never touched. Set a title to use a dedicated list instead.
    command_tasklist_name: str = "@default"
    # Path used when a command task carries a bare body (no explicit path).
    relay_default_path: str = "/api/notion/create-pages"

    notion_api_base: str = "https://api.notion.com/v1"
    notion_version: str = "2022-06-28"
    # Some pages (e.g. wiki/newer block types) 400 on the pinned version when
    # listing block children; the read path retries once with this newer version.
    notion_version_fallback: str = "2025-09-03"

    @property
    def notion_roots(self) -> list[str]:
        if self.notion_root_ids.strip():
            return [r.strip() for r in self.notion_root_ids.split(",") if r.strip()]
        return list(DEFAULT_NOTION_ROOTS)

    @property
    def allowed_relay_paths(self) -> list[str]:
        return [p.strip() for p in self.relay_allowed_paths.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
