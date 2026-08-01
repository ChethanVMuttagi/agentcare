"""Concurrency test (Sprint 3) for the rate limiter's own correctness
under genuinely concurrent access.

`tests/api/test_rate_limiting.py` (Sprint 2) fires its requests
SEQUENTIALLY — one `await` at a time — which can prove the limiter
rejects the (N+1)th call, but cannot catch a check-then-increment race
in the limiter's own bookkeeping. This fires many requests at once via
`asyncio.gather` (genuine concurrent asyncio scheduling, with real
interleaving at every `await` point inside request processing — auth,
DB, etc.) and confirms the total that get past the limiter never
exceeds the configured bound.

Deliberately does NOT use `tests.conftest.client_with_db` — that
fixture overrides `get_db_session` to share ONE `db_session` connection
across the whole test (see its docstring), and a single asyncpg
connection cannot serve multiple genuinely concurrent queries at once
("another operation is in progress"). This test instead points the
REAL app at a real, pool-backed engine (mirroring
`tests/db/test_appointment_concurrency.py`'s "dedicated real
concurrency" pattern) so each concurrent request gets its own pooled
connection, the same way it would in production. No real user is
needed — every request deliberately uses wrong credentials, so a 401
(not 429) is the "got past the limiter" signal.

Scoped to single-process concurrency, matching `app.core.rate_limit`'s
own documented scope (in-memory storage, one process, no shared cache
— see that module's docstring); this does not prove multi-process
atomicity, which would need a shared backend (e.g. Redis) this codebase
doesn't have.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/api/test_rate_limiting_concurrency.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.session import dispose_engine, get_engine, get_sessionmaker
from app.main import create_app

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)

_PASSWORD = "Synthetic-Test-Password-123!"
_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", _POSTGRES_TEST_URL or "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
async def real_engine_client() -> AsyncIterator[AsyncClient]:
    """The real app, with its real (pool-backed, uncached-between-tests)
    engine — NOT the shared-session `client_with_db` fixture. Disposed
    afterward so this test's engine/pool never leaks into a later test."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    app: FastAPI = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    await dispose_engine()


async def test_concurrent_auth_token_requests_never_exceed_the_configured_limit(
    real_engine_client: AsyncClient,
) -> None:
    """Default `RATE_LIMIT_AUTH_TOKEN` is `"5/minute"`. Fire 15 requests
    at once and confirm at most 5 ever reach the real credential check
    (401 — bad password, meaning they got past the limiter) and the
    rest are rejected (429) — a check-then-increment race would let
    more than 5 through. `tests.conftest`'s autouse `_reset_rate_limiter`
    fixture already guarantees a clean bucket before this test starts."""
    responses = await asyncio.gather(
        *[
            real_engine_client.post(
                "/api/v1/auth/token",
                json={
                    "email": "synthetic.rate-limit-concurrency@example.com",
                    "password": _PASSWORD,
                },
            )
            for _ in range(15)
        ]
    )

    statuses = [response.status_code for response in responses]
    assert set(statuses) <= {401, 429}
    assert statuses.count(401) <= 5
    assert statuses.count(429) >= 10
    assert statuses.count(401) + statuses.count(429) == 15
