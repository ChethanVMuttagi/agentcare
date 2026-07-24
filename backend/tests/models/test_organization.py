"""Organization model tests against real PostgreSQL.

See tests/models/conftest.py for why these require
AGENTCARE_TEST_POSTGRES_URL and how test data is guaranteed not to persist
(savepoint-rollback session). All names/slugs are obviously synthetic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrganizationType


async def test_organization_id_is_generated_uuid(db_session: AsyncSession) -> None:
    org = Organization(
        name="Synthetic Test Hospital",
        slug="synthetic-test-hospital-uuid",
        organization_type=OrganizationType.HOSPITAL,
    )
    db_session.add(org)
    await db_session.flush()

    assert isinstance(org.id, uuid.UUID)


async def test_organization_requires_name(db_session: AsyncSession) -> None:
    org = Organization(
        slug="synthetic-org-missing-name",
        organization_type=OrganizationType.CLINIC,
    )
    db_session.add(org)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_organization_requires_slug(db_session: AsyncSession) -> None:
    org = Organization(
        name="Synthetic Org Missing Slug",
        organization_type=OrganizationType.CLINIC,
    )
    db_session.add(org)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_organization_slug_must_be_unique(db_session: AsyncSession) -> None:
    org1 = Organization(
        name="Synthetic Duplicate Slug Org A",
        slug="synthetic-duplicate-slug",
        organization_type=OrganizationType.HOSPITAL,
    )
    db_session.add(org1)
    await db_session.flush()

    org2 = Organization(
        name="Synthetic Duplicate Slug Org B",
        slug="synthetic-duplicate-slug",
        organization_type=OrganizationType.CLINIC,
    )
    db_session.add(org2)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_organization_type_persists_and_round_trips(db_session: AsyncSession) -> None:
    org = Organization(
        name="Synthetic Enum Org",
        slug="synthetic-enum-org",
        organization_type=OrganizationType.HOSPITAL_GROUP,
    )
    db_session.add(org)
    await db_session.flush()
    org_id = org.id  # capture before expiring: expire() also expires `id`
    db_session.expire(org)

    reloaded = await db_session.get(Organization, org_id)
    assert reloaded is not None
    assert reloaded.organization_type is OrganizationType.HOSPITAL_GROUP


async def test_organization_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
) -> None:
    # Proves the CHECK constraint is real, database-level enforcement —
    # not just SQLAlchemy's application-side `validate_strings` — by
    # inserting a bogus value with raw SQL, bypassing the ORM entirely.
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO organizations "
                "(id, name, slug, organization_type, is_active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'Raw SQL Org', "
                "'synthetic-raw-sql-invalid-org-type', 'not_a_real_type', "
                "true, now(), now())"
            )
        )
    await db_session.rollback()


async def test_organization_is_active_defaults_true(db_session: AsyncSession) -> None:
    org = Organization(
        name="Synthetic Active Org",
        slug="synthetic-active-org",
        organization_type=OrganizationType.CLINIC,
    )
    db_session.add(org)
    await db_session.flush()

    assert org.is_active is True


async def test_organization_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession,
) -> None:
    org = Organization(
        name="Synthetic Timestamp Org",
        slug="synthetic-timestamp-org",
        organization_type=OrganizationType.HEALTHCARE_PROVIDER,
    )
    db_session.add(org)
    await db_session.flush()

    assert org.created_at is not None
    assert org.updated_at is not None
    assert org.created_at.tzinfo is not None
    assert org.updated_at.tzinfo is not None
