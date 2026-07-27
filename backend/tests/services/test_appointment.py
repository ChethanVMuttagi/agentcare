"""app.services.appointment.AppointmentService tests against real PostgreSQL.

Sequential correctness only — the mandatory GENUINE concurrency proof
lives in tests/db/test_appointment_concurrency.py (real, independent,
concurrently-executing transactions). This file proves the business-rule
validation, state transitions, and the (sequential) translation of the
database's exclusion-constraint violation into `AppointmentConflictError`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.repositories import appointment as appointment_repository
from app.services.appointment import (
    AppointmentConflictError,
    AppointmentInPastError,
    AppointmentNotFoundError,
    AppointmentOutsideAvailabilityError,
    AppointmentService,
    DepartmentInactiveError,
    InvalidAppointmentDurationError,
    InvalidAppointmentTransitionError,
    PatientInactiveError,
    PractitionerInactiveError,
    PractitionerNotAssignedError,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]
MakePatient = Callable[..., Awaitable[Patient]]

_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def _wide_open_scenario(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    suffix: str,
) -> tuple[Organization, Department, Practitioner, Patient]:
    """A scenario with availability covering every day of the week,
    00:00-23:59:59 UTC, so tests don't need to reason about which weekday
    `_FUTURE` happens to fall on."""
    org = await make_organization(suffix)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")

    for day in DayOfWeek:
        window = PractitionerAvailability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=day,
            start_time=time(0, 0),
            end_time=time(23, 59, 59),
            timezone="UTC",
        )
        db_session.add(window)
    await db_session.flush()

    return org, department, practitioner, patient


# --- book_appointment ---------------------------------------------------


async def test_book_appointment_succeeds(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-book",
    )
    service = AppointmentService(db_session)

    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    assert appointment.id is not None
    assert appointment.end_at == _FUTURE + timedelta(minutes=30)


async def test_book_appointment_rejects_duration_too_short(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-too-short",
    )
    service = AppointmentService(db_session)

    with pytest.raises(InvalidAppointmentDurationError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=5,
        )


async def test_book_appointment_rejects_duration_too_long(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-too-long",
    )
    service = AppointmentService(db_session)

    with pytest.raises(InvalidAppointmentDurationError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=300,
        )


async def test_book_appointment_rejects_cross_tenant_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-cross-patient",
    )
    other_org = await make_organization("svc-appt-cross-patient-other")
    other_patient = await make_patient(other_org, "PN-svc-appt-cross-patient-other")
    service = AppointmentService(db_session)

    with pytest.raises(AppointmentNotFoundError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=other_patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=30,
        )


async def test_book_appointment_rejects_inactive_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-appt-inactive-patient")
    facility = await make_facility(org, "svc-appt-inactive-patient")
    department = await make_department(org, facility, "SVC-APPT-INACTIVE-PATIENT")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-svc-appt-inactive-patient", is_active=False)
    service = AppointmentService(db_session)

    with pytest.raises(PatientInactiveError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=30,
        )


async def test_book_appointment_rejects_inactive_practitioner(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-appt-inactive-prac")
    facility = await make_facility(org, "svc-appt-inactive-prac")
    department = await make_department(org, facility, "SVC-APPT-INACTIVE-PRAC")
    practitioner = await make_practitioner(org, is_active=False)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-svc-appt-inactive-prac")
    service = AppointmentService(db_session)

    with pytest.raises(PractitionerInactiveError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=30,
        )


async def test_book_appointment_rejects_inactive_department(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-appt-inactive-dept")
    facility = await make_facility(org, "svc-appt-inactive-dept")
    department = await make_department(
        org, facility, "SVC-APPT-INACTIVE-DEPT", is_active=False
    )
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-svc-appt-inactive-dept")
    service = AppointmentService(db_session)

    with pytest.raises(DepartmentInactiveError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=30,
        )


async def test_book_appointment_rejects_unassigned_practitioner(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-appt-unassigned")
    facility = await make_facility(org, "svc-appt-unassigned")
    department = await make_department(org, facility, "SVC-APPT-UNASSIGNED")
    practitioner = await make_practitioner(org)  # never assigned
    patient = await make_patient(org, "PN-svc-appt-unassigned")
    service = AppointmentService(db_session)

    with pytest.raises(PractitionerNotAssignedError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=30,
        )


async def test_book_appointment_rejects_time_in_the_past(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-past",
    )
    service = AppointmentService(db_session)

    with pytest.raises(AppointmentInPastError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=datetime.now(UTC) - timedelta(hours=1),
            duration_minutes=30,
        )


async def test_book_appointment_rejects_time_outside_availability(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-appt-outside-avail")
    facility = await make_facility(org, "svc-appt-outside-avail")
    department = await make_department(org, facility, "SVC-APPT-OUTSIDE-AVAIL")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-svc-appt-outside-avail")
    # No availability windows created at all.
    service = AppointmentService(db_session)

    with pytest.raises(AppointmentOutsideAvailabilityError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE,
            duration_minutes=30,
        )


async def test_book_appointment_rejects_overlapping_conflict(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-conflict",
    )
    patient_b = await make_patient(org, "PN-svc-appt-conflict-b")
    # Captured as plain UUIDs BEFORE the conflict: `book_appointment`'s
    # internal `session.rollback()` (on conflict) expires every object
    # already loaded into this shared session — including `org` — so any
    # further `org.id`-style attribute access after the conflict would
    # trigger a synchronous expired-attribute reload outside of an
    # awaited context. Real callers never hit this: a request ends at the
    # exception (see `app.core.exceptions`), it doesn't keep using other
    # objects from the same session afterward.
    org_id = org.id
    service = AppointmentService(db_session)
    await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    with pytest.raises(AppointmentConflictError):
        await service.book_appointment(
            organization_id=org.id,
            patient_id=patient_b.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=_FUTURE + timedelta(minutes=15),
            duration_minutes=30,
        )

    # Session must remain usable after the rollback inside book_appointment.
    results = await appointment_repository.list_by_organization(db_session, organization_id=org_id)
    assert len(results) == 1


async def test_book_appointment_allows_adjacent_slot(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-adjacent",
    )
    patient_b = await make_patient(org, "PN-svc-appt-adjacent-b")
    service = AppointmentService(db_session)
    await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    second = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE + timedelta(minutes=30),
        duration_minutes=30,
    )

    assert second.id is not None


# --- get_appointment / listing ------------------------------------------


async def test_get_appointment_cross_tenant_returns_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org_a, department_a, practitioner_a, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-get-cross-a",
    )
    org_b = await make_organization("svc-appt-get-cross-b")
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org_a.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner_a.id,
        department_id=department_a.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    with pytest.raises(AppointmentNotFoundError):
        await service.get_appointment(organization_id=org_b.id, appointment_id=appointment.id)


async def test_get_appointment_scoped_to_wrong_patient_returns_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-get-wrong-patient",
    )
    patient_b = await make_patient(org, "PN-svc-appt-get-wrong-patient-b")
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    with pytest.raises(AppointmentNotFoundError):
        await service.get_appointment(
            organization_id=org.id, appointment_id=appointment.id, patient_id=patient_b.id
        )

    # The owning patient CAN still retrieve it.
    found = await service.get_appointment(
        organization_id=org.id, appointment_id=appointment.id, patient_id=patient_a.id
    )
    assert found.id == appointment.id


# --- reschedule_appointment ----------------------------------------------


async def test_reschedule_appointment_succeeds(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-reschedule",
    )
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )
    new_start = _FUTURE + timedelta(hours=2)

    rescheduled = await service.reschedule_appointment(
        organization_id=org.id,
        appointment_id=appointment.id,
        start_at=new_start,
        duration_minutes=45,
    )

    assert rescheduled.start_at == new_start
    assert rescheduled.end_at == new_start + timedelta(minutes=45)


async def test_reschedule_rejects_non_booked_appointment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-reschedule-cancelled",
    )
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )
    await service.cancel_appointment(organization_id=org.id, appointment_id=appointment.id)

    with pytest.raises(InvalidAppointmentTransitionError):
        await service.reschedule_appointment(
            organization_id=org.id,
            appointment_id=appointment.id,
            start_at=_FUTURE + timedelta(hours=2),
            duration_minutes=30,
        )


async def test_reschedule_conflict_leaves_original_appointment_unchanged(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-reschedule-conflict",
    )
    patient_b = await make_patient(org, "PN-svc-appt-reschedule-conflict-b")
    service = AppointmentService(db_session)
    blocking_start = _FUTURE + timedelta(hours=5)
    await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=blocking_start,
        duration_minutes=30,
    )
    original_start = _FUTURE
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=original_start,
        duration_minutes=30,
    )
    # Captured as plain UUIDs BEFORE the conflict: `reschedule_appointment`'s
    # internal `session.rollback()` (on conflict) expires every object
    # already loaded into this shared session, so `appointment.id`/`org.id`
    # must not be accessed as ORM attributes after the conflict — see the
    # identical note in `test_book_appointment_rejects_overlapping_conflict`.
    org_id = org.id
    appointment_id = appointment.id

    with pytest.raises(AppointmentConflictError):
        await service.reschedule_appointment(
            organization_id=org_id,
            appointment_id=appointment_id,
            start_at=blocking_start + timedelta(minutes=10),
            duration_minutes=30,
        )

    reloaded = await service.get_appointment(
        organization_id=org_id, appointment_id=appointment_id
    )
    assert reloaded.start_at == original_start
    assert reloaded.end_at == original_start + timedelta(minutes=30)


async def test_reschedule_scoped_to_wrong_patient_is_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-reschedule-wrong-patient",
    )
    patient_b = await make_patient(org, "PN-svc-appt-reschedule-wrong-patient-b")
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    with pytest.raises(AppointmentNotFoundError):
        await service.reschedule_appointment(
            organization_id=org.id,
            appointment_id=appointment.id,
            start_at=_FUTURE + timedelta(hours=2),
            duration_minutes=30,
            patient_id=patient_b.id,
        )


# --- cancel_appointment ---------------------------------------------------


async def test_cancel_appointment_succeeds_with_reason(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-cancel",
    )
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    cancelled = await service.cancel_appointment(
        organization_id=org.id,
        appointment_id=appointment.id,
        cancellation_reason="patient requested",
    )

    assert cancelled.status is AppointmentStatus.CANCELLED
    assert cancelled.cancellation_reason == "patient requested"


async def test_cancel_appointment_rejects_repeated_cancellation(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-cancel-twice",
    )
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )
    await service.cancel_appointment(organization_id=org.id, appointment_id=appointment.id)

    with pytest.raises(InvalidAppointmentTransitionError):
        await service.cancel_appointment(organization_id=org.id, appointment_id=appointment.id)


async def test_cancelled_appointment_frees_the_slot_for_rebooking(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "svc-appt-cancel-frees",
    )
    patient_b = await make_patient(org, "PN-svc-appt-cancel-frees-b")
    service = AppointmentService(db_session)
    original = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_a.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )
    await service.cancel_appointment(organization_id=org.id, appointment_id=original.id)

    rebooked = await service.book_appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        duration_minutes=30,
    )

    assert rebooked.id is not None
