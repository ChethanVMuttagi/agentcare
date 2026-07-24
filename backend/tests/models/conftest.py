"""Shared fixtures for domain model tests against real PostgreSQL.

Organization/Facility constraint behavior (uniqueness, foreign keys, CHECK
constraints, enum persistence, timezone-aware timestamps) is
PostgreSQL-specific and is not proven by SQLite — see docs/DATABASE.md
Section 12. Every test that requests `db_session` is skipped unless
`AGENTCARE_TEST_POSTGRES_URL` points at a real, reachable PostgreSQL
instance with the STORY-003 migration already applied
(`alembic upgrade head`).

Each test runs inside an outer connection-level transaction that is
always rolled back afterward, using SQLAlchemy's
`join_transaction_mode="create_savepoint"` session pattern — so even if
test code calls `session.commit()`, nothing is actually persisted to the
shared development database. Only synthetic, obviously-fake data is ever
used here regardless.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.organization import Organization, OrganizationType

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    if not _POSTGRES_TEST_URL:
        pytest.skip(
            "Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance "
            "(with `alembic upgrade head` applied) to run domain model tests."
        )

    engine = create_async_engine(_POSTGRES_TEST_URL)
    async with engine.connect() as connection:
        await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            yield session
        await connection.rollback()
    await engine.dispose()


@pytest.fixture()
def make_organization(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Organization]]:
    """Factory for a synthetic, flushed (never committed) Organization."""

    async def _make(
        slug_suffix: str,
        organization_type: OrganizationType = OrganizationType.HOSPITAL,
    ) -> Organization:
        org = Organization(
            name=f"Synthetic Test Organization {slug_suffix}",
            slug=f"synthetic-test-org-{slug_suffix}",
            organization_type=organization_type,
        )
        db_session.add(org)
        await db_session.flush()
        return org

    return _make
