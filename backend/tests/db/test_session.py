"""Tests for engine/session lifecycle (app.db.session).

Cache clearing between tests is handled by the autouse fixture in
tests/db/conftest.py. A SQLite (aiosqlite) URL is used to exercise real
session creation/closing without needing a live PostgreSQL instance —
see docs/DATABASE.md Section 12 for why this doesn't prove
PostgreSQL-specific compatibility.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import (
    build_engine,
    dispose_engine,
    get_db_session,
    get_engine,
    get_sessionmaker,
)


def test_get_engine_raises_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_engine()


def test_get_engine_returns_engine_for_configured_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    engine = get_engine()
    assert isinstance(engine, AsyncEngine)


def test_engine_creation_does_not_connect_eagerly() -> None:
    # 192.0.2.1 is a non-routable documentation address (RFC 5737).
    # Construction must not raise or block: SQLAlchemy engines connect
    # lazily on first use, never at creation time.
    engine = build_engine("postgresql+asyncpg://user:pw@192.0.2.1:5432/agentcare")
    assert isinstance(engine, AsyncEngine)


def test_get_sessionmaker_returns_async_sessionmaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    factory = get_sessionmaker()
    assert isinstance(factory, async_sessionmaker)


async def test_get_db_session_yields_a_working_session_then_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    generator = get_db_session()

    session = await generator.__anext__()
    assert isinstance(session, AsyncSession)
    assert session.is_active

    # Draining the generator runs the `async with` block's exit, closing
    # the session — no exception should be swallowed here since none was
    # raised inside the `with` block.
    with pytest.raises(StopAsyncIteration):
        await generator.__anext__()


async def test_dispose_engine_without_any_configured_database_is_a_no_op() -> None:
    # No DATABASE_URL set (default test environment): disposing must not
    # create an engine merely to tear it down.
    await dispose_engine()
    assert get_engine.cache_info().currsize == 0


async def test_dispose_engine_clears_cache_after_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    get_engine()
    assert get_engine.cache_info().currsize == 1

    await dispose_engine()
    assert get_engine.cache_info().currsize == 0
