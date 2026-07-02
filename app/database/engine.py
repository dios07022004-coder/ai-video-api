"""Async engine + session management.

`Database` wraps a single ``AsyncEngine``/sessionmaker. It is provider-agnostic:
pass any SQLAlchemy async URL. SQLite gets pragmatic defaults (WAL, foreign keys)
applied per-connection so the local dev experience matches production semantics.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import Settings, get_settings
from app.database.base import Base


class Database:
    """Owns the engine and session factory; provides scoped sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._url = url
        self._is_sqlite = url.startswith("sqlite")
        connect_args: dict = {}
        if self._is_sqlite:
            # allow use across the worker's thread pool / avoid file-lock stalls
            connect_args = {"timeout": 30}
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            future=True,
            pool_pre_ping=not self._is_sqlite,
            connect_args=connect_args,
        )
        if self._is_sqlite:
            self._install_sqlite_pragmas()
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    def _install_sqlite_pragmas(self) -> None:
        @event.listens_for(self._engine.sync_engine, "connect")
        def _set_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def create_all(self) -> None:
        """Create tables from ORM metadata (dev/bootstrap; use Alembic in prod)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def healthcheck(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def session(self) -> AsyncSession:
        """Return a new session (caller manages commit/close)."""
        return self._sessionmaker()

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[AsyncSession]:
        """Transactional scope: commit on success, rollback on error, always close."""
        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def dispose(self) -> None:
        await self._engine.dispose()


@lru_cache(maxsize=1)
def get_database(settings: Settings | None = None) -> Database:
    """Process-wide Database singleton (shared by API and workers)."""
    settings = settings or get_settings()
    return Database(settings.database_url, echo=settings.debug and settings.env == "dev")


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    db = get_database()
    async with db.scope() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Standalone transactional scope for workers / scripts."""
    db = get_database()
    async with db.scope() as session:
        yield session
