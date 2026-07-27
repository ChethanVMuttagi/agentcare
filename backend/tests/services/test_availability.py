"""app.services.availability.AvailabilityService tests against real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.services.availability import (
    AvailabilityOverlapError,
    AvailabilityService,
    InvalidAvailabilityTimeRangeError,
    InvalidAvailabilityTimezoneError,
    PractitionerNotAssignedError,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]


async def test_create_availability_succeeds(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("svc-avail-create")
    facility = await make_facility(org, "svc-avail-create")
    department = await make_department(org, facility, "SVC-AVAIL-CREATE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    service = AvailabilityService(db_session)

    availability = await service.create_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="Asia/Kolkata",
    )

    assert availability.id is not None
    assert availability.timezone == "Asia/Kolkata"


async def test_create_availability_rejects_unassigned_practitioner(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("svc-avail-unassigned")
    facility = await make_facility(org, "svc-avail-unassigned")
    department = await make_department(org, facility, "SVC-AVAIL-UNASSIGNED")
    practitioner = await make_practitioner(org)  # never assigned
    service = AvailabilityService(db_session)

    with pytest.raises(PractitionerNotAssignedError):
        await service.create_availability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            timezone="UTC",
        )


async def test_create_availability_rejects_inactive_assignment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("svc-avail-inactive-assign")
    facility = await make_facility(org, "svc-avail-inactive-assign")
    department = await make_department(org, facility, "SVC-AVAIL-INACTIVE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department, is_active=False)
    service = AvailabilityService(db_session)

    with pytest.raises(PractitionerNotAssignedError):
        await service.create_availability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            timezone="UTC",
        )


async def test_create_availability_rejects_invalid_time_range(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("svc-avail-bad-range")
    facility = await make_facility(org, "svc-avail-bad-range")
    department = await make_department(org, facility, "SVC-AVAIL-BAD-RANGE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    service = AvailabilityService(db_session)

    with pytest.raises(InvalidAvailabilityTimeRangeError):
        await service.create_availability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(12, 0),
            end_time=time(9, 0),
            timezone="UTC",
        )


async def test_create_availability_rejects_invalid_timezone(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("svc-avail-bad-tz")
    facility = await make_facility(org, "svc-avail-bad-tz")
    department = await make_department(org, facility, "SVC-AVAIL-BAD-TZ")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    service = AvailabilityService(db_session)

    with pytest.raises(InvalidAvailabilityTimezoneError):
        await service.create_availability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            timezone="Not/A_Real_Timezone",
        )


async def test_create_availability_rejects_overlap(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("svc-avail-overlap")
    facility = await make_facility(org, "svc-avail-overlap")
    department = await make_department(org, facility, "SVC-AVAIL-OVERLAP")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
    )
    service = AvailabilityService(db_session)

    with pytest.raises(AvailabilityOverlapError):
        await service.create_availability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=DayOfWeek.MONDAY,
            start_time=time(11, 0),
            end_time=time(13, 0),
            timezone="UTC",
        )


async def test_create_availability_allows_adjacent_windows(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("svc-avail-adjacent")
    facility = await make_facility(org, "svc-avail-adjacent")
    department = await make_department(org, facility, "SVC-AVAIL-ADJACENT")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
    )
    service = AvailabilityService(db_session)

    availability = await service.create_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(12, 0),
        end_time=time(15, 0),
        timezone="UTC",
    )

    assert availability.start_time == time(12, 0)


async def test_create_availability_allows_overlap_against_inactive_window(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("svc-avail-inactive-overlap")
    facility = await make_facility(org, "svc-avail-inactive-overlap")
    department = await make_department(org, facility, "SVC-AVAIL-INACTIVE-OVERLAP")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        is_active=False,
    )
    service = AvailabilityService(db_session)

    availability = await service.create_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )

    assert availability.id is not None


async def test_create_availability_allows_overlap_on_different_day(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("svc-avail-different-day")
    facility = await make_facility(org, "svc-avail-different-day")
    department = await make_department(org, facility, "SVC-AVAIL-DIFF-DAY")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
    )
    service = AvailabilityService(db_session)

    availability = await service.create_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        day_of_week=DayOfWeek.TUESDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )

    assert availability.day_of_week is DayOfWeek.TUESDAY


async def test_list_availability_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org_a = await make_organization("svc-avail-list-a")
    org_b = await make_organization("svc-avail-list-b")
    facility_a = await make_facility(org_a, "svc-avail-list-a")
    facility_b = await make_facility(org_b, "svc-avail-list-b")
    department_a = await make_department(org_a, facility_a, "SVC-AVAIL-LIST-A")
    department_b = await make_department(org_b, facility_b, "SVC-AVAIL-LIST-B")
    practitioner_a = await make_practitioner(org_a)
    practitioner_b = await make_practitioner(org_b)
    await make_practitioner_department(org_a, practitioner_a, department_a)
    await make_practitioner_department(org_b, practitioner_b, department_b)
    window_a = await make_availability(org_a, practitioner_a, department_a)
    await make_availability(org_b, practitioner_b, department_b)
    service = AvailabilityService(db_session)

    results = await service.list_availability(
        organization_id=org_a.id, practitioner_id=practitioner_a.id
    )

    assert [w.id for w in results] == [window_a.id]
