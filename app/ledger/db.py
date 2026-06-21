"""Ledger database engine and session management."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.ledger import models  # noqa: F401  (ensure tables are registered)

_engine = None


def init_engine(db_path: str):
    """Create (or return) the SQLite engine and ensure tables exist.

    ``:memory:`` is honored for tests; otherwise the parent directory is created
    so a fresh Railway volume works on first boot.
    """
    global _engine
    if _engine is not None:
        return _engine

    if db_path != ":memory:":
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        url = f"sqlite:///{db_path}"
    else:
        url = "sqlite://"

    _engine = create_engine(
        url,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(_engine)
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("Ledger engine not initialized; call init_engine() first")
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests to isolate state)."""
    global _engine
    _engine = None


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
