"""`app.services.reminder_scheduler.ReminderScheduler` tests against
real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import ReminderStatus, ReminderType
from app.models.user import User
from app.repositories import reminder as reminder_repository
from app.services.reminder_scheduler import DEFAULT_LEAD_TIME, ReminderScheduler

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeAppointment = Callable[..., Awaitable[Appointment]]

_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def _scenario(
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
    make_appointment: MakeAppointment,
    start_at: datetime | None = None,
) -> tuple[Organization, User, Appointment]:
    org = await make_organization(suffix)
    admin = await make_user(suffix)
    await make_membership(org, admin, role=Role.ADMIN)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")
    appointment = await make_appointment(
        org, patient, practitioner, department, start_at=start_at or _FUTURE
    )
    return org, admin, appointment


async def test_schedule_appointment_reminder_uses_default_lead_time(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, admin, appointment = await _scenario(
        "sched-default-lead",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
        make_appointment=make_appointment,
    )
    scheduler = ReminderScheduler(db_session)

    reminder = await scheduler.schedule_appointment_reminder(
        appointment, initiated_by_user_id=admin.id
    )

    assert reminder.reminder_type is ReminderType.APPOINTMENT_REMINDER
    assert reminder.scheduled_at == appointment.start_at - DEFAULT_LEAD_TIME
    assert reminder.appointment_id == appointment.id
    assert reminder.patient_id == appointment.patient_id
    assert reminder.payload == {"appointment_start_at": appointment.start_at.isoformat()}


async def test_schedule_appointment_reminder_respects_custom_lead_time(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, admin, appointment = await _scenario(
        "sched-custom-lead",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
        make_appointment=make_appointment,
    )
    scheduler = ReminderScheduler(db_session, lead_time=timedelta(hours=2))

    reminder = await scheduler.schedule_appointment_reminder(
        appointment, initiated_by_user_id=admin.id
    )
    assert reminder.scheduled_at == appointment.start_at - timedelta(hours=2)


async def test_schedule_appointment_reminder_for_soon_starting_appointment_is_scheduled_in_past(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    """An appointment starting sooner than the lead time still gets a
    reminder — scheduled in the near past, so the worker picks it up on
    its very next poll rather than the reminder never existing."""
    soon = datetime.now(UTC) + timedelta(hours=1)
    org, admin, appointment = await _scenario(
        "sched-soon",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
        make_appointment=make_appointment,
        start_at=soon,
    )
    scheduler = ReminderScheduler(db_session)

    reminder = await scheduler.schedule_appointment_reminder(
        appointment, initiated_by_user_id=admin.id
    )
    assert reminder.scheduled_at < datetime.now(UTC)


async def test_cancel_appointment_reminders_cancels_only_cancellable_ones(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, admin, appointment = await _scenario(
        "sched-cancel",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
        make_appointment=make_appointment,
    )
    scheduler = ReminderScheduler(db_session)
    reminder = await scheduler.schedule_appointment_reminder(
        appointment, initiated_by_user_id=admin.id
    )

    cancelled = await scheduler.cancel_appointment_reminders(
        appointment, initiated_by_user_id=admin.id
    )
    assert [r.id for r in cancelled] == [reminder.id]

    refetched = await reminder_repository.get_by_id(
        db_session, organization_id=org.id, reminder_id=reminder.id
    )
    assert refetched is not None
    assert refetched.status is ReminderStatus.CANCELLED
