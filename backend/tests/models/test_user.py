"""User model tests against real PostgreSQL.

See tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL
and how test data is guaranteed not to persist. All emails are obviously
synthetic (`@example.com` — RFC 2606 reserved).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.models.user import User


async def test_user_id_is_generated_uuid(db_session: AsyncSession) -> None:
    user = User(email="synthetic.uuid.test@example.com", password_hash=hash_password("x"))
    db_session.add(user)
    await db_session.flush()

    assert isinstance(user.id, uuid.UUID)


async def test_user_requires_email(db_session: AsyncSession) -> None:
    user = User(password_hash=hash_password("x"))
    db_session.add(user)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_requires_password_hash(db_session: AsyncSession) -> None:
    user = User(email="synthetic.no.password@example.com")
    db_session.add(user)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


def test_user_email_is_normalized_on_assignment() -> None:
    # Pure application-level behavior — no database needed.
    user = User(
        email="  Synthetic.Mixed.Case@Example.COM  ", password_hash=hash_password("x")
    )
    assert user.email == "synthetic.mixed.case@example.com"


async def test_user_email_must_be_unique(db_session: AsyncSession) -> None:
    email = "synthetic.duplicate.email@example.com"
    user1 = User(email=email, password_hash=hash_password("x"))
    db_session.add(user1)
    await db_session.flush()

    user2 = User(email=email, password_hash=hash_password("y"))
    db_session.add(user2)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_email_uniqueness_applies_after_normalization(db_session: AsyncSession) -> None:
    # Different casing/whitespace, same normalized email — must still
    # collide, since normalization is applied before the unique
    # constraint is checked.
    user1 = User(
        email="synthetic.normalized.dup@example.com", password_hash=hash_password("x")
    )
    db_session.add(user1)
    await db_session.flush()

    user2 = User(
        email="  SYNTHETIC.NORMALIZED.DUP@EXAMPLE.COM  ", password_hash=hash_password("y")
    )
    db_session.add(user2)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_password_hash_persists_and_is_not_plaintext(db_session: AsyncSession) -> None:
    plaintext = "Synthetic-Test-Password-123!"
    hashed = hash_password(plaintext)
    user = User(email="synthetic.password.persist@example.com", password_hash=hashed)
    db_session.add(user)
    await db_session.flush()
    user_id = user.id  # capture before expiring: expire() expires `id` too
    db_session.expire(user)

    reloaded = await db_session.get(User, user_id)
    assert reloaded is not None
    assert reloaded.password_hash == hashed
    assert reloaded.password_hash != plaintext
    assert plaintext not in reloaded.password_hash


async def test_user_is_active_defaults_true(db_session: AsyncSession) -> None:
    user = User(email="synthetic.active.default@example.com", password_hash=hash_password("x"))
    db_session.add(user)
    await db_session.flush()

    assert user.is_active is True


async def test_user_timestamps_are_set_and_timezone_aware(db_session: AsyncSession) -> None:
    user = User(email="synthetic.timestamps@example.com", password_hash=hash_password("x"))
    db_session.add(user)
    await db_session.flush()

    assert user.created_at is not None
    assert user.updated_at is not None
    assert user.created_at.tzinfo is not None
    assert user.updated_at.tzinfo is not None
