"""Tests for the database connectivity check itself (app.db.health).

Uses a real SQLite (aiosqlite) engine to exercise actual SQLAlchemy
execution against a real, if different, database — this is not a mock of
`check_database_connection`. SQLite is used ONLY for isolated automated
testing; PostgreSQL remains the production system of record and does not
share SQLite's dialect/behavior in general. See docs/DATABASE.md Section 12.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from app.db.health import check_database_connection


async def test_check_database_connection_succeeds_against_a_reachable_database() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        assert await check_database_connection(engine) is True
    finally:
        await engine.dispose()


async def test_check_database_connection_fails_against_an_unreachable_database() -> None:
    # 192.0.2.1 is a non-routable documentation address (RFC 5737); the
    # connection attempt fails rather than hanging. A short connect
    # timeout keeps this test fast and deterministic either way.
    engine = create_async_engine(
        "postgresql+asyncpg://user:pw@192.0.2.1:5432/agentcare",
        connect_args={"timeout": 2},
    )
    try:
        assert await check_database_connection(engine) is False
    finally:
        await engine.dispose()
