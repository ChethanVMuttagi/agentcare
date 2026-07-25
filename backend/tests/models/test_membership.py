"""OrganizationMembership model tests against real PostgreSQL.

See tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL
and how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]


async def test_membership_id_is_generated_uuid(
    db_session: AsyncSession, make_organization: MakeOrganization, make_user: MakeUser
) -> None:
    org = await make_organization("mem-uuid")
    user = await make_user("mem-uuid")

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.STAFF)
    db_session.add(membership)
    await db_session.flush()

    assert isinstance(membership.id, uuid.UUID)


async def test_membership_requires_a_valid_organization(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user("mem-no-org")
    membership = OrganizationMembership(
        organization_id=uuid.uuid4(), user_id=user.id, role=Role.STAFF
    )
    db_session.add(membership)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_membership_requires_a_valid_user(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("mem-no-user")
    membership = OrganizationMembership(
        organization_id=org.id, user_id=uuid.uuid4(), role=Role.STAFF
    )
    db_session.add(membership)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_membership_is_unique_per_organization_and_user(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("mem-unique")
    user = await make_user("mem-unique")
    await make_membership(org, user, role=Role.ADMIN)

    duplicate = OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.STAFF)
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_membership_relationship_to_organization_and_user(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("mem-relationship")
    user = await make_user("mem-relationship")
    membership = await make_membership(org, user, role=Role.STAFF)

    await db_session.refresh(membership, attribute_names=["organization", "user"])
    assert membership.organization.id == org.id
    assert membership.user.id == user.id

    await db_session.refresh(user, attribute_names=["memberships"])
    assert membership in user.memberships

    await db_session.refresh(org, attribute_names=["memberships"])
    assert membership in org.memberships


async def test_role_persists_and_round_trips(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("mem-role")
    user = await make_user("mem-role")
    membership = await make_membership(org, user, role=Role.PATIENT)
    membership_id = membership.id
    db_session.expire(membership)

    reloaded = await db_session.get(OrganizationMembership, membership_id)
    assert reloaded is not None
    assert reloaded.role is Role.PATIENT


async def test_user_can_have_memberships_in_multiple_organizations(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a = await make_organization("mem-multi-a")
    org_b = await make_organization("mem-multi-b")
    user = await make_user("mem-multi-user")

    membership_a = await make_membership(org_a, user, role=Role.ADMIN)
    membership_b = await make_membership(org_b, user, role=Role.STAFF)

    assert membership_a.organization_id != membership_b.organization_id
    assert membership_a.user_id == membership_b.user_id == user.id


async def test_membership_is_active_defaults_true(
    db_session: AsyncSession, make_organization: MakeOrganization, make_user: MakeUser
) -> None:
    org = await make_organization("mem-active-default")
    user = await make_user("mem-active-default")

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.STAFF)
    db_session.add(membership)
    await db_session.flush()

    assert membership.is_active is True


async def test_membership_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession, make_organization: MakeOrganization, make_user: MakeUser
) -> None:
    org = await make_organization("mem-timestamps")
    user = await make_user("mem-timestamps")

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.STAFF)
    db_session.add(membership)
    await db_session.flush()

    assert membership.created_at is not None
    assert membership.updated_at is not None
    assert membership.created_at.tzinfo is not None
    assert membership.updated_at.tzinfo is not None
