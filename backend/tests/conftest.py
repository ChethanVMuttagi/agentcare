"""Root test fixtures, shared across every test package.

`db_session` and the `make_*` factory fixtures require
`AGENTCARE_TEST_POSTGRES_URL` (a real, reachable PostgreSQL instance with
migrations applied) and are skipped otherwise — see the `db_session`
fixture. Each test runs inside an outer connection-level transaction that
is always rolled back afterward (SQLAlchemy's
`join_transaction_mode="create_savepoint"` pattern), so no synthetic test
data is ever actually persisted. Only obviously-synthetic data
(`@example.com` emails, `synthetic-*` slugs) is ever used.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.db.session import get_db_session
from app.main import create_app
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.user import User


@pytest.fixture()
def app() -> FastAPI:
    return create_app()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    if not _POSTGRES_TEST_URL:
        pytest.skip(
            "Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance "
            "(with `alembic upgrade head` applied) to run tests that need a database."
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
async def client_with_db(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An async client whose `get_db_session` dependency is overridden to
    use the same rolled-back-afterward `db_session`, so data created via
    `make_user`/`make_organization`/etc. in a test is visible to the
    request the client makes, and nothing persists afterward.

    Deliberately `httpx.AsyncClient` + `ASGITransport`, NOT
    `starlette.testclient.TestClient`: `TestClient` dispatches requests
    through its own background thread/event loop (an anyio "portal"),
    which breaks `db_session`'s asyncpg connection — asyncpg connections
    are bound to the event loop that created them and cannot be used from
    a different one ("Future attached to a different loop"). `AsyncClient`
    with `ASGITransport` runs the request in the *same* event loop as the
    calling test, so the one `db_session` connection is used consistently
    end-to-end.
    """

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


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


@pytest.fixture()
def make_user(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[User]]:
    """Factory for a synthetic, flushed (never committed) User.

    Uses the `example.com` domain (RFC 2606: reserved specifically for
    documentation/examples, guaranteed never to be a real, registered
    mail-receiving domain) so these addresses can never collide with a
    real one. `.invalid` (also RFC 2606) would be even more obviously
    fake, but pydantic's `EmailStr` (via `email-validator`) rejects it
    outright as a "special-use or reserved name" — `example.com` is the
    practical choice that both libraries accept. Uses a default synthetic
    password distinct from any real credential.
    """

    async def _make(
        email_suffix: str,
        password: str = "Synthetic-Test-Password-123!",
        is_active: bool = True,
    ) -> User:
        user = User(
            email=f"synthetic.test.user.{email_suffix}@example.com",
            password_hash=hash_password(password),
            is_active=is_active,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _make


@pytest.fixture()
def make_membership(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[OrganizationMembership]]:
    """Factory for a synthetic, flushed (never committed) OrganizationMembership."""

    async def _make(
        organization: Organization,
        user: User,
        role: Role = Role.STAFF,
        is_active: bool = True,
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=role,
            is_active=is_active,
        )
        db_session.add(membership)
        await db_session.flush()
        return membership

    return _make
