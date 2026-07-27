"""app.services.availability_query.AvailabilityQueryService tests against
real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.services.availability_query import AvailabilityQueryService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeAppointment = Callable[..., Awaitable[Appointment]]

# A concrete, always-Monday, always-future date so windows created with
# `day_of_week=DayOfWeek.MONDAY` reliably match. Chosen far enough out that
# "not in the past" is never a concern for any test in this file.
_FUTURE_MONDAY = date(2026, 8, 3)
assert _FUTURE_MONDAY.weekday() == 0


async def _scenario(
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    suffix: str,
) -> tuple[Organization, Department, Practitioner]:
    org = await make_organization(suffix)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    return org, department, practitioner


async def test_list_available_times_returns_slots_within_window(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-basic",
    )
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        timezone="UTC",
    )
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
        slot_interval_minutes=15,
    )

    # 09:00, 09:15, 09:30 all fit a 30-minute appointment before 10:00;
    # 09:45 does not (09:45 + 30min = 10:15 > 10:00).
    starts = [s.start_at.time() for s in slots]
    assert starts == [time(9, 0), time(9, 15), time(9, 30)]
    assert all(s.start_at.tzinfo is not None for s in slots)


async def test_list_available_times_wrong_weekday_returns_empty(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-wrong-day",
    )
    await make_availability(
        org, practitioner, department, day_of_week=DayOfWeek.TUESDAY  # not _FUTURE_MONDAY
    )
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
    )

    assert slots == []


async def test_list_available_times_converts_timezone_correctly(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-tz",
    )
    # Asia/Kolkata is UTC+05:30 with no DST — 09:00 local == 03:30 UTC.
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(9, 30),
        timezone="Asia/Kolkata",
    )
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
    )

    assert len(slots) == 1
    assert slots[0].start_at == datetime(2026, 8, 3, 3, 30, tzinfo=UTC)


async def test_list_available_times_excludes_conflicting_booked_appointment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-conflict",
    )
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        timezone="UTC",
    )
    patient = await make_patient(org, "PN-avq-conflict")
    booked_start = datetime(2026, 8, 3, 9, 15, tzinfo=UTC)
    await make_appointment(
        org, patient, practitioner, department, start_at=booked_start, duration_minutes=30
    )
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
        slot_interval_minutes=15,
    )

    starts = [s.start_at.time() for s in slots]
    # 09:00 would end at 09:30, overlapping the 09:15-09:45 booking -> excluded.
    # 09:15, 09:30 directly overlap -> excluded.
    assert time(9, 0) not in starts
    assert time(9, 15) not in starts
    assert time(9, 30) not in starts


async def test_list_available_times_cancelled_appointment_does_not_exclude_slot(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-cancelled",
    )
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(9, 30),
        timezone="UTC",
    )
    patient = await make_patient(org, "PN-avq-cancelled")
    await make_appointment(
        org,
        patient,
        practitioner,
        department,
        start_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        duration_minutes=30,
        status=AppointmentStatus.CANCELLED,
    )
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
    )

    assert len(slots) == 1
    assert slots[0].start_at == datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


async def test_list_available_times_inactive_practitioner_returns_empty(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("avq-inactive-prac")
    facility = await make_facility(org, "avq-inactive-prac")
    department = await make_department(org, facility, "AVQ-INACTIVE-PRAC")
    practitioner = await make_practitioner(org, is_active=False)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(org, practitioner, department, day_of_week=DayOfWeek.MONDAY)
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
    )

    assert slots == []


async def test_list_available_times_inactive_department_returns_empty(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("avq-inactive-dept")
    facility = await make_facility(org, "avq-inactive-dept")
    department = await make_department(org, facility, "AVQ-INACTIVE-DEPT", is_active=False)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(org, practitioner, department, day_of_week=DayOfWeek.MONDAY)
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
    )

    assert slots == []


async def test_list_available_times_inactive_assignment_returns_empty(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("avq-inactive-assign")
    facility = await make_facility(org, "avq-inactive-assign")
    department = await make_department(org, facility, "AVQ-INACTIVE-ASSIGN")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department, is_active=False)
    await make_availability(org, practitioner, department, day_of_week=DayOfWeek.MONDAY)
    service = AvailabilityQueryService(db_session)

    slots = await service.list_available_times(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        on_date=_FUTURE_MONDAY,
        duration_minutes=30,
    )

    assert slots == []


async def test_is_within_availability_true_for_time_inside_window(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-fits",
    )
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(12, 0),
        timezone="UTC",
    )
    service = AvailabilityQueryService(db_session)

    fits = await service.is_within_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=datetime(2026, 8, 3, 9, 30, tzinfo=UTC),
        end_at=datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
    )

    assert fits is True


async def test_is_within_availability_false_when_time_extends_past_window(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-overflow",
    )
    await make_availability(
        org,
        practitioner,
        department,
        day_of_week=DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        timezone="UTC",
    )
    service = AvailabilityQueryService(db_session)

    fits = await service.is_within_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=datetime(2026, 8, 3, 9, 45, tzinfo=UTC),
        end_at=datetime(2026, 8, 3, 10, 15, tzinfo=UTC),
    )

    assert fits is False


async def test_is_within_availability_false_for_no_matching_window(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org, department, practitioner = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        "avq-none",
    )
    service = AvailabilityQueryService(db_session)

    fits = await service.is_within_availability(
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=datetime(2026, 8, 3, 9, 30, tzinfo=UTC),
        end_at=datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
    )

    assert fits is False
