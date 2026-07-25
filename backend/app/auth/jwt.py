"""Stateless JWT access-token creation and decoding.

Never implement JWT cryptographic primitives by hand — this module is a
thin wrapper around `PyJWT`.

Trust boundary (see docs/RBAC.md): a token identifies WHO the user is
(`sub`) and nothing else. It carries no organization membership, no role,
no password hash, and no patient/medical data. Organization membership
and role are always re-resolved from the database on every request (see
`app.auth.dependencies.get_current_membership`) — a token is never
treated as an authoritative source for either.

Token revocation limitation: tokens are stateless — there is no
server-side session/revocation store in this story. `jti` (a random,
per-token identifier) is included specifically so a future
revocation/session-control mechanism has something to key off of;
nothing currently checks it. Short-lived access tokens
(`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, default 30) are the only mitigation
for a leaked token today — see docs/RBAC.md "Token Revocation
Limitation" for the full picture and what logout does and does not do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from pydantic import BaseModel, ValidationError

from app.core.config import Settings

_REQUIRED_CLAIMS = ["sub", "exp", "iat", "jti"]


class TokenClaims(BaseModel):
    """The minimal, validated set of claims this application issues and trusts.

    `sub` is the user's UUID as a string. `exp`/`iat` are raw Unix
    timestamps (seconds) — kept as `int`, not converted to `datetime`,
    since nothing in this codebase needs them as anything but a
    validity check (performed by `pyjwt.decode` itself).
    """

    sub: str
    exp: int
    iat: int
    jti: str


class InvalidTokenError(Exception):
    """Raised for any token that fails to decode or validate: malformed,
    expired, wrong signature, or missing a required claim. Callers (see
    `app.auth.dependencies`) map this to HTTP 401 — never anything more
    specific, so a client cannot distinguish "expired" from "forged" from
    the error alone."""


def _require_secret(settings: Settings) -> str:
    if settings.jwt_secret_key is None:
        raise RuntimeError("JWT_SECRET_KEY is not configured; cannot create/decode tokens.")
    return settings.jwt_secret_key.get_secret_value()


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    """Create a short-lived access token identifying `user_id`.

    Contains only `sub`/`exp`/`iat`/`jti` — no email, no role, no
    membership, no patient/medical data. See module docstring.
    """
    secret = _require_secret(settings)
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    """Decode and validate `token`. Raises `InvalidTokenError` for any
    failure — malformed, expired, wrong signature, or a missing claim.
    Never raises any other exception type."""
    secret = _require_secret(settings)
    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": _REQUIRED_CLAIMS},
        )
        return TokenClaims.model_validate(payload)
    except (pyjwt.PyJWTError, ValidationError) as exc:
        raise InvalidTokenError("Invalid or expired token.") from exc
