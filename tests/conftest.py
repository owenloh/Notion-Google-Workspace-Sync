"""Shared test fixtures: an isolated in-memory ledger and default settings."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings
from app.ledger import db as ledger_db


@pytest.fixture
def session():
    """A fresh in-memory SQLite ledger per test (shared connection)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, echo=False
    )
    SQLModel.metadata.create_all(engine)
    # Point the module-level engine at this one so repo helpers using
    # session_scope() (if any) stay consistent within the test.
    ledger_db._engine = engine
    with Session(engine) as s:
        yield s
    ledger_db.reset_engine()


@pytest.fixture
def settings():
    return Settings(
        sync_bot_notion_user_id="bot-user-123",
        inflight_ttl_seconds=300,
        tombstone_grace_seconds=86400,
    )
