"""`app.repositories.reminder_attempt` tests against real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import Reminder, ReminderAttempt, ReminderAttemptStatus
from app.models.user import User
from app.models.workflow import WorkflowRun, WorkflowStep
from app.repositories import reminder_attempt as reminder_attempt_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeAppointment = Callable[..., Awaitable[Appointment]]
MakeWorkflowRun = Callable[..., Awaitable[WorkflowRun]]
MakeWorkflowStep = Callable[..., Awaitable[WorkflowStep]]
MakeReminder = Callable[..., Awaitable[Reminder]]
MakeReminderAttempt = Callable[..., Awaitable[ReminderAttempt]]


async def _reminder_scenario(
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
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_reminder: MakeReminder,
) -> tuple[Organization, Reminder]:
    org = await make_organization(suffix)
    admin = await make_user(suffix)
    await make_membership(org, admin, role=Role.ADMIN)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")
    appointment = await make_appointment(org, patient, practitioner, department)
    run = await make_workflow_run(org, admin.id, patient=patient)
    step = await make_workflow_step(org, run, 1, step_type="reminder_delivery")
    reminder = await make_reminder(org, appointment, patient, run, step)
    return org, reminder


async def test_create_and_list_by_reminder(
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
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_reminder: MakeReminder,
    make_reminder_attempt: MakeReminderAttempt,
) -> None:
    org, reminder = await _reminder_scenario(
        "repo-rem-attempt-create",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
        make_appointment=make_appointment,
        make_workflow_run=make_workflow_run,
        make_workflow_step=make_workflow_step,
        make_reminder=make_reminder,
    )
    first = await make_reminder_attempt(
        org, reminder, attempt_number=1, status=ReminderAttemptStatus.FAILED,
        safe_error_message="provider unavailable",
    )
    second = await make_reminder_attempt(
        org, reminder, attempt_number=2, status=ReminderAttemptStatus.SENT
    )

    results = await reminder_attempt_repository.list_by_reminder(
        db_session, organization_id=org.id, reminder_id=reminder.id
    )
    assert [a.id for a in results] == [first.id, second.id]
    assert results[0].safe_error_message == "provider unavailable"
    assert results[1].safe_error_message is None


async def test_list_by_reminder_is_tenant_scoped(
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
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_reminder: MakeReminder,
    make_reminder_attempt: MakeReminderAttempt,
) -> None:
    org, reminder = await _reminder_scenario(
        "repo-rem-attempt-tenant",
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        make_facility=make_facility,
        make_department=make_department,
        make_practitioner=make_practitioner,
        make_practitioner_department=make_practitioner_department,
        make_patient=make_patient,
        make_appointment=make_appointment,
        make_workflow_run=make_workflow_run,
        make_workflow_step=make_workflow_step,
        make_reminder=make_reminder,
    )
    await make_reminder_attempt(org, reminder)

    other_org = await make_organization("repo-rem-attempt-tenant-other")
    results = await reminder_attempt_repository.list_by_reminder(
        db_session, organization_id=other_org.id, reminder_id=reminder.id
    )
    assert results == []
