"""Mandatory real-PostgreSQL end-to-end proofs for STORY-015's workflow
templates — the full chain, never trusting an intermediate layer's
return value alone: every assertion re-queries the database directly.

    Patient Registration (no duplicate)  -> patient created -> workflow
                                             completed -> verified

    Patient Registration (soft duplicate) -> paused for approval
                                             -> approved -> workflow
                                             completed, no second
                                             patient created -> verified

    Appointment Booking via Coordinator  -> Scheduling specialist books
                                             -> reminder AUTOMATICALLY
                                             scheduled -> verified

    Appointment Rescheduling via         -> Scheduling specialist
    Coordinator                             reschedules -> OLD reminder
                                             cancelled, NEW one scheduled
                                             -> verified

    Coordinator clarification -> pause   -> follow-up resumes the SAME
                                             run -> handoff -> tool call
                                             -> completed -> verified

Uses its own dedicated engine/sessionmaker (see
`tests/db/test_workflow_concurrency.py`'s module docstring for why) —
genuinely committed, independently-queryable state across multiple real
sessions, mirroring `tests/db/test_reminder_e2e.py`/`tests/db/test_approval_e2e.py`'s
established pattern.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_workflow_templates_e2e.py
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.agents.definitions import build_default_agent_registry
from app.ai.coordinator_decisions import (
    CoordinatorClarificationRequiredDecision,
    HandoffDecision,
    TargetAgent,
)
from app.ai.decisions import ToolCallDecision
from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.fake_provider import FakeLLMProvider
from app.ai.tools.registry_builder import build_full_tool_registry
from app.models.appointment import Appointment
from app.models.approval import ApprovalRequest
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.patient import Patient
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.reminder import Reminder, ReminderStatus
from app.models.user import User
from app.models.workflow import (
    WorkflowEvent,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.services.appointment import AppointmentService
from app.services.approval import ApprovalService
from app.services.patient_registration import PatientRegistrationService

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
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Templates E2E Org {suffix}",
            slug=f"synthetic-templates-e2e-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        user = User(
            email=f"synthetic.templates.e2e.{suffix}@example.com", password_hash="not-a-real-hash"
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


async def _teardown(session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario) -> None:
    async with session_factory() as session:
        # FK-safe (child-before-parent) order: ApprovalRequest/Reminder/
        # WorkflowEvent reference WorkflowStep/WorkflowRun, which in turn
        # reference OrganizationMembership (`initiated_by_user_id`) — so
        # OrganizationMembership must be deleted AFTER WorkflowRun, not
        # swept up in the same generic loop as the other organization-
        # scoped models.
        for model in (
            ApprovalRequest,
            Reminder,
            WorkflowEvent,
            WorkflowStep,
            WorkflowRun,
            Appointment,
            PractitionerAvailability,
            PractitionerDepartment,
            Practitioner,
            Department,
            Facility,
            Patient,
            OrganizationMembership,
        ):
            await session.execute(
                delete(model).where(model.organization_id == scenario.organization_id)
            )
        await session.execute(delete(User).where(User.id == scenario.user_id))
        await session.execute(
            delete(Organization).where(Organization.id == scenario.organization_id)
        )
        await session.commit()


async def test_patient_registration_no_duplicate_creates_patient_and_completes() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        async with session_factory() as session:
            service = PatientRegistrationService(session)
            run = await service.start_registration(
                organization_id=scenario.organization_id,
                initiated_by_user_id=scenario.user_id,
                patient_number=f"PN-NEW-{scenario.organization_id.hex[:6]}",
                first_name="Devon",
                last_name="Reyes",
                date_of_birth=datetime(1997, 3, 3).date(),
            )
            run_id = run.id

        async with session_factory() as session:
            run_row = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run_row.status is WorkflowStatus.COMPLETED
            assert run_row.patient_id is None

            patient_row = (
                await session.execute(
                    select(Patient).where(
                        Patient.organization_id == scenario.organization_id,
                        Patient.first_name == "Devon",
                        Patient.last_name == "Reyes",
                    )
                )
            ).scalar_one()
            assert patient_row.date_of_birth == datetime(1997, 3, 3).date()
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_patient_registration_soft_duplicate_approved_never_creates_second_patient() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        async with session_factory() as session:
            original = (
                await session.execute(
                    select(Patient).where(Patient.id == scenario.patient_id)
                )
            ).scalar_one()
            first_name, last_name, dob = (
                original.first_name,
                original.last_name,
                original.date_of_birth,
            )

        async with session_factory() as session:
            service = PatientRegistrationService(session)
            run = await service.start_registration(
                organization_id=scenario.organization_id,
                initiated_by_user_id=scenario.user_id,
                patient_number=f"PN-DUP-{scenario.organization_id.hex[:6]}",
                first_name=first_name,
                last_name=last_name,
                date_of_birth=dob,
            )
            run_id = run.id
            assert run.status is WorkflowStatus.WAITING

        async with session_factory() as session:
            approval = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run_id)
                )
            ).scalar_one()
            approval_id = approval.id

        async with session_factory() as session:
            approval_service = ApprovalService(session)
            await approval_service.approve(
                organization_id=scenario.organization_id,
                approval_id=approval_id,
                approved_by_user=scenario.user_id,
                actor_identifier=str(scenario.user_id),
            )

        async with session_factory() as session:
            run_row = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run_row.status is WorkflowStatus.COMPLETED

            patients = (
                (
                    await session.execute(
                        select(Patient).where(
                            Patient.organization_id == scenario.organization_id,
                            Patient.first_name == first_name,
                            Patient.last_name == last_name,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(patients) == 1, "no second patient was ever created"
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_appointment_booking_via_coordinator_schedules_a_reminder() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        start_at = datetime.now(UTC) + timedelta(hours=2)
        async with session_factory() as session:
            provider = FakeLLMProvider(
                coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
                decision=ToolCallDecision(
                    tool_name="book_appointment",
                    arguments={
                        "practitioner_id": str(scenario.practitioner_id),
                        "department_id": str(scenario.department_id),
                        "start_at": start_at.isoformat(),
                        "duration_minutes": 30,
                        "patient_id": str(scenario.patient_id),
                    },
                ),
            )
            orchestration = AgentOrchestrationService(
                session, provider, build_full_tool_registry(), build_default_agent_registry()
            )
            result = await orchestration.execute_administrative_request(
                organization_id=scenario.organization_id,
                initiated_by_user_id=scenario.user_id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
                request_text="Book an appointment",
            )
            assert result.workflow_status is WorkflowStatus.COMPLETED
            appointment_id = uuid.UUID(result.tool_result_data["appointment_id"])

        async with session_factory() as session:
            steps = (
                (
                    await session.execute(
                        select(WorkflowStep)
                        .where(WorkflowStep.workflow_run_id == result.workflow_run_id)
                        .order_by(WorkflowStep.sequence_number)
                    )
                )
                .scalars()
                .all()
            )
            assert [s.step_type for s in steps] == ["coordination", "specialist_execution"]

            reminder = (
                await session.execute(
                    select(Reminder).where(Reminder.appointment_id == appointment_id)
                )
            ).scalar_one()
            assert reminder.status is ReminderStatus.PENDING
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_appointment_rescheduling_via_coordinator_replaces_the_reminder() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        original_start = datetime.now(UTC) + timedelta(hours=2)
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

        new_start = original_start + timedelta(hours=3)
        async with session_factory() as session:
            provider = FakeLLMProvider(
                coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
                decision=ToolCallDecision(
                    tool_name="reschedule_appointment",
                    arguments={
                        "appointment_id": str(appointment_id),
                        "start_at": new_start.isoformat(),
                        "duration_minutes": 30,
                    },
                ),
            )
            orchestration = AgentOrchestrationService(
                session, provider, build_full_tool_registry(), build_default_agent_registry()
            )
            result = await orchestration.execute_administrative_request(
                organization_id=scenario.organization_id,
                initiated_by_user_id=scenario.user_id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.APPOINTMENT_RESCHEDULING,
                request_text="Reschedule my appointment",
            )
            assert result.workflow_status is WorkflowStatus.COMPLETED
            assert result.tool_name == "reschedule_appointment"

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

            appointment_row = (
                await session.execute(
                    select(Appointment).where(Appointment.id == appointment_id)
                )
            ).scalar_one()
            assert appointment_row.start_at == new_start
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_clarification_pause_then_resume_then_handoff_completes() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        async with session_factory() as session:
            provider = FakeLLMProvider(
                coordinator_decision=CoordinatorClarificationRequiredDecision(
                    message="Which department?"
                )
            )
            orchestration = AgentOrchestrationService(
                session, provider, build_full_tool_registry(), build_default_agent_registry()
            )
            paused = await orchestration.execute_administrative_request(
                organization_id=scenario.organization_id,
                initiated_by_user_id=scenario.user_id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
                request_text="Help me",
            )
            assert paused.workflow_status is WorkflowStatus.WAITING
            run_id = paused.workflow_run_id

        async with session_factory() as session:
            run_row = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run_row.status is WorkflowStatus.WAITING

        async with session_factory() as session:
            provider = FakeLLMProvider(
                coordinator_decision=HandoffDecision(target_agent=TargetAgent.DOCUMENT),
                decision=ToolCallDecision(
                    tool_name="list_patient_documents",
                    arguments={"patient_id": str(scenario.patient_id)},
                ),
            )
            orchestration = AgentOrchestrationService(
                session, provider, build_full_tool_registry(), build_default_agent_registry()
            )
            resumed = await orchestration.execute_administrative_request(
                organization_id=scenario.organization_id,
                initiated_by_user_id=scenario.user_id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
                request_text="Check documents on file",
                workflow_run_id=run_id,
            )
            assert resumed.workflow_run_id == run_id
            assert resumed.workflow_status is WorkflowStatus.COMPLETED
            assert resumed.handled_by_agent == "document"

        async with session_factory() as session:
            run_row = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run_row.status is WorkflowStatus.COMPLETED

            events = (
                (
                    await session.execute(
                        select(WorkflowEvent)
                        .where(WorkflowEvent.workflow_run_id == run_id)
                        .order_by(WorkflowEvent.sequence)
                    )
                )
                .scalars()
                .all()
            )
            assert [e.event_type.value for e in events] == [
                "workflow_created",
                "workflow_started",
                "step_started",
                "step_waiting",
                "workflow_waiting",
                "step_resumed",
                "workflow_resumed",
                "agent_handoff",
                "step_completed",
                "step_started",
                "tool_invoked",
                "step_completed",
                "workflow_completed",
            ]
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
