"""`app.repositories.reminder` tests against real PostgreSQL.

Covers tenant-scoped CRUD/lookup, `get_by_id_for_update`'s row lock, and
`acquire_pending` — the `SELECT ... FOR UPDATE SKIP LOCKED` primitive the
whole reminder engine's concurrency safety is built on (real concurrent-
claim proof lives in `tests/db/test_reminder_worker_concurrency.py`;
this file covers its single-connection selection/mutation logic:
due-PENDING selection, stale-PROCESSING recovery, batch-size limiting,
and ordering).
"""

from __future__ import annotations

import uuid
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
from app.models.reminder import Reminder, ReminderStatus
from app.models.user import User
from app.models.workflow import WorkflowRun, WorkflowStep
from app.repositories import reminder as reminder_repository

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
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> tuple[Organization, User, Appointment, Patient, WorkflowRun, WorkflowStep]:
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
    return org, admin, appointment, patient, run, step


async def test_create_and_get_by_id(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-create",
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
    )
    reminder = await make_reminder(org, appointment, patient, run, step)

    fetched = await reminder_repository.get_by_id(
        db_session, organization_id=org.id, reminder_id=reminder.id
    )
    assert fetched is not None
    assert fetched.id == reminder.id
    assert fetched.status is ReminderStatus.PENDING


async def test_get_by_id_returns_none_for_wrong_organization(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-wrong-org",
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
    )
    reminder = await make_reminder(org, appointment, patient, run, step)

    other_org = await make_organization("repo-rem-wrong-org-other")
    fetched = await reminder_repository.get_by_id(
        db_session, organization_id=other_org.id, reminder_id=reminder.id
    )
    assert fetched is None


async def test_get_by_id_for_update_locks_and_returns_the_row(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-lock",
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
    )
    reminder = await make_reminder(org, appointment, patient, run, step)

    locked = await reminder_repository.get_by_id_for_update(
        db_session, organization_id=org.id, reminder_id=reminder.id
    )
    assert locked is not None
    assert locked.id == reminder.id


async def test_list_by_appointment_filters_by_status(
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
) -> None:
    org, admin, appointment, patient, run, step = await _scenario(
        "repo-rem-list-appt",
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
    )
    pending = await make_reminder(
        org, appointment, patient, run, step, status=ReminderStatus.PENDING
    )
    run2 = await make_workflow_run(org, admin.id, patient=appointment.patient)
    step2 = await make_workflow_step(org, run2, 1, step_type="reminder_delivery")
    sent = await make_reminder(
        org, appointment, patient, run2, step2, status=ReminderStatus.SENT
    )

    only_pending = await reminder_repository.list_by_appointment(
        db_session,
        organization_id=org.id,
        appointment_id=appointment.id,
        statuses=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
    )
    assert [r.id for r in only_pending] == [pending.id]

    every_status = await reminder_repository.list_by_appointment(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )
    assert {r.id for r in every_status} == {pending.id, sent.id}


async def test_list_by_patient_scoped_to_one_patient(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-list-patient",
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
    )
    reminder = await make_reminder(org, appointment, patient, run, step)

    other_patient = await make_patient(org, "PN-repo-rem-list-patient-other")
    results = await reminder_repository.list_by_patient(
        db_session, organization_id=org.id, patient_id=patient.id
    )
    assert [r.id for r in results] == [reminder.id]

    empty = await reminder_repository.list_by_patient(
        db_session, organization_id=org.id, patient_id=other_patient.id
    )
    assert empty == []


async def test_acquire_pending_claims_due_reminders_and_sets_lock(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-acquire-due",
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
    )
    due = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    not_yet_due = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
    )

    now = datetime.now(UTC)
    claimed = await reminder_repository.acquire_pending(
        db_session,
        now=now,
        stale_before=now - timedelta(minutes=5),
        worker_id="worker-a",
        batch_size=10,
    )

    claimed_ids = {r.id for r in claimed}
    assert due.id in claimed_ids
    assert not_yet_due.id not in claimed_ids

    claimed_due = next(r for r in claimed if r.id == due.id)
    assert claimed_due.status is ReminderStatus.PROCESSING
    assert claimed_due.locked_by == "worker-a"
    assert claimed_due.locked_at == now
    assert claimed_due.attempt_count == 1


async def test_acquire_pending_recovers_stale_locks(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-acquire-stale",
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
    )
    abandoned = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC) - timedelta(hours=1),
        locked_by="dead-worker",
        attempt_count=1,
    )
    fresh_processing = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC),
        locked_by="live-worker",
        attempt_count=1,
    )

    now = datetime.now(UTC)
    claimed = await reminder_repository.acquire_pending(
        db_session,
        now=now,
        stale_before=now - timedelta(minutes=5),
        worker_id="recovery-worker",
        batch_size=10,
    )

    claimed_ids = {r.id for r in claimed}
    assert abandoned.id in claimed_ids, "an abandoned (stale-locked) reminder must be recovered"
    assert fresh_processing.id not in claimed_ids, "a live worker's lock must never be stolen"

    recovered = next(r for r in claimed if r.id == abandoned.id)
    assert recovered.locked_by == "recovery-worker"
    assert recovered.attempt_count == 2


async def test_acquire_pending_never_claims_cancelled_or_sent_reminders(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-acquire-terminal",
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
    )
    past = datetime.now(UTC) - timedelta(minutes=5)
    cancelled = await make_reminder(
        org, appointment, patient, run, step,
        status=ReminderStatus.CANCELLED, scheduled_at=past,
    )
    sent = await make_reminder(
        org, appointment, patient, run, step,
        status=ReminderStatus.SENT, scheduled_at=past,
    )
    failed = await make_reminder(
        org, appointment, patient, run, step,
        status=ReminderStatus.FAILED, scheduled_at=past,
    )

    now = datetime.now(UTC)
    claimed = await reminder_repository.acquire_pending(
        db_session, now=now, stale_before=now - timedelta(minutes=5), worker_id="w", batch_size=10
    )
    claimed_ids = {r.id for r in claimed}
    assert cancelled.id not in claimed_ids
    assert sent.id not in claimed_ids
    assert failed.id not in claimed_ids


