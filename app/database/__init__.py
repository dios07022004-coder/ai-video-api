"""Async database layer (SQLAlchemy 2.0). SQLite now, PostgreSQL later — same API."""

from app.database.engine import (
    Database,
    get_database,
    get_session,
    session_scope,
)

__all__ = ["Database", "get_database", "get_session", "session_scope"]
