"""FastAPI dependencies for authentication and tenant-authorized access.

Trust boundary (see docs/RBAC.md): a JWT only ever identifies WHO the
user is (`sub`). It is NEVER trusted for organization membership or
role — those are always re-resolved from the database, fresh, on every
request via `get_current_membership`. A token cannot carry a stale or
forged role/membership because it never carries one at all.

401 vs 403 (see docs/RBAC.md): missing/invalid/expired credentials, or an
inactive user, are 401 (`AuthenticationError`) — authentication itself
failed. A recognized, active user who lacks access to the requested
organization or role is 403 (`AuthorizationError`) — authenticated, but
not authorized. Both "no membership at all" and "membership exists but
wrong role" resolve to the same 403 shape, so a caller cannot use the
response to distinguish "you're not a member" from "you're a member with
the wrong role" from "this organization doesn't exist" — see
`get_current_membership` and `require_roles` below.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import InvalidTokenError, decode_access_token
from app.core.config import Settings, get_settings
from app.core.exceptions import AppException
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User


class AuthenticationError(AppException):
    """401: authentication is missing, invalid, or the user is inactive."""

    status_code = 401
    error_code = "authentication_required"


class AuthorizationError(AppException):
    """403: authenticated, but not authorized for the requested organization/role."""

    status_code = 403
    error_code = "not_authorized"


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AuthenticationError("Missing or invalid Authorization header.")
    return token


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the authenticated `User` from a `Bearer` access token.

    Raises `AuthenticationError` (401) for: a missing/malformed
    Authorization header, an invalid/expired/wrong-signature token, a
    token whose `sub` isn't a real user id, or an inactive user — all
    with the same generic message, so a caller cannot distinguish which
    case applies.
    """
    token = _extract_bearer_token(authorization)

    try:
        claims = decode_access_token(token, settings)
    except InvalidTokenError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    try:
        user_id = uuid.UUID(claims.sub)
    except ValueError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired token.")

    return user


async def get_current_membership(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> OrganizationMembership:
    """Resolve `current_user`'s ACTIVE membership in `organization_id`.

    `organization_id` is a plain `uuid.UUID` parameter (not itself a
    `Depends`) so FastAPI binds it automatically to a route's
    `{organization_id}` path parameter by name once a real
    organization-scoped route exists; it can also be supplied directly
    when calling this function outside of FastAPI's request handling
    (e.g. in tests).

    Role and membership status are read fresh from the database on every
    call — never trusted from the token. Raises `AuthorizationError`
    (403), not 404, whether the membership is simply missing, inactive,
    or the organization itself doesn't exist — a caller cannot
    distinguish these from the response, which avoids disclosing
    cross-tenant organization existence to a non-member (see
    docs/RBAC.md).
    """
    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == current_user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None or not membership.is_active:
        raise AuthorizationError("You do not have access to this organization.")
    return membership


def require_roles(
    *allowed_roles: Role,
) -> Callable[[OrganizationMembership], Awaitable[OrganizationMembership]]:
    """Dependency factory: require the current membership's role to be one
    of `allowed_roles`. Depends on (and therefore also enforces)
    `get_current_membership` — an inactive/missing membership is rejected
    before the role is even checked.

    Usage (once a real organization-scoped route exists):
        Depends(require_roles(Role.ADMIN))
        Depends(require_roles(Role.ADMIN, Role.STAFF))
    """

    async def _dependency(
        membership: Annotated[OrganizationMembership, Depends(get_current_membership)],
    ) -> OrganizationMembership:
        if membership.role not in allowed_roles:
            raise AuthorizationError("You do not have permission to perform this action.")
        return membership

    return _dependency