async def test_acquire_pending_respects_batch_size_and_orders_by_scheduled_at(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-acquire-batch",
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
    )
    now = datetime.now(UTC)
    earliest = await make_reminder(
        org, appointment, patient, run, step, scheduled_at=now - timedelta(minutes=30)
    )
    middle = await make_reminder(
        org, appointment, patient, run, step, scheduled_at=now - timedelta(minutes=20)
    )
    await make_reminder(
        org, appointment, patient, run, step, scheduled_at=now - timedelta(minutes=10)
    )

    claimed = await reminder_repository.acquire_pending(
        db_session, now=now, stale_before=now - timedelta(minutes=5), worker_id="w", batch_size=2
    )
    assert len(claimed) == 2
    assert [r.id for r in claimed] == [earliest.id, middle.id]


async def test_mark_processing_locks_and_increments_attempt_count(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-mark-processing",
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
    )
    reminder = await make_reminder(
        org, appointment, patient, run, step, status=ReminderStatus.FAILED
    )

    locked_at = datetime.now(UTC)
    result = await reminder_repository.mark_processing(
        db_session,
        organization_id=org.id,
        reminder_id=reminder.id,
        worker_id="retry-worker",
        locked_at=locked_at,
    )
    assert result is not None
    assert result.status is ReminderStatus.PROCESSING
    assert result.locked_by == "retry-worker"
    assert result.attempt_count == 1


async def test_mark_processing_returns_none_for_unknown_reminder(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-rem-mark-processing-unknown")
    result = await reminder_repository.mark_processing(
        db_session,
        organization_id=org.id,
        reminder_id=uuid.uuid4(),
        worker_id="w",
        locked_at=datetime.now(UTC),
    )
    assert result is None


async def test_mark_sent_clears_lock_and_error(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-mark-sent",
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
    )
    reminder = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC),
        locked_by="w",
        last_error="previous attempt failed",
    )

    sent_at = datetime.now(UTC)
    result = await reminder_repository.mark_sent(
        db_session, organization_id=org.id, reminder_id=reminder.id, sent_at=sent_at
    )
    assert result is not None
    assert result.status is ReminderStatus.SENT
    assert result.sent_at == sent_at
    assert result.locked_at is None
    assert result.locked_by is None
    assert result.last_error is None


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
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_reminder: MakeReminder,
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-mark-failed-retry",
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
    )
    reminder = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC),
        locked_by="w",
        attempt_count=1,
        max_attempts=5,
    )

    result = await reminder_repository.mark_failed(
        db_session,
        organization_id=org.id,
        reminder_id=reminder.id,
        safe_error="provider unavailable",
        next_status=ReminderStatus.PENDING,
    )
    assert result is not None
    assert result.status is ReminderStatus.PENDING
    assert result.last_error == "provider unavailable"
    assert result.locked_at is None


async def test_mark_failed_truncates_oversized_error_message(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-mark-failed-oversized",
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
    )
    reminder = await make_reminder(
        org, appointment, patient, run, step, status=ReminderStatus.PROCESSING
    )

    result = await reminder_repository.mark_failed(
        db_session,
        organization_id=org.id,
        reminder_id=reminder.id,
        safe_error="x" * 1000,
        next_status=ReminderStatus.FAILED,
    )
    assert result is not None
    assert len(result.last_error) == 500


async def test_cancel_clears_lock_and_sets_cancelled_at(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-cancel",
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
    )
    reminder = await make_reminder(
        org,
        appointment,
        appointment.patient,
        run,
        step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC),
        locked_by="w",
    )

    cancelled_at = datetime.now(UTC)
    result = await reminder_repository.cancel(
        db_session, organization_id=org.id, reminder_id=reminder.id, cancelled_at=cancelled_at
    )
    assert result is not None
    assert result.status is ReminderStatus.CANCELLED
    assert result.cancelled_at == cancelled_at
    assert result.locked_at is None
    assert result.locked_by is None


async def test_find_expired_locks_returns_only_stale_processing_rows(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-expired-locks",
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
    )
    stale = await make_reminder(
        org, appointment, patient, run, step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC) - timedelta(hours=1),
        locked_by="dead",
    )
    fresh = await make_reminder(
        org, appointment, patient, run, step,
        status=ReminderStatus.PROCESSING,
        locked_at=datetime.now(UTC),
        locked_by="alive",
    )
    pending = await make_reminder(
        org, appointment, patient, run, step, status=ReminderStatus.PENDING
    )

    result = await reminder_repository.find_expired_locks(
        db_session, stale_before=datetime.now(UTC) - timedelta(minutes=5)
    )
    result_ids = {r.id for r in result}
    assert stale.id in result_ids
    assert fresh.id not in result_ids
    assert pending.id not in result_ids


async def test_update_flushes_mutated_attributes(
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
) -> None:
    org, _admin, appointment, patient, run, step = await _scenario(
        "repo-rem-update",
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
    )
    reminder = await make_reminder(org, appointment, patient, run, step)

    new_time = datetime.now(UTC) + timedelta(days=2)
    reminder.scheduled_at = new_time
    updated = await reminder_repository.update(db_session, reminder)
    assert updated.scheduled_at == new_time

    refetched = await reminder_repository.get_by_id(
        db_session, organization_id=org.id, reminder_id=reminder.id
    )
    assert refetched is not None
    assert refetched.scheduled_at == new_time
