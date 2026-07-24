"""Facility model tests against real PostgreSQL.

See tests/models/conftest.py for why these require
AGENTCARE_TEST_POSTGRES_URL and how test data is guaranteed not to persist
(savepoint-rollback session). All names/codes are obviously synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.facility import Facility, FacilityType
from app.models.organization import Organization


async def test_facility_id_is_generated_uuid(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-uuid")
    facility = Facility(
        organization_id=org.id,
        name="Synthetic Facility",
        code="SF-UUID",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add(facility)
    await db_session.flush()

    assert isinstance(facility.id, uuid.UUID)


async def test_facility_requires_a_valid_organization(db_session: AsyncSession) -> None:
    facility = Facility(
        organization_id=uuid.uuid4(),  # no such organization exists
        name="Orphan Facility",
        code="ORPHAN-1",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add(facility)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_facility_organization_relationship(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-relationship")
    facility = Facility(
        organization_id=org.id,
        name="Synthetic Facility",
        code="SF-REL",
        facility_type=FacilityType.HOSPITAL,
        timezone="UTC",
    )
    db_session.add(facility)
    await db_session.flush()

    await db_session.refresh(facility, attribute_names=["organization"])
    assert facility.organization.id == org.id

    await db_session.refresh(org, attribute_names=["facilities"])
    assert facility in org.facilities


async def test_facility_code_unique_within_organization(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-code-uniqueness")
    facility1 = Facility(
        organization_id=org.id,
        name="Facility One",
        code="DUP-CODE",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add(facility1)
    await db_session.flush()

    facility2 = Facility(
        organization_id=org.id,
        name="Facility Two",
        code="DUP-CODE",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add(facility2)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_same_facility_code_allowed_across_different_organizations(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org1 = await make_organization("fac-code-cross-org-1")
    org2 = await make_organization("fac-code-cross-org-2")

    facility1 = Facility(
        organization_id=org1.id,
        name="Facility A",
        code="SHARED-CODE",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    facility2 = Facility(
        organization_id=org2.id,
        name="Facility B",
        code="SHARED-CODE",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add_all([facility1, facility2])

    await db_session.flush()  # must NOT raise

    assert facility1.code == facility2.code
    assert facility1.organization_id != facility2.organization_id


async def test_facility_type_persists_and_round_trips(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-enum")
    facility = Facility(
        organization_id=org.id,
        name="Synthetic Enum Facility",
        code="ENUM-1",
        facility_type=FacilityType.DIAGNOSTIC_CENTER,
        timezone="UTC",
    )
    db_session.add(facility)
    await db_session.flush()
    facility_id = facility.id  # capture before expiring: expire() expires `id` too
    db_session.expire(facility)

    reloaded = await db_session.get(Facility, facility_id)
    assert reloaded is not None
    assert reloaded.facility_type is FacilityType.DIAGNOSTIC_CENTER


async def test_facility_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    # Proves the CHECK constraint is real, database-level enforcement, not
    # just SQLAlchemy's application-side `validate_strings` — by inserting
    # a bogus value with raw SQL, bypassing the ORM entirely.
    org = await make_organization("fac-check-constraint")
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO facilities "
                "(id, organization_id, name, code, facility_type, timezone, "
                "is_active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, 'Raw SQL Facility', "
                "'RAW-1', 'not_a_real_type', 'UTC', true, now(), now())"
            ),
            {"org_id": org.id},
        )
    await db_session.rollback()


async def test_facility_timezone_persists(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-timezone")
    facility = Facility(
        organization_id=org.id,
        name="Synthetic Timezone Facility",
        code="TZ-1",
        facility_type=FacilityType.HOSPITAL,
        timezone="America/New_York",
    )
    db_session.add(facility)
    await db_session.flush()
    facility_id = facility.id  # capture before expiring: expire() expires `id` too
    db_session.expire(facility)

    reloaded = await db_session.get(Facility, facility_id)
    assert reloaded is not None
    assert reloaded.timezone == "America/New_York"


def test_facility_rejects_invalid_timezone_at_application_layer() -> None:
    # Pure application-level validation — no database involved, so this
    # runs regardless of AGENTCARE_TEST_POSTGRES_URL.
    with pytest.raises(ValueError, match="valid IANA timezone"):
        Facility(
            organization_id=uuid.uuid4(),
            name="Bad Timezone Facility",
            code="BADTZ-1",
            facility_type=FacilityType.CLINIC,
            timezone="Not/A_Real_Zone",
        )


async def test_facility_is_active_defaults_true(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-active-default")
    facility = Facility(
        organization_id=org.id,
        name="Synthetic Active Facility",
        code="ACTIVE-1",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add(facility)
    await db_session.flush()

    assert facility.is_active is True


async def test_facility_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession, make_organization: Callable[..., Awaitable[Organization]]
) -> None:
    org = await make_organization("fac-timestamps")
    facility = Facility(
        organization_id=org.id,
        name="Synthetic Timestamp Facility",
        code="TS-1",
        facility_type=FacilityType.CLINIC,
        timezone="UTC",
    )
    db_session.add(facility)
    await db_session.flush()

    assert facility.created_at is not None
    assert facility.updated_at is not None
    assert facility.created_at.tzinfo is not None
    assert facility.updated_at.tzinfo is not None
