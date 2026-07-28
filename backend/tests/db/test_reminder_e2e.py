"""Mandatory real-PostgreSQL end-to-end proofs for the reminder engine
(STORY-013) — the full chain, never trusting an intermediate layer's
return value alone: every assertion re-queries the database directly.

    Appointment booked      -> Reminder persisted -> Worker executes
                             -> Reminder marked sent -> Workflow updated
                             -> Database verified

    Appointment cancelled   -> Reminder cancelled -> Worker skips
                             -> Verified

    Reschedule              -> Old reminder cancelled -> New reminder
                                created -> Worker processes only the
                                latest -> Verified

Uses its own dedicated engine/sessionmaker (see
`tests/workers/test_reminder_worker.py`'s module docstring for why) —
`AppointmentService`/`ReminderWorker` both need genuinely committed,
independently-queryable state across multiple real sessions.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_reminder_e2e.py
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.patient import Patient
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import Reminder, ReminderAttempt, ReminderStatus
from app.models.user import User
from app.models.workflow import WorkflowEvent, WorkflowRun, WorkflowStatus, WorkflowStep
from app.notifications.fake import FakeNotificationProvider
from app.services.appointment import AppointmentService
from app.workers.reminder_worker import ReminderWorker

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)


@dataclass(frozen=True)
class _Scenario:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    patient_id: uuid.UUID
    practitioner_id: uuid.UUID
    department_id: uuid.UUID


async def _setup(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> _Scenario:
    """Create and COMMIT a genuinely persisted, fully bookable scenario
    (org/admin/patient/practitioner/department/wide-open availability) —
    NO appointment yet; each test books its own via `AppointmentService`,
    exactly as the real product does."""
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Reminder E2E Org {suffix}",
            slug=f"synthetic-reminder-e2e-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        user = User(
            email=f"synthetic.reminder.e2e.{suffix}@example.com", password_hash="not-a-real-hash"
        )
        session.add(user)
        await session.flush()

        session.add(
            OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.ADMIN)
        )
        await session.flush()

        facility = Facility(
            organization_id=org.id, name=f"Synthetic Facility {suffix}", code=f"FAC-{suffix}",
            facility_type=FacilityType.HOSPITAL, timezone="UTC",
        )
        session.add(facility)
        await session.flush()

        department = Department(
            organization_id=org.id, facility_id=facility.id,
            name=f"Synthetic Department {suffix}", code=f"DEPT-{suffix}",
        )
        session.add(department)
        await session.flush()

        practitioner = Practitioner(
            organization_id=org.id, first_name="Synthetic", last_name="Practitioner",
            practitioner_type=PractitionerType.PHYSICIAN,
        )
        session.add(practitioner)
        await session.flush()

        session.add(
            PractitionerDepartment(
                organization_id=org.id, practitioner_id=practitioner.id,
                department_id=department.id,
            )
        )
        await session.flush()

        for day in DayOfWeek:
            session.add(
                PractitionerAvailability(
                    organization_id=org.id, practitioner_id=practitioner.id,
                    department_id=department.id, day_of_week=day,
                    start_time=time(0, 0), end_time=time(23, 59, 59), timezone="UTC",
                )
            )
        await session.flush()

        patient = Patient(
            organization_id=org.id, patient_number=f"PN-{suffix}",
            first_name="Synthetic", last_name="Patient", date_of_birth=datetime(1990, 1, 1).date(),
        )
        session.add(patient)
        await session.flush()

        await session.commit()

        return _Scenario(
            organization_id=org.id, user_id=user.id, patient_id=patient.id,
            practitioner_id=practitioner.id, department_id=department.id,
        )


async def _teardown(
    session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario
) -> None:
    async with session_factory() as session:
        for model in (ReminderAttempt, Reminder, WorkflowEvent, WorkflowStep, WorkflowRun,
                      Appointment, PractitionerAvailability, PractitionerDepartment,
                      Practitioner, Department, Facility, Patient, OrganizationMembership):
            await session.execute(
                delete(model).where(model.organization_id == scenario.organization_id)
            )
        await session.execute(delete(User).where(User.id == scenario.user_id))
        await session.execute(
            delete(Organization).where(Organization.id == scenario.organization_id)
        )
        await session.commit()


async def test_booked_appointment_reminder_is_delivered_and_workflow_completed() -> None:
    """Appointment booked -> Reminder persisted -> Worker executes ->
    Reminder marked sent -> Workflow updated -> Database verified."""
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        # An appointment starting sooner than the 24h default lead time
        # -> its reminder is scheduled in the near past, so the worker
        # picks it up immediately (see `ReminderScheduler`).
        start_at = datetime.now(UTC) + timedelta(hours=1)
        async with session_factory() as session:
            appointment_service = AppointmentService(session)
            appointment = await appointment_service.book_appointment(
                organization_id=scenario.organization_id,
                patient_id=scenario.patient_id,
                practitioner_id=scenario.practitioner_id,
                department_id=scenario.department_id,
                start_at=start_at,
                duration_minutes=30,
                initiated_by_user_id=scenario.user_id,
            )
            appointment_id = appointment.id

        # 1. Reminder genuinely persisted, PENDING, in the database —
        #    never trusting the service call's return value alone.
        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.appointment_id == appointment_id)
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.PENDING
            reminder_id = reminder.id
            workflow_run_id = reminder.workflow_run_id

        # 2. Worker executes.
        provider = FakeNotificationProvider()
        worker = ReminderWorker(session_factory, provider, worker_id="e2e-booked-worker")
        claimed = await worker.run_once()
        assert claimed == 1

        # 3. Reminder marked sent — re-queried directly, not trusting
        #    the worker's internal state.
        async with session_factory() as session:
            reminder = (
                await session.execute(select(Reminder).where(Reminder.id == reminder_id))
            ).scalar_one()
            assert reminder.status is ReminderStatus.SENT
            assert reminder.sent_at is not None

            # 4. Workflow updated: run COMPLETED, full event chain present.
            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == workflow_run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.COMPLETED

            events = (
                (
                    await session.execute(
                        select(WorkflowEvent)
                        .where(WorkflowEvent.workflow_run_id == workflow_run_id)
                        .order_by(WorkflowEvent.sequence)
                    )
                )
                .scalars()
                .all()
            )
            assert [e.event_type.value for e in events] == [
                "workflow_created",
                "reminder_scheduled",
                "workflow_started",
                "step_started",
                "reminder_started",
                "reminder_sent",
                "step_completed",
                "workflow_completed",
            ]

            # 5. The appointment itself is untouched by any of this.
            appointment_row = (
                await session.execute(select(Appointment).where(Appointment.id == appointment_id))
            ).scalar_one()
            assert appointment_row.status is AppointmentStatus.BOOKED

        assert len(provider.sent_messages) == 1
        assert provider.sent_messages[0].appointment_id == appointment_id
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_cancelled_appointment_reminder_is_cancelled_and_worker_skips_it() -> None:
    """Appointment cancelled -> Reminder cancelled -> Worker skips ->
    Verified."""
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        start_at = datetime.now(UTC) + timedelta(hours=1)
        async with session_factory() as session:
            appointment_service = AppointmentService(session)
            appointment = await appointment_service.book_appointment(
                organization_id=scenario.organization_id,
                patient_id=scenario.patient_id,
                practitioner_id=scenario.practitioner_id,
                department_id=scenario.department_id,
                start_at=start_at,
                duration_minutes=30,
                initiated_by_user_id=scenario.user_id,
            )
            appointment_id = appointment.id

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.appointment_id == appointment_id)
                )
            ).scalar_one()
            reminder_id = reminder.id
            assert reminder.status is ReminderStatus.PENDING

        # Cancel the appointment -> its reminder must be cancelled too.
        async with session_factory() as session:
            appointment_service = AppointmentService(session)
            await appointment_service.cancel_appointment(
                organization_id=scenario.organization_id,
                appointment_id=appointment_id,
                initiated_by_user_id=scenario.user_id,
            )

        async with session_factory() as session:
            reminder = (
                await session.execute(select(Reminder).where(Reminder.id == reminder_id))
            ).scalar_one()
            assert reminder.status is ReminderStatus.CANCELLED

        # Worker polls — must find NOTHING due (the reminder is no
        # longer PENDING) and must never deliver it.
        provider = FakeNotificationProvider()
        worker = ReminderWorker(session_factory, provider, worker_id="e2e-cancelled-worker")
        claimed = await worker.run_once()
        assert claimed == 0
        assert provider.sent_messages == []

        async with session_factory() as session:
            reminder = (
                await session.execute(select(Reminder).where(Reminder.id == reminder_id))
            ).scalar_one()
            assert reminder.status is ReminderStatus.CANCELLED  # unchanged by the worker

            run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == reminder.workflow_run_id)
                )
            ).scalar_one()
            assert run.status is WorkflowStatus.CANCELLED
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_rescheduled_appointment_only_the_new_reminder_is_delivered() -> None:
    """Reschedule -> Old reminder cancelled -> New reminder created ->
    Worker processes only the latest -> Verified."""
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        original_start = datetime.now(UTC) + timedelta(hours=1)
        async with session_factory() as session:
            appointment_service = AppointmentService(session)
            appointment = await appointment_service.book_appointment(
                organization_id=scenario.organization_id,
                patient_id=scenario.patient_id,
                practitioner_id=scenario.practitioner_id,
                department_id=scenario.department_id,
                start_at=original_start,
                duration_minutes=30,
                initiated_by_user_id=scenario.user_id,
            )
            appointment_id = appointment.id

        async with session_factory() as session:
            original_reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.appointment_id == appointment_id)
                )
            ).scalar_one()
            original_reminder_id = original_reminder.id

        # Reschedule to a new (still soon) time.
        new_start = datetime.now(UTC) + timedelta(hours=2)
        async with session_factory() as session:
            appointment_service = AppointmentService(session)
            await appointment_service.reschedule_appointment(
                organization_id=scenario.organization_id,
                appointment_id=appointment_id,
                start_at=new_start,
                duration_minutes=30,
                initiated_by_user_id=scenario.user_id,
            )

        async with session_factory() as session:
            reminders = (
                (
                    await session.execute(
                        select(Reminder).where(Reminder.appointment_id == appointment_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(reminders) == 2
            old = next(r for r in reminders if r.id == original_reminder_id)
            new = next(r for r in reminders if r.id != original_reminder_id)
            assert old.status is ReminderStatus.CANCELLED
            assert new.status is ReminderStatus.PENDING
            new_reminder_id = new.id

        # Worker polls: must claim and send ONLY the new reminder, never
        # the cancelled old one.
        provider = FakeNotificationProvider()
        worker = ReminderWorker(session_factory, provider, worker_id="e2e-reschedule-worker")
        claimed = await worker.run_once()
        assert claimed == 1

        assert len(provider.sent_messages) == 1
        assert provider.sent_messages[0].reminder_id == new_reminder_id

        async with session_factory() as session:
            old = (
                await session.execute(select(Reminder).where(Reminder.id == original_reminder_id))
            ).scalar_one()
            assert old.status is ReminderStatus.CANCELLED  # untouched

            new = (
                await session.execute(select(Reminder).where(Reminder.id == new_reminder_id))
            ).scalar_one()
            assert new.status is ReminderStatus.SENT
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
