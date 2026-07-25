"""Authentication service: verifies credentials against persisted Users.

Deliberately thin — password hashing/verification lives in
`app.auth.security`, JWT creation/decoding in `app.auth.jwt`. This
module's only job is wiring "email + password" to "a User, or not."
"""

from __future__ import annotations

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import verify_password
from app.models.user import User, normalize_email

# A password hash for a value nobody will ever submit — verified against
# when no matching user is found, so a nonexistent email takes roughly as
# long to reject as a real one with a wrong password. Reduces (does not
# eliminate) user-enumeration via a response-time side channel. Computed
# once, at import time, not derived from any real credential.
_DUMMY_PASSWORD_HASH = PasswordHasher().hash("agentcare-timing-safety-dummy-password")


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User | None:
    """Return the matching, active `User` if `email`/`password` are correct.

    Returns `None` — uniformly, with no distinguishing signal — for: no
    user with that email, a wrong password, or an inactive user. Callers
    (see `app.api.v1.endpoints.auth`) must turn a `None` result into one
    single, generic "invalid credentials" response; this function
    deliberately gives them no way to tell the three cases apart.
    """
    normalized = normalize_email(email)
    result = await session.execute(select(User).where(User.email == normalized))
    user = result.scalar_one_or_none()

    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None

    if not verify_password(password, user.password_hash):
        return None

    if not user.is_active:
        return None

    return user
