"""PractitionerAvailability model tests against real PostgreSQL.

This represents a RECURRING availability window, not a materialized
appointment slot — see docs/SCHEDULING_RESOURCES.md. See
tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL and
how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]


async def test_availability_id_is_generated_uuid(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("avail-uuid")
    facility = await make_facility(org, "avail-uuid")
    department = await make_department(org, facility, "AVAIL-UUID")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    availability = PractitionerAvailability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )
    db_session.add(availability)
    await db_session.flush()

    assert isinstance(availability.id, uuid.UUID)


async def test_availability_requires_an_existing_assignment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    """No `PractitionerDepartment` row exists for this pairing — the
    composite FK into `practitioner_departments` must reject it."""
    org = await make_organization("avail-no-assignment")
    facility = await make_facility(org, "avail-no-assignment")
    department = await make_department(org, facility, "AVAIL-NO-ASSIGN")
    practitioner = await make_practitioner(org)  # never assigned to `department`

    availability = PractitionerAvailability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )
    db_session.add(availability)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_availability_start_time_must_be_before_end_time(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("avail-bad-range")
    facility = await make_facility(org, "avail-bad-range")
    department = await make_department(org, facility, "AVAIL-BAD-RANGE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    availability = PractitionerAvailability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(12, 0),
        end_time=time(9, 0),  # before start_time
        timezone="UTC",
    )
    db_session.add(availability)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_availability_rejects_equal_start_and_end_time(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("avail-equal-range")
    facility = await make_facility(org, "avail-equal-range")
    department = await make_department(org, facility, "AVAIL-EQUAL-RANGE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    availability = PractitionerAvailability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(9, 0),
        timezone="UTC",
    )
    db_session.add(availability)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_availability_day_of_week_persists_and_round_trips(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("avail-day-roundtrip")
    facility = await make_facility(org, "avail-day-roundtrip")
    department = await make_department(org, facility, "AVAIL-DAY")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    availability = await make_availability(
        org, practitioner, department, day_of_week=DayOfWeek.FRIDAY
    )
    availability_id = availability.id
    db_session.expire(availability)

    reloaded = await db_session.get(PractitionerAvailability, availability_id)
    assert reloaded is not None
    assert reloaded.day_of_week is DayOfWeek.FRIDAY


async def test_availability_timezone_rejects_invalid_identifier_at_application_layer(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("avail-bad-tz")
    facility = await make_facility(org, "avail-bad-tz")
    department = await make_department(org, facility, "AVAIL-BAD-TZ")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    with pytest.raises(ValueError, match="IANA timezone"):
        PractitionerAvailability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            timezone="Not/A_Real_Timezone",
        )


async def test_availability_timezone_persists(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("avail-tz-persist")
    facility = await make_facility(org, "avail-tz-persist")
    department = await make_department(org, facility, "AVAIL-TZ-PERSIST")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    availability = await make_availability(
        org, practitioner, department, timezone="Asia/Kolkata"
    )

    assert availability.timezone == "Asia/Kolkata"


async def test_availability_day_of_week_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    # Proves the CHECK constraint is real, database-level enforcement,
    # not just SQLAlchemy's application-side `validate_strings`.
    org = await make_organization("avail-raw-day-check")
    facility = await make_facility(org, "avail-raw-day-check")
    department = await make_department(org, facility, "AVAIL-RAW-DAY")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO practitioner_availability "
                "(id, organization_id, practitioner_id, department_id, day_of_week, "
                "start_time, end_time, timezone, is_active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :practitioner_id, :department_id, "
                "'not_a_real_day', '09:00', '12:00', 'UTC', true, now(), now())"
            ),
            {
                "org_id": org.id,
                "practitioner_id": practitioner.id,
                "department_id": department.id,
            },
        )
    await db_session.rollback()


async def test_availability_is_active_defaults_true(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("avail-active-default")
    facility = await make_facility(org, "avail-active-default")
    department = await make_department(org, facility, "AVAIL-ACTIVE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    availability = PractitionerAvailability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )
    db_session.add(availability)
    await db_session.flush()

    assert availability.is_active is True


async def test_availability_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("avail-timestamps")
    facility = await make_facility(org, "avail-timestamps")
    department = await make_department(org, facility, "AVAIL-TIMESTAMPS")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    availability = PractitionerAvailability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )
    db_session.add(availability)
    await db_session.flush()

    assert availability.created_at is not None
    assert availability.updated_at is not None
    assert availability.created_at.tzinfo is not None
    assert availability.updated_at.tzinfo is not None


async def test_availability_relationships(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("avail-relationships")
    facility = await make_facility(org, "avail-relationships")
    department = await make_department(org, facility, "AVAIL-REL")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    availability = await make_availability(org, practitioner, department)

    await db_session.refresh(availability, attribute_names=["practitioner", "department"])
    assert availability.practitioner.id == practitioner.id
    assert availability.department.id == department.id

    await db_session.refresh(practitioner, attribute_names=["availability_windows"])
    assert availability in practitioner.availability_windows

    await db_session.refresh(department, attribute_names=["availability_windows"])
    assert availability in department.availability_windows
