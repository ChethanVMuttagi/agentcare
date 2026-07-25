"""Tests for app.auth.dependencies: authentication and tenant-authorized access.

Exercises the dependency functions directly (as plain async functions),
not through a real FastAPI route — no organization-scoped route exists
yet to wire `get_current_membership`/`require_roles` into (see
docs/RBAC.md). This mirrors how `app.db.session.get_db_session` was
introduced and tested in STORY-002 before any route consumed it.

Real PostgreSQL required — see tests/conftest.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    AuthenticationError,
    AuthorizationError,
    get_current_membership,
    get_current_user,
    require_roles,
)
from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]


# --- get_current_user -----------------------------------------------------


async def test_get_current_user_resolves_valid_token(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user("dep-valid-token")
    settings = get_settings()
    token = create_access_token(user.id, settings)

    resolved = await get_current_user(
        settings=settings, session=db_session, authorization=f"Bearer {token}"
    )

    assert resolved.id == user.id


async def test_get_current_user_rejects_missing_header(db_session: AsyncSession) -> None:
    settings = get_settings()
    with pytest.raises(AuthenticationError):
        await get_current_user(settings=settings, session=db_session, authorization=None)


async def test_get_current_user_rejects_malformed_header(db_session: AsyncSession) -> None:
    settings = get_settings()
    with pytest.raises(AuthenticationError):
        await get_current_user(
            settings=settings, session=db_session, authorization="not-a-bearer-header"
        )


async def test_get_current_user_rejects_malformed_token(db_session: AsyncSession) -> None:
    settings = get_settings()
    with pytest.raises(AuthenticationError):
        await get_current_user(
            settings=settings, session=db_session, authorization="Bearer garbage-token"
        )


async def test_get_current_user_rejects_token_for_unknown_user(db_session: AsyncSession) -> None:
    settings = get_settings()
    token = create_access_token(uuid.uuid4(), settings)  # no such user persisted
    with pytest.raises(AuthenticationError):
        await get_current_user(
            settings=settings, session=db_session, authorization=f"Bearer {token}"
        )


async def test_get_current_user_rejects_inactive_user(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user("dep-inactive-user", is_active=False)
    settings = get_settings()
    token = create_access_token(user.id, settings)

    with pytest.raises(AuthenticationError):
        await get_current_user(
            settings=settings, session=db_session, authorization=f"Bearer {token}"
        )


# --- get_current_membership (tenant context) ------------------------------


async def test_get_current_membership_resolves_active_membership(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("dep-membership")
    user = await make_user("dep-membership-user")
    await make_membership(org, user, role=Role.ADMIN)

    membership = await get_current_membership(
        organization_id=org.id, current_user=user, session=db_session
    )

    assert membership.organization_id == org.id
    assert membership.user_id == user.id
    assert membership.role is Role.ADMIN


async def test_get_current_membership_rejects_no_membership(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
) -> None:
    org = await make_organization("dep-no-membership")
    user = await make_user("dep-no-membership-user")

    with pytest.raises(AuthorizationError):
        await get_current_membership(
            organization_id=org.id, current_user=user, session=db_session
        )


async def test_get_current_membership_rejects_inactive_membership(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("dep-inactive-membership")
    user = await make_user("dep-inactive-membership-user")
    await make_membership(org, user, role=Role.STAFF, is_active=False)

    with pytest.raises(AuthorizationError):
        await get_current_membership(
            organization_id=org.id, current_user=user, session=db_session
        )


async def test_cross_organization_membership_isolation(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a = await make_organization("dep-cross-org-a")
    org_b = await make_organization("dep-cross-org-b")
    user = await make_user("dep-cross-org-user")
    await make_membership(org_a, user, role=Role.ADMIN)  # member of org_a ONLY

    membership = await get_current_membership(
        organization_id=org_a.id, current_user=user, session=db_session
    )
    assert membership.organization_id == org_a.id

    # A valid, authenticated user — but not a member of org_b.
    with pytest.raises(AuthorizationError):
        await get_current_membership(
            organization_id=org_b.id, current_user=user, session=db_session
        )


async def test_user_can_belong_to_multiple_organizations(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a = await make_organization("dep-multi-org-a")
    org_b = await make_organization("dep-multi-org-b")
    user = await make_user("dep-multi-org-user")
    await make_membership(org_a, user, role=Role.ADMIN)
    await make_membership(org_b, user, role=Role.STAFF)

    membership_a = await get_current_membership(
        organization_id=org_a.id, current_user=user, session=db_session
    )
    membership_b = await get_current_membership(
        organization_id=org_b.id, current_user=user, session=db_session
    )

    assert membership_a.role is Role.ADMIN
    assert membership_b.role is Role.STAFF


# --- require_roles (RBAC) ---------------------------------------------------


async def test_require_roles_allows_permitted_role(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("dep-require-admin")
    user = await make_user("dep-require-admin-user")
    membership = await make_membership(org, user, role=Role.ADMIN)

    dependency = require_roles(Role.ADMIN, Role.STAFF)
    result = await dependency(membership=membership)

    assert result is membership


async def test_require_roles_allows_staff_when_staff_is_permitted(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("dep-require-staff")
    user = await make_user("dep-require-staff-user")
    membership = await make_membership(org, user, role=Role.STAFF)

    dependency = require_roles(Role.ADMIN, Role.STAFF)
    result = await dependency(membership=membership)

    assert result is membership


async def test_require_roles_denies_role_not_in_the_allowed_set(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("dep-require-deny")
    user = await make_user("dep-require-deny-user")
    membership = await make_membership(org, user, role=Role.PATIENT)

    dependency = require_roles(Role.ADMIN, Role.STAFF)
    with pytest.raises(AuthorizationError):
        await dependency(membership=membership)
