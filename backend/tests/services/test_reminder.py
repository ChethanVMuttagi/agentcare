"""`app.services.reminder.ReminderService` tests against real PostgreSQL.

Proves the reminder lifecycle is fully integrated with the SAME
workflow engine every other AgentCare capability uses: every reminder
owns its own `WorkflowRun`/`WorkflowStep`, and every meaningful moment
(scheduled/started/sent/failed/cancelled) is a real, persisted
`WorkflowEvent` — never a parallel, undiscoverable side channel.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import Reminder, ReminderStatus, ReminderType
from app.models.user import User
from app.models.workflow import WorkflowRequestType, WorkflowStatus
from app.repositories import reminder as reminder_repository
from app.repositories import workflow_event as workflow_event_repository
from app.repositories import workflow_run as workflow_run_repository
from app.repositories import workflow_step as workflow_step_repository
from app.services.reminder import (
    InvalidReminderTransitionError,
    ReminderNotFoundError,
    ReminderService,
)
from app.services.workflow import WorkflowInitiatorNotActiveMemberError

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
) -> tuple[Organization, User, Patient, Appointment]:
    org = await make_organization(suffix)
    admin = await make_user(suffix)
    await make_membership(org, admin, role=Role.ADMIN)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")
    appointment = await make_appointment(org, patient, practitioner, department)
    return org, admin, patient, appointment


async def _events(
    db_session: AsyncSession, *, organization_id: uuid.UUID, run_id: uuid.UUID
) -> list[str]:
    events = await workflow_event_repository.list_by_run(
        db_session, organization_id=organization_id, workflow_run_id=run_id
    )
    return [e.event_type.value for e in events]


async def test_schedule_reminder_creates_workflow_run_step_and_event(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-schedule",
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
    service = ReminderService(db_session)

    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE - timedelta(hours=24),
        initiated_by_user_id=admin.id,
        payload={"appointment_start_at": _FUTURE.isoformat()},
    )

    assert reminder.status is ReminderStatus.PENDING
    assert reminder.workflow_run_id is not None
    assert reminder.workflow_step_id is not None

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.PENDING
    assert run.request_type is WorkflowRequestType.REMINDER_DELIVERY
    assert run.patient_id == patient.id

    steps = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert len(steps) == 1
    assert steps[0].step_type == "reminder_delivery"

    event_types = await _events(
        db_session, organization_id=org.id, run_id=reminder.workflow_run_id
    )
    assert event_types == ["workflow_created", "reminder_scheduled"]


async def test_schedule_reminder_requires_active_initiator_membership(
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
    org, _admin, patient, appointment = await _scenario(
        "svc-rem-schedule-inactive",
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
    service = ReminderService(db_session)

    with pytest.raises(WorkflowInitiatorNotActiveMemberError):
        await service.schedule_reminder(
            organization_id=org.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            reminder_type=ReminderType.APPOINTMENT_REMINDER,
            scheduled_at=_FUTURE,
            initiated_by_user_id=uuid.uuid4(),
        )


async def test_get_reminder_not_found(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-rem-not-found")
    service = ReminderService(db_session)
    with pytest.raises(ReminderNotFoundError):
        await service.get_reminder(organization_id=org.id, reminder_id=uuid.uuid4())


async def test_cancel_reminder_pending_cancels_run_too(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-cancel-pending",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )

    cancelled = await service.cancel_reminder(organization_id=org.id, reminder_id=reminder.id)
    assert cancelled.status is ReminderStatus.CANCELLED
    assert cancelled.cancelled_at is not None

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.CANCELLED

    event_types = await _events(
        db_session, organization_id=org.id, run_id=reminder.workflow_run_id
    )
    assert event_types == [
        "workflow_created",
        "reminder_scheduled",
        "workflow_cancelled",
        "reminder_cancelled",
    ]


async def test_cancel_reminder_rejects_already_terminal_reminder(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-cancel-terminal",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )
    await service.cancel_reminder(organization_id=org.id, reminder_id=reminder.id)

    with pytest.raises(InvalidReminderTransitionError):
        await service.cancel_reminder(organization_id=org.id, reminder_id=reminder.id)


async def test_cancel_reminders_for_appointment_only_touches_cancellable_ones(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-cancel-for-appt",
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
    service = ReminderService(db_session)
    pending = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )
    sent = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )
    await reminder_repository.mark_processing(
        db_session,
        organization_id=org.id,
        reminder_id=sent.id,
        worker_id="w",
        locked_at=datetime.now(UTC),
    )
    await db_session.commit()
    await service.mark_started(
        organization_id=org.id, reminder_id=sent.id, actor_identifier="w"
    )
    await service.mark_sent(organization_id=org.id, reminder_id=sent.id, actor_identifier="w")

    cancelled_list = await service.cancel_reminders_for_appointment(
        organization_id=org.id, appointment_id=appointment.id
    )
    assert [r.id for r in cancelled_list] == [pending.id]

    refetched_sent = await service.get_reminder(organization_id=org.id, reminder_id=sent.id)
    assert refetched_sent.status is ReminderStatus.SENT  # untouched


async def test_reschedule_reminder_moves_scheduled_at_in_place(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-reschedule",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )
    original_run_id = reminder.workflow_run_id

    new_time = _FUTURE + timedelta(days=1)
    rescheduled = await service.reschedule_reminder(
        organization_id=org.id, reminder_id=reminder.id, scheduled_at=new_time
    )
    assert rescheduled.id == reminder.id
    assert rescheduled.scheduled_at == new_time
    assert rescheduled.workflow_run_id == original_run_id  # same run, in place

    event_types = await _events(db_session, organization_id=org.id, run_id=original_run_id)
    assert event_types == ["workflow_created", "reminder_scheduled", "reminder_scheduled"]


async def test_reschedule_reminder_rejects_non_pending(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-reschedule-invalid",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )
    await service.cancel_reminder(organization_id=org.id, reminder_id=reminder.id)

    with pytest.raises(InvalidReminderTransitionError):
        await service.reschedule_reminder(
            organization_id=org.id, reminder_id=reminder.id, scheduled_at=_FUTURE
        )


async def _force_exhausted_failure(
    service: ReminderService,
    db_session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reminder_id: uuid.UUID,
    actor_identifier: str,
) -> Reminder:
    """Drive a reminder straight to `FAILED` via a single exhausted
    attempt (`max_attempts=1`, set by the caller before this helper
    runs)."""
    await reminder_repository.mark_processing(
        db_session,
        organization_id=organization_id,
        reminder_id=reminder_id,
        worker_id=actor_identifier,
        locked_at=datetime.now(UTC),
    )
    await db_session.commit()
    await service.mark_started(
        organization_id=organization_id, reminder_id=reminder_id, actor_identifier=actor_identifier
    )
    return await service.mark_failed(
        organization_id=organization_id,
        reminder_id=reminder_id,
        safe_error="provider unavailable",
        actor_identifier=actor_identifier,
    )


async def test_mark_started_transitions_run_and_step_only_on_first_attempt(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-mark-started",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
        max_attempts=5,
    )
    run_id = reminder.workflow_run_id

    # Attempt 1: claim, start -> run/step PENDING -> RUNNING.
    await reminder_repository.mark_processing(
        db_session, organization_id=org.id, reminder_id=reminder.id, worker_id="w1",
        locked_at=datetime.now(UTC),
    )
    await db_session.commit()
    await service.mark_started(
        organization_id=org.id, reminder_id=reminder.id, actor_identifier="w1"
    )

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.RUNNING

    # Fail (retry) then start attempt 2: run must STAY RUNNING, not be
    # re-transitioned (which would raise WorkflowConflictError were it
    # attempted) — only a fresh REMINDER_STARTED audit event is added.
    await service.mark_failed(
        organization_id=org.id,
        reminder_id=reminder.id,
        safe_error="transient",
        actor_identifier="w1",
    )
    await reminder_repository.mark_processing(
        db_session, organization_id=org.id, reminder_id=reminder.id, worker_id="w2",
        locked_at=datetime.now(UTC),
    )
    await db_session.commit()
    await service.mark_started(
        organization_id=org.id, reminder_id=reminder.id, actor_identifier="w2"
    )

    run_after_retry = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=run_id
    )
    assert run_after_retry is not None
    assert run_after_retry.status is WorkflowStatus.RUNNING

    event_types = await _events(db_session, organization_id=org.id, run_id=run_id)
    assert event_types == [
        "workflow_created",
        "reminder_scheduled",
        "workflow_started",
        "step_started",
        "reminder_started",
        "reminder_failed",
        "reminder_started",
    ]


async def test_mark_started_rejects_reminder_not_currently_processing(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-mark-started-invalid",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )

    with pytest.raises(InvalidReminderTransitionError):
        await service.mark_started(
            organization_id=org.id, reminder_id=reminder.id, actor_identifier="w"
        )

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.PENDING  # never started


async def test_mark_sent_completes_run_and_step(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-mark-sent",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )
    await reminder_repository.mark_processing(
        db_session, organization_id=org.id, reminder_id=reminder.id, worker_id="w",
        locked_at=datetime.now(UTC),
    )
    await db_session.commit()
    await service.mark_started(
        organization_id=org.id, reminder_id=reminder.id, actor_identifier="w"
    )

    sent = await service.mark_sent(
        organization_id=org.id, reminder_id=reminder.id, actor_identifier="w"
    )
    assert sent.status is ReminderStatus.SENT
    assert sent.sent_at is not None

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.COMPLETED

    steps = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert steps[0].status.value == "completed"

    event_types = await _events(db_session, organization_id=org.id, run_id=reminder.workflow_run_id)
    assert event_types == [
        "workflow_created",
        "reminder_scheduled",
        "workflow_started",
        "step_started",
        "reminder_started",
        "reminder_sent",
        "step_completed",
        "workflow_completed",
    ]


async def test_mark_failed_retries_when_attempts_remain(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-mark-failed-retry",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
        max_attempts=5,
    )
    await reminder_repository.mark_processing(
        db_session, organization_id=org.id, reminder_id=reminder.id, worker_id="w",
        locked_at=datetime.now(UTC),
    )
    await db_session.commit()
    await service.mark_started(
        organization_id=org.id, reminder_id=reminder.id, actor_identifier="w"
    )

    failed = await service.mark_failed(
        organization_id=org.id, reminder_id=reminder.id, safe_error="timeout", actor_identifier="w"
    )
    assert failed.status is ReminderStatus.PENDING  # retried, not terminal
    assert failed.last_error == "timeout"

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.RUNNING  # still in progress, not failed


async def test_mark_failed_exhausted_fails_run_and_step(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-mark-failed-exhausted",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
        max_attempts=1,
    )
    failed = await _force_exhausted_failure(
        service, db_session, organization_id=org.id, reminder_id=reminder.id, actor_identifier="w"
    )
    assert failed.status is ReminderStatus.FAILED

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.FAILED
    assert run.failure_code == "reminder_delivery_failed"

    steps = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=reminder.workflow_run_id
    )
    assert steps[0].status.value == "failed"


async def test_retry_failed_creates_new_workflow_run_and_grants_one_more_attempt(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-retry-failed",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
        max_attempts=1,
    )
    original_run_id = reminder.workflow_run_id
    await _force_exhausted_failure(
        service, db_session, organization_id=org.id, reminder_id=reminder.id, actor_identifier="w"
    )

    retried = await service.retry_failed(
        organization_id=org.id, reminder_id=reminder.id, initiated_by_user_id=admin.id
    )
    assert retried.status is ReminderStatus.PENDING
    assert retried.max_attempts == 2
    assert retried.last_error is None
    assert retried.workflow_run_id != original_run_id

    # The ORIGINAL run stays FAILED forever — a truthful historical
    # record, never resurrected.
    original_run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=original_run_id
    )
    assert original_run is not None
    assert original_run.status is WorkflowStatus.FAILED

    new_run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=retried.workflow_run_id
    )
    assert new_run is not None
    assert new_run.status is WorkflowStatus.PENDING


async def test_retry_failed_rejects_non_failed_reminder(
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
    org, admin, patient, appointment = await _scenario(
        "svc-rem-retry-invalid",
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
    service = ReminderService(db_session)
    reminder = await service.schedule_reminder(
        organization_id=org.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        reminder_type=ReminderType.APPOINTMENT_REMINDER,
        scheduled_at=_FUTURE,
        initiated_by_user_id=admin.id,
    )

    with pytest.raises(InvalidReminderTransitionError):
        await service.retry_failed(
            organization_id=org.id, reminder_id=reminder.id, initiated_by_user_id=admin.id
        )
