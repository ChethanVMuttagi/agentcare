"""Automatic reminder scheduling integration tests (STORY-013):
`app.services.appointment.AppointmentService` <->
`app.services.reminder_scheduler.ReminderScheduler`.

Covers the three mandated flows:

    Appointment booked      -> Reminder scheduled
    Appointment rescheduled -> Old reminder cancelled -> New reminder scheduled
    Appointment cancelled   -> Reminder cancelled

Plus the backward-compatibility and failure-isolation guarantees this
integration depends on: `initiated_by_user_id` is optional (omitting it
reproduces STORY-007's exact pre-STORY-013 behavior, no reminder at
all), and a reminder-scheduling failure never makes an already-genuinely-
successful appointment mutation look like it failed.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import ReminderStatus
from app.models.user import User
from app.repositories import reminder as reminder_repository
from app.services.appointment import AppointmentService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakePatient = Callable[..., Awaitable[Patient]]

_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def _wide_open_scenario(
    db_session: AsyncSession,
    suffix: str,
    *,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> tuple[Organization, User, Department, Practitioner, Patient]:
    org = await make_organization(suffix)
    admin = await make_user(suffix)
    await make_membership(org, admin, role=Role.ADMIN)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")
    for day in DayOfWeek:
        db_session.add(
            PractitionerAvailability(
                organization_id=org.id,
                practitioner_id=practitioner.id,
                department_id=department.id,
                day_of_week=day,
                start_time=time(0, 0),
                end_time=time(23, 59, 59),
                timezone="UTC",
            )
        )
    await db_session.flush()
    return org, admin, department, practitioner, patient


async def _reminders_for(
    db_session: AsyncSession, *, organization_id: uuid.UUID, appointment_id: uuid.UUID
) -> list:
    return list(
        await reminder_repository.list_by_appointment(
            db_session, organization_id=organization_id, appointment_id=appointment_id
        )
    )


async def test_book_appointment_schedules_a_reminder_when_initiator_given(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, admin, department, practitioner, patient = await _wide_open_scenario(
        db_session, "integ-book",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
    )
    service = AppointmentService(db_session)

    appointment = await service.book_appointment(
        organization_id=org.id, patient_id=patient.id, practitioner_id=practitioner.id,
        department_id=department.id, start_at=_FUTURE, duration_minutes=30,
        initiated_by_user_id=admin.id,
    )

    reminders = await _reminders_for(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert len(reminders) == 1
    assert reminders[0].status is ReminderStatus.PENDING
    assert reminders[0].scheduled_at == _FUTURE - timedelta(hours=24)


async def test_book_appointment_without_initiator_schedules_no_reminder(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    """Backward compatibility: omitting `initiated_by_user_id` reproduces
    pre-STORY-013 behavior exactly — no reminder, no error."""
    org, _admin, department, practitioner, patient = await _wide_open_scenario(
        db_session, "integ-book-no-initiator",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
    )
    service = AppointmentService(db_session)

    appointment = await service.book_appointment(
        organization_id=org.id, patient_id=patient.id, practitioner_id=practitioner.id,
        department_id=department.id, start_at=_FUTURE, duration_minutes=30,
    )

    reminders = await _reminders_for(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert reminders == []


async def test_book_appointment_succeeds_even_if_reminder_scheduling_fails(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    """A reminder-scheduling failure (here: `initiated_by_user_id` names
    no active member) must NEVER make an already-genuinely-successful
    booking look like it failed."""
    org, _admin, department, practitioner, patient = await _wide_open_scenario(
        db_session, "integ-book-reminder-fails",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
    )
    service = AppointmentService(db_session)

    appointment = await service.book_appointment(
        organization_id=org.id, patient_id=patient.id, practitioner_id=practitioner.id,
        department_id=department.id, start_at=_FUTURE, duration_minutes=30,
        initiated_by_user_id=uuid.uuid4(),  # not a real member -> reminder scheduling fails
    )

    assert appointment is not None
    assert appointment.status is AppointmentStatus.BOOKED

    refetched = await service.get_appointment(organization_id=org.id, appointment_id=appointment.id)
    assert refetched.id == appointment.id

    reminders = await _reminders_for(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert reminders == []  # scheduling failed silently; no partial/corrupt reminder row


async def test_reschedule_appointment_cancels_old_reminder_and_schedules_new(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, admin, department, practitioner, patient = await _wide_open_scenario(
        db_session, "integ-reschedule",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
    )
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id, patient_id=patient.id, practitioner_id=practitioner.id,
        department_id=department.id, start_at=_FUTURE, duration_minutes=30,
        initiated_by_user_id=admin.id,
    )
    original_reminders = await _reminders_for(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert len(original_reminders) == 1
    original_reminder_id = original_reminders[0].id

    new_start = _FUTURE + timedelta(days=5)
    await service.reschedule_appointment(
        organization_id=org.id, appointment_id=appointment.id, start_at=new_start,
        duration_minutes=30, initiated_by_user_id=admin.id,
    )

    reminders = await _reminders_for(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert len(reminders) == 2

    old = next(r for r in reminders if r.id == original_reminder_id)
    assert old.status is ReminderStatus.CANCELLED

    new = next(r for r in reminders if r.id != original_reminder_id)
    assert new.status is ReminderStatus.PENDING
    assert new.scheduled_at == new_start - timedelta(hours=24)


async def test_cancel_appointment_cancels_its_reminder(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, admin, department, practitioner, patient = await _wide_open_scenario(
        db_session, "integ-cancel",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
    )
    service = AppointmentService(db_session)
    appointment = await service.book_appointment(
        organization_id=org.id, patient_id=patient.id, practitioner_id=practitioner.id,
        department_id=department.id, start_at=_FUTURE, duration_minutes=30,
        initiated_by_user_id=admin.id,
    )

    await service.cancel_appointment(
        organization_id=org.id, appointment_id=appointment.id, initiated_by_user_id=admin.id,
    )

    reminders = await _reminders_for(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert len(reminders) == 1
    assert reminders[0].status is ReminderStatus.CANCELLED
