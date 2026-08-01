"""Rate limiting (Sprint 2): `POST /auth/token` and `POST .../agent/execute`
actually return 429 once their configured limit (`RATE_LIMIT_AUTH_TOKEN`/
`RATE_LIMIT_AGENT_EXECUTE` — see `app.core.config.Settings`) is exceeded
within a single test, against the REAL default limits — no settings
override needed, since `tests.conftest._reset_rate_limiter` (autouse)
guarantees every test starts with a clean bucket regardless. See
`tests/core/test_rate_limit.py` for the settings-wiring-only tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.coordinator_decisions import HandoffDecision, TargetAgent
from app.ai.decisions import DecisionKind, SafeResponseDecision
from app.ai.providers.base import LLMProvider
from app.ai.providers.factory import get_llm_provider
from app.ai.providers.fake_provider import FakeLLMProvider
from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]

_PASSWORD = "Synthetic-Test-Password-123!"
_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- POST /auth/token --------------------------------------------------------


async def test_auth_token_returns_429_once_the_limit_is_exceeded(
    client_with_db: AsyncClient,
) -> None:
    """Default `RATE_LIMIT_AUTH_TOKEN` is `"5/minute"` — the 6th rapid
    call in this test must be rejected, the first 5 must behave exactly
    as they would unrated (401 for a bad password — this test never
    creates a real user, so every call is a genuine credential
    rejection, not just "blocked by the limiter early")."""
    responses = [
        await client_with_db.post(
            "/api/v1/auth/token",
            json={"email": "synthetic.nonexistent@example.com", "password": _PASSWORD},
        )
        for _ in range(6)
    ]

    assert [r.status_code for r in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    body = responses[5].json()
    assert body["error"]["code"] == "rate_limited"


async def test_auth_token_rate_limit_does_not_block_a_single_normal_login(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    """Regression guard for the fix itself: one ordinary login must never
    be affected by rate limiting existing at all."""
    user = await make_user("rate-limit-normal-login", password=_PASSWORD)

    response = await client_with_db.post(
        "/api/v1/auth/token", json={"email": user.email, "password": _PASSWORD}
    )

    assert response.status_code == 200


# --- POST .../agent/execute --------------------------------------------------


def _agent_url(organization: Organization) -> str:
    return f"/api/v1/organizations/{organization.id}/agent/execute"


@asynccontextmanager
async def _client_with_agent(
    app: FastAPI, db_session: AsyncSession, provider: LLMProvider
) -> AsyncIterator[AsyncClient]:
    """Same shape as `tests.api.test_agent_endpoints._client_with_agent`
    — overrides both the DB session and the LLM provider, never a real
    Anthropic/Groq call."""

    async def _db_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_llm_provider] = lambda: provider
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_agent_execute_returns_429_once_the_limit_is_exceeded(
    app: FastAPI,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """Default `RATE_LIMIT_AGENT_EXECUTE` is `"20/minute"` — the 21st
    rapid call in this test must be rejected; the first 20 must complete
    as genuine (fake-LLM-backed) orchestration runs, not just "blocked
    early"."""
    organization = await make_organization("rate-limit-agent")
    user = await make_user("rate-limit-agent")
    await make_membership(organization, user, role=Role.ADMIN)
    token = create_access_token(user.id, get_settings())
    headers = {"Authorization": f"Bearer {token}"}

    provider = FakeLLMProvider(
        coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
        decision=SafeResponseDecision(
            kind=DecisionKind.SAFE_RESPONSE, message="Here is your summary."
        ),
    )

    async with _client_with_agent(app, db_session, provider) as client:
        responses = [
            await client.post(
                _agent_url(organization),
                json={"request_type": "administrative_routing", "request_text": "hello"},
                headers=headers,
            )
            for _ in range(21)
        ]

    assert all(r.status_code == 201 for r in responses[:20])
    assert responses[20].status_code == 429
    body = responses[20].json()
    assert body["error"]["code"] == "rate_limited"


async def test_agent_execute_rate_limit_does_not_block_a_single_normal_call(
    app: FastAPI,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    organization = await make_organization("rate-limit-agent-normal")
    user = await make_user("rate-limit-agent-normal")
    await make_membership(organization, user, role=Role.ADMIN)
    token = create_access_token(user.id, get_settings())
    headers = {"Authorization": f"Bearer {token}"}

    provider = FakeLLMProvider(
        coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
        decision=SafeResponseDecision(
            kind=DecisionKind.SAFE_RESPONSE, message="Here is your summary."
        ),
    )

    async with _client_with_agent(app, db_session, provider) as client:
        response = await client.post(
            _agent_url(organization),
            json={"request_type": "administrative_routing", "request_text": "hello"},
            headers=headers,
        )

    assert response.status_code == 201
