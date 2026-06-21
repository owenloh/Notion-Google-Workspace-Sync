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
DEFAULT_NOTION_ROOTS: tuple[str, ...] = (
    "54816fca-6f6c-4588-8c1a-1cdfcc6c9092",  # Areas of Focus (data source)
    "f0ea8841-ca74-47b7-a28a-0b367bca8c41",  # Projects (data source)
    "1d3eb1dd-2803-4692-a4d5-6ca9709ae570",  # Actions (data source)
    "34f6f0cc-dd76-801d-b0ec-de6c10685d10",  # Mission Control (page → Briefing)
    "1fa6f0cc-dd76-809e-8bcb-e5db5ae28237",  # Library (page → References tray)
)

# Notion data-source ids for the relational spine, so engines can classify a
# page by which database it belongs to.
AREAS_DS_ID = DEFAULT_NOTION_ROOTS[0]
PROJECTS_DS_ID = DEFAULT_NOTION_ROOTS[1]
ACTIONS_DS_ID = DEFAULT_NOTION_ROOTS[2]


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
    notion_poll_seconds: int = 180
    google_poll_seconds: int = 120
    inflight_ttl_seconds: int = 300
    tombstone_grace_seconds: int = 86400

    notion_api_base: str = "https://api.notion.com/v1"
    notion_version: str = "2022-06-28"

    @property
    def notion_roots(self) -> list[str]:
        if self.notion_root_ids.strip():
            return [r.strip() for r in self.notion_root_ids.split(",") if r.strip()]
        return list(DEFAULT_NOTION_ROOTS)


@lru_cache
def get_settings() -> Settings:
    return Settings()
