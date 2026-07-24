"""Optional PostgreSQL integration smoke test.

Every other test in tests/db/ uses SQLite for isolation and speed, which
does NOT prove PostgreSQL-specific compatibility (dialect differences,
asyncpg-specific connection behavior, etc. — see docs/DATABASE.md Section
12). This is the one test that actually exercises PostgreSQL. It is
skipped unless a real, reachable instance is provided, and it is
intentionally not a prerequisite for STORY-002 completion.

To run it locally:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_postgres_integration.py
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.health import check_database_connection

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)


async def test_postgres_connection_smoke_test() -> None:
    assert _POSTGRES_TEST_URL is not None  # narrows type; guarded by skipif above
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_pre_ping=True)
    try:
        assert await check_database_connection(engine) is True
    finally:
        await engine.dispose()
