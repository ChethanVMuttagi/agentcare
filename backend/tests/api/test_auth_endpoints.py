"""Auth endpoint tests: POST /api/v1/auth/token, GET /api/v1/auth/me.

Uses `client_with_db` (tests/conftest.py) — an `httpx.AsyncClient` whose
database dependency is overridden to the same rolled-back-afterward
`db_session` used by `make_user`, so users created in a test are visible
to the request the client makes, and nothing persists afterward. Real
PostgreSQL required (skipped otherwise — see tests/conftest.py).

`client_with_db` is deliberately an async client, not
`starlette.testclient.TestClient` — see tests/conftest.py for why
(TestClient's background-thread dispatch breaks a shared asyncpg
connection across event loops).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.user import User

MakeUser = Callable[..., Awaitable[User]]

_PASSWORD = "Synthetic-Test-Password-123!"

# 32+ chars so PyJWT doesn't emit its InsecureKeyLengthWarning — still
# obviously synthetic. Scoped to this module only (autouse=True on a
# module-level fixture), since sibling files in tests/api/ (test_health.py
# etc.) don't need a JWT secret configured.
_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- POST /auth/token -------------------------------------------------------


async def test_login_with_valid_credentials_returns_access_token(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("token-valid", password=_PASSWORD)

    response = await client_with_db.post(
        "/api/v1/auth/token", json={"email": user.email, "password": _PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]


async def test_login_with_unknown_email_is_rejected_generically(
    client_with_db: AsyncClient,
) -> None:
    response = await client_with_db.post(
        "/api/v1/auth/token",
        json={"email": "synthetic.nonexistent@example.com", "password": _PASSWORD},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_credentials"


async def test_login_with_wrong_password_is_rejected_generically(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("token-wrong-password", password=_PASSWORD)

    response = await client_with_db.post(
        "/api/v1/auth/token", json={"email": user.email, "password": "wrong-password-entirely"}
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "invalid_credentials"


async def test_unknown_email_and_wrong_password_responses_are_identical_in_shape(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("token-shape-compare", password=_PASSWORD)

    unknown_email_response = await client_with_db.post(
        "/api/v1/auth/token",
        json={"email": "synthetic.nobody@example.com", "password": _PASSWORD},
    )
    wrong_password_response = await client_with_db.post(
        "/api/v1/auth/token", json={"email": user.email, "password": "definitely-wrong"}
    )

    assert unknown_email_response.status_code == wrong_password_response.status_code == 401
    assert unknown_email_response.json() == wrong_password_response.json()


async def test_login_with_inactive_user_is_rejected(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("token-inactive", password=_PASSWORD, is_active=False)

    response = await client_with_db.post(
        "/api/v1/auth/token", json={"email": user.email, "password": _PASSWORD}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


# --- GET /auth/me ------------------------------------------------------------


async def test_me_with_valid_token_returns_safe_user_profile(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("me-valid")
    settings = get_settings()
    token = create_access_token(user.id, settings)

    response = await client_with_db.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.id)
    assert body["email"] == user.email
    assert body["is_active"] is True
    assert "password_hash" not in body
    assert "password" not in body


async def test_me_without_token_is_rejected(client_with_db: AsyncClient) -> None:
    response = await client_with_db.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_with_malformed_token_is_rejected(client_with_db: AsyncClient) -> None:
    response = await client_with_db.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer this-is-not-a-real-jwt"}
    )
    assert response.status_code == 401


async def test_me_with_malformed_authorization_header_is_rejected(
    client_with_db: AsyncClient,
) -> None:
    response = await client_with_db.get(
        "/api/v1/auth/me", headers={"Authorization": "not-bearer-at-all"}
    )
    assert response.status_code == 401


async def test_me_with_token_for_inactive_user_is_rejected(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("me-inactive", is_active=False)
    settings = get_settings()
    token = create_access_token(user.id, settings)

    response = await client_with_db.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


async def test_password_hash_never_appears_anywhere_in_the_response(
    client_with_db: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("me-no-hash-leak", password=_PASSWORD)
    settings = get_settings()
    token = create_access_token(user.id, settings)

    response = await client_with_db.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert user.password_hash not in response.text
