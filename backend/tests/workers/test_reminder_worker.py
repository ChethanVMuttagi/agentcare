"""`app.workers.reminder_worker.ReminderWorker` tests against real,
genuinely committed PostgreSQL state.

Like `tests/db/test_workflow_concurrency.py`/`tests/db/test_appointment_concurrency.py`,
these tests deliberately open their OWN dedicated engine/sessionmaker
rather than using the shared savepoint-isolated `db_session` fixture:
`ReminderWorker` opens its own sessions per claim/per-reminder (exactly
as it does in production), and the mandatory concurrency proof requires
genuinely independent connections racing each other. Setup data is
really committed; synthetic rows are explicitly deleted in `finally`
blocks.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/workers/test_reminder_worker.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import Reminder, ReminderAttempt, ReminderStatus, ReminderType
from app.models.user import User
from app.models.workflow import WorkflowEvent, WorkflowRun, WorkflowStatus, WorkflowStep
from app.notifications.fake import AlwaysRaisingFakeNotificationProvider, FakeNotificationProvider
from app.repositories import reminder as reminder_repository
from app.services.reminder import ReminderService
from app.workers.reminder_worker import ReminderWorker

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)

_PAST = datetime.now(UTC) - timedelta(minutes=10)


@dataclass(frozen=True)
class _Scenario:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    patient_id: uuid.UUID
    appointment_id: uuid.UUID
    reminder_ids: list[uuid.UUID] = field(default_factory=list)


async def _setup(
    session_factory: async_sessionmaker[AsyncSession],
    suffix: str,
    *,
    reminder_count: int = 1,
    max_attempts: int = 5,
) -> _Scenario:
    """Create and COMMIT a genuinely persisted scenario: one org, one
    active admin member, one patient, one booked appointment, and
    `reminder_count` due (`scheduled_at` in the past) `PENDING`
    reminders for it — each with its own real `WorkflowRun`/`WorkflowStep`
    via `ReminderService.schedule_reminder`."""
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Reminder Worker Org {suffix}",
            slug=f"synthetic-reminder-worker-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        user = User(
            email=f"synthetic.reminder.worker.{suffix}@example.com",
            password_hash="not-a-real-hash",
        )
        session.add(user)
        await session.flush()

        session.add(
            OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.ADMIN)
        )
        await session.flush()

        facility = Facility(
            organization_id=org.id,
            name=f"Synthetic Facility {suffix}",
            code=f"FAC-{suffix}",
            facility_type=FacilityType.HOSPITAL,
            timezone="UTC",
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

        patient = Patient(
            organization_id=org.id, patient_number=f"PN-{suffix}",
            first_name="Synthetic", last_name="Patient", date_of_birth=datetime(1990, 1, 1).date(),
        )
        session.add(patient)
        await session.flush()

        appointment = Appointment(
            organization_id=org.id, patient_id=patient.id, practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=datetime.now(UTC) + timedelta(days=1),
            end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
            status=AppointmentStatus.BOOKED,
        )
        session.add(appointment)
        await session.flush()
        await session.commit()

        reminder_ids: list[uuid.UUID] = []
        service = ReminderService(session)
        for i in range(reminder_count):
            reminder = await service.schedule_reminder(
                organization_id=org.id,
                appointment_id=appointment.id,
                patient_id=patient.id,
                reminder_type=ReminderType.APPOINTMENT_REMINDER,
                scheduled_at=_PAST - timedelta(seconds=i),
                initiated_by_user_id=user.id,
                max_attempts=max_attempts,
            )
            reminder_ids.append(reminder.id)

        return _Scenario(
            organization_id=org.id,
            user_id=user.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            reminder_ids=reminder_ids,
        )


async def _teardown(session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario) -> None:
    """Explicitly delete every synthetic row this test created, in
    FK-safe (child-before-parent) order."""
    async with session_factory() as session:
        await session.execute(
            delete(ReminderAttempt).where(
                ReminderAttempt.organization_id == scenario.organization_id
            )
        )
        await session.execute(
            delete(Reminder).where(Reminder.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(WorkflowEvent).where(
                WorkflowEvent.organization_id == scenario.organization_id
            )
        )
        await session.execute(
            delete(WorkflowStep).where(WorkflowStep.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(WorkflowRun).where(WorkflowRun.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Appointment).where(Appointment.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(PractitionerDepartment).where(
                PractitionerDepartment.organization_id == scenario.organization_id
            )
        )
        await session.execute(
            delete(Practitioner).where(Practitioner.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Department).where(Department.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Facility).where(Facility.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Patient).where(Patient.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == scenario.organization_id
            )
        )
        await session.execute(delete(User).where(User.id == scenario.user_id))
        await session.execute(
            delete(Organization).where(Organization.id == scenario.organization_id)
        )
        await session.commit()


async def test_run_once_processes_due_reminder_and_marks_sent() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        provider = FakeNotificationProvider()
        worker = ReminderWorker(session_factory, provider, worker_id="worker-sent")

        claimed_count = await worker.run_once()
        assert claimed_count == 1

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.SENT
            assert reminder.sent_at is not None
            assert reminder.locked_at is None
            assert reminder.locked_by is None

            run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == reminder.workflow_run_id)
                )
            ).scalar_one()
            assert run.status is WorkflowStatus.COMPLETED

            attempts = (
                (
                    await session.execute(
                        select(ReminderAttempt).where(
                            ReminderAttempt.reminder_id == reminder.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(attempts) == 1
            assert attempts[0].status.value == "sent"
            assert attempts[0].provider_name == "fake"

        assert len(provider.sent_messages) == 1
        assert provider.sent_messages[0].reminder_id == scenario.reminder_ids[0]
        assert provider.sent_messages[0].patient_id == scenario.patient_id
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_run_once_retries_then_succeeds_on_a_later_poll() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8], max_attempts=5)

    try:
        failing_provider = FakeNotificationProvider(
            always_succeed=False, fail_detail="provider down"
        )
        failing_worker = ReminderWorker(
            session_factory, failing_provider, worker_id="worker-fail"
        )
        await failing_worker.run_once()

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.PENDING  # retried, not exhausted
            assert reminder.attempt_count == 1
            assert reminder.last_error == "provider down"

            run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == reminder.workflow_run_id)
                )
            ).scalar_one()
            assert run.status is WorkflowStatus.RUNNING  # still in progress

        succeeding_provider = FakeNotificationProvider()
        succeeding_worker = ReminderWorker(
            session_factory, succeeding_provider, worker_id="worker-succeed"
        )
        await succeeding_worker.run_once()

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.SENT
            assert reminder.attempt_count == 2

            attempts = (
                (
                    await session.execute(
                        select(ReminderAttempt)
                        .where(ReminderAttempt.reminder_id == reminder.id)
                        .order_by(ReminderAttempt.attempt_number)
                    )
                )
                .scalars()
                .all()
            )
            assert [a.status.value for a in attempts] == ["failed", "sent"]
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_run_once_exhausts_retries_and_marks_permanently_failed() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8], max_attempts=1)

    try:
        provider = FakeNotificationProvider(always_succeed=False, fail_detail="permanent outage")
        worker = ReminderWorker(session_factory, provider, worker_id="worker-exhaust")
        await worker.run_once()

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.FAILED
            assert reminder.last_error == "permanent outage"

            run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == reminder.workflow_run_id)
                )
            ).scalar_one()
            assert run.status is WorkflowStatus.FAILED
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_run_once_treats_unexpected_provider_exception_as_a_failed_attempt() -> None:
    """The provider raising (not returning a controlled failure result)
    must never crash the worker's poll loop — it's treated exactly like
    an ordinary delivery failure, with a safe, generic error recorded
    (never the raw exception)."""
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8], max_attempts=1)

    try:
        provider = AlwaysRaisingFakeNotificationProvider()
        worker = ReminderWorker(session_factory, provider, worker_id="worker-raises")
        claimed_count = await worker.run_once()
        assert claimed_count == 1  # the batch claim itself is unaffected

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.FAILED
            assert "RuntimeError" not in (reminder.last_error or "")
            assert "simulated unexpected provider failure" not in (reminder.last_error or "")

            attempts = (
                (
                    await session.execute(
                        select(ReminderAttempt).where(
                            ReminderAttempt.reminder_id == reminder.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert attempts[0].status.value == "failed"
            assert "RuntimeError" not in (attempts[0].safe_error_message or "")
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_run_once_recovers_an_abandoned_lock() -> None:
    """Simulates a worker that crashed mid-attempt: the reminder is left
    `PROCESSING` with a stale `locked_at`. A later poll (by ANY worker)
    must recover and successfully complete it — never leave it stuck
    forever."""
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        async with session_factory() as session:
            await reminder_repository.mark_processing(
                session,
                organization_id=scenario.organization_id,
                reminder_id=scenario.reminder_ids[0],
                worker_id="dead-worker",
                locked_at=datetime.now(UTC) - timedelta(hours=1),
            )
            await session.commit()

        provider = FakeNotificationProvider()
        worker = ReminderWorker(
            session_factory,
            provider,
            worker_id="recovery-worker",
            lock_timeout=timedelta(minutes=5),
        )
        claimed_count = await worker.run_once()
        assert claimed_count == 1

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.SENT
            assert reminder.attempt_count == 2  # original abandoned attempt + the recovery
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_process_one_skips_a_reminder_cancelled_after_being_claimed() -> None:
    """Proves the narrow-but-real race `ReminderService.mark_started`
    closes: a reminder cancelled AFTER `acquire_pending` claimed it (but
    BEFORE delivery actually starts) must never be sent. Calls the
    worker's internal `_process_one` directly to deterministically
    reproduce exactly this ordering — `run_once()` cannot express
    "claim, then something else happens, then process" as one call."""
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        async with session_factory() as session:
            now = datetime.now(UTC)
            await reminder_repository.acquire_pending(
                session,
                now=now,
                stale_before=now - timedelta(minutes=5),
                worker_id="claim-worker",
                batch_size=10,
            )
            await session.commit()

        async with session_factory() as session:
            service = ReminderService(session)
            await service.cancel_reminder(
                organization_id=scenario.organization_id, reminder_id=scenario.reminder_ids[0]
            )

        provider = FakeNotificationProvider()
        worker = ReminderWorker(session_factory, provider, worker_id="claim-worker")
        await worker._process_one(scenario.organization_id, scenario.reminder_ids[0])

        assert provider.sent_messages == []  # never actually attempted delivery

        async with session_factory() as session:
            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.id == scenario.reminder_ids[0])
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.CANCELLED  # untouched by the worker
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_run_once_respects_batch_size_across_many_due_reminders() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8], reminder_count=5)

    try:
        provider = FakeNotificationProvider()
        worker = ReminderWorker(
            session_factory, provider, worker_id="worker-batch", batch_size=2
        )
        claimed_count = await worker.run_once()
        assert claimed_count == 2

        async with session_factory() as session:
            sent = (
                (
                    await session.execute(
                        select(Reminder).where(
                            Reminder.id.in_(scenario.reminder_ids),
                            Reminder.status == ReminderStatus.SENT,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(sent) == 2
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_concurrent_workers_never_double_process_the_same_reminder() -> None:
    """THE mandatory concurrency proof: two independent `ReminderWorker`
    instances, each with their OWN connection pool, poll the SAME due
    reminders CONCURRENTLY (`asyncio.gather`). `SELECT ... FOR UPDATE
    SKIP LOCKED` must guarantee every reminder is claimed and delivered
    by EXACTLY ONE of them — proven against real, concurrently-executing
    transactions, not sequential calls."""
    assert _POSTGRES_TEST_URL is not None
    reminder_count = 6
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=10, max_overflow=10)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(
        session_factory, uuid.uuid4().hex[:8], reminder_count=reminder_count
    )

    try:
        provider_a = FakeNotificationProvider()
        provider_b = FakeNotificationProvider()
        worker_a = ReminderWorker(
            session_factory, provider_a, worker_id="concurrent-a", batch_size=10
        )
        worker_b = ReminderWorker(
            session_factory, provider_b, worker_id="concurrent-b", batch_size=10
        )

        claimed_a, claimed_b = await asyncio.gather(worker_a.run_once(), worker_b.run_once())

        assert claimed_a + claimed_b == reminder_count, (
            f"expected all {reminder_count} reminders claimed exactly once between "
            f"the two workers, got a={claimed_a} b={claimed_b}"
        )

        sent_ids_a = {m.reminder_id for m in provider_a.sent_messages}
        sent_ids_b = {m.reminder_id for m in provider_b.sent_messages}
        assert sent_ids_a.isdisjoint(sent_ids_b), "no reminder may ever be sent by BOTH workers"
        assert sent_ids_a | sent_ids_b == set(scenario.reminder_ids)

        async with session_factory() as session:
            reminders = (
                (
                    await session.execute(
                        select(Reminder).where(Reminder.id.in_(scenario.reminder_ids))
                    )
                )
                .scalars()
                .all()
            )
            assert all(r.status is ReminderStatus.SENT for r in reminders)
            assert all(r.attempt_count == 1 for r in reminders)
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
