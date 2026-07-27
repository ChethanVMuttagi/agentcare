"""app.repositories.availability tests against real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.repositories import availability as availability_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]


async def test_list_by_practitioner_returns_only_that_practitioners_windows(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("repo-avail-list")
    facility = await make_facility(org, "repo-avail-list")
    department = await make_department(org, facility, "REPO-AVAIL-LIST")
    practitioner_a = await make_practitioner(org)
    practitioner_b = await make_practitioner(org)
    await make_practitioner_department(org, practitioner_a, department)
    await make_practitioner_department(org, practitioner_b, department)
    window_a = await make_availability(org, practitioner_a, department)
    await make_availability(org, practitioner_b, department)

    results = await availability_repository.list_by_practitioner(
        db_session, organization_id=org.id, practitioner_id=practitioner_a.id
    )

    assert [w.id for w in results] == [window_a.id]


async def test_list_active_by_practitioner_department_day_filters_correctly(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("repo-avail-overlap-candidates")
    facility = await make_facility(org, "repo-avail-overlap-candidates")
    department = await make_department(org, facility, "REPO-AVAIL-CANDIDATES")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    monday_active = await make_availability(
        org, practitioner, department, day_of_week=DayOfWeek.MONDAY, is_active=True
    )
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(13, 0),
        end_time=time(15, 0),
        is_active=False,
    )
    await make_availability(org, practitioner, department, day_of_week=DayOfWeek.TUESDAY)

    results = await availability_repository.list_active_by_practitioner_department_day(
        db_session,
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
    )

    assert [w.id for w in results] == [monday_active.id]


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("repo-avail-create-no-commit")
    facility = await make_facility(org, "repo-avail-create-no-commit")
    department = await make_department(org, facility, "REPO-AVAIL-CREATE")
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

    created = await availability_repository.create(db_session, availability)
    assert created.id is not None

    await db_session.rollback()

    results = await availability_repository.list_by_practitioner(
        db_session, organization_id=org.id, practitioner_id=practitioner.id
    )
    assert results == []
