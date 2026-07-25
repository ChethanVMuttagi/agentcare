"""Tests for app.auth.service.authenticate_user against real PostgreSQL.

See tests/conftest.py for `db_session`/`make_user` and why real
PostgreSQL is required. All emails/passwords are obviously synthetic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import authenticate_user
from app.models.user import User

_PASSWORD = "Synthetic-Test-Password-123!"


async def test_authenticate_user_succeeds_with_correct_credentials(
    db_session: AsyncSession, make_user: Callable[..., Awaitable[User]]
) -> None:
    user = await make_user("auth-success", password=_PASSWORD)

    result = await authenticate_user(db_session, user.email, _PASSWORD)

    assert result is not None
    assert result.id == user.id


async def test_authenticate_user_login_email_is_normalized(
    db_session: AsyncSession, make_user: Callable[..., Awaitable[User]]
) -> None:
    user = await make_user("auth-normalize", password=_PASSWORD)
    # Submit the login email with different case/whitespace than stored —
    # normalization must make this match anyway.
    shouted_email = f"  {user.email.upper()}  "

    result = await authenticate_user(db_session, shouted_email, _PASSWORD)

    assert result is not None
    assert result.id == user.id


async def test_authenticate_user_rejects_unknown_email(db_session: AsyncSession) -> None:
    result = await authenticate_user(
        db_session, "synthetic.nonexistent.user@example.com", _PASSWORD
    )
    assert result is None


async def test_authenticate_user_rejects_wrong_password(
    db_session: AsyncSession, make_user: Callable[..., Awaitable[User]]
) -> None:
    user = await make_user("auth-wrong-password", password=_PASSWORD)

    result = await authenticate_user(db_session, user.email, "a-completely-wrong-password")

    assert result is None


async def test_authenticate_user_rejects_inactive_user(
    db_session: AsyncSession, make_user: Callable[..., Awaitable[User]]
) -> None:
    user = await make_user("auth-inactive", password=_PASSWORD, is_active=False)

    result = await authenticate_user(db_session, user.email, _PASSWORD)

    assert result is None
