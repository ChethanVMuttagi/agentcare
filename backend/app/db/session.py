"""Async SQLAlchemy engine and session management.

Engine creation is lazy: importing this module, or any other application
module, never opens a database connection. The engine is created on first
use and cached for the lifetime of the process — mirroring how
`get_settings()` is cached in `app.core.config`.

Transaction ownership: `get_db_session` creates a session, yields it, and
closes it — it never commits automatically. Callers (future services or
workflows) own the transaction boundary explicitly, so behavior stays
predictable once multi-step workflows are introduced. See docs/DATABASE.md.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def build_engine(database_url: str) -> AsyncEngine:
    """Build a new async engine for `database_url`.

    A plain, uncached constructor — used by `get_engine()` for the
    process-wide engine, and directly by tests that need an isolated
    engine without touching the global cache.
    """
    return create_async_engine(database_url, pool_pre_ping=True)


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call.

    Raises `RuntimeError` if `DATABASE_URL` is not configured. Callers that
    don't require the database (e.g. liveness) must not call this.
    """
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError(
            "DATABASE_URL is not configured; the database engine cannot be created."
        )
    return build_engine(settings.database_url.get_secret_value())


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a request-scoped session, then close it.

    No automatic commit or rollback: an exception raised while the session
    is in use propagates to the caller unmodified. The session is still
    reliably closed via the `async with` block below.
    """
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose of the engine's connection pool, if one was ever created.

    Safe to call even when no engine has been created (e.g. DATABASE_URL
    not configured, or in tests) — it will not create one just to tear it
    down. Intended for use during application shutdown (see the lifespan
    handler in `app.main`).
    """
    if get_engine.cache_info().currsize > 0:
        await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
