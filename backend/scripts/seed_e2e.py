"""One-off local/CI script: seed deterministic data for the Playwright E2E
suite (`frontend/e2e/`).

Not part of the application itself — mirrors `scripts/seed_demo_user.py`/
`scripts/seed_hackathon_demo.py`'s "one-off script, not application code"
status. Idempotent: safe to run repeatedly (e.g. once per CI run) against
a freshly migrated, empty database — every record is looked up by a fixed
slug/email/patient number before being created.

Unlike the hackathon demo script, the one workflow run seeded here goes
through the REAL `AgentOrchestrationService` with a `FakeLLMProvider`
(never a real LLM) — a genuine Coordinator -> Scheduling handoff, tool
call, and completion, with real `WorkflowEvent` rows, so
`workflow-execution.spec.ts` can assert against real data with zero LLM
dependency and zero flakiness.

Run from `backend/` with:
    python scripts/seed_e2e.py

Requires DATABASE_URL configured (via backend/.env or the environment,
same as every other script here). The admin password defaults to a fixed
synthetic constant, overridable via `E2E_ADMIN_PASSWORD` (CI sets neither
env var, so the default applies there too — safe because this seeds a
disposable, non-production, ephemeral CI/local database only).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.agents.definitions import build_default_agent_registry
from app.ai.coordinator_decisions import HandoffDecision, TargetAgent
from app.ai.decisions import ToolCallDecision
from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.fake_provider import FakeLLMProvider
from app.ai.tools.registry_builder import build_full_tool_registry
from app.auth.security import hash_password
from app.core.config import Settings
from app.db.session import build_engine
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.patient import Patient
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User
from app.models.workflow import WorkflowRequestType
from app.services.appointment import AppointmentService
from app.services.patient import PatientService

E2E_ADMIN_EMAIL = "e2e.admin@agentcare-e2e-tests.com"
E2E_ADMIN_PASSWORD_DEFAULT = "E2E-test-password-1!"
E2E_ORG_SLUG = "agentcare-e2e"
E2E_ORG_NAME = "AgentCare E2E Test Clinic"
E2E_PATIENT_NUMBER = "PN-E2E-001"


def _next_weekday_at(weekday: int, hour: int, *, weeks_ahead: int = 1) -> datetime:
    """The next `weekday` (Mon=0..Sun=6) at `hour:00 UTC`, at least
    `weeks_ahead` full weeks out — keeps every seeded appointment
    comfortably in the future regardless of when this script runs."""
    now = datetime.now(UTC)
    base = now + timedelta(weeks=weeks_ahead)
    days_ahead = (weekday - base.weekday()) % 7
    target = base + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)


async def main() -> None:
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not configured. Set it in backend/.env first.")

    admin_password = os.environ.get("E2E_ADMIN_PASSWORD", E2E_ADMIN_PASSWORD_DEFAULT)

    engine = build_engine(settings.database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Every id needed below is captured into a plain local variable the
        # moment it's available, and ONLY those local variables are used
        # from then on — never a re-dereferenced ORM attribute. The
        # `AgentOrchestrationService` call in step 4 runs several of its
        # own commits on this same session; relying on attribute access
        # against objects fetched before that (e.g. `org.id`) afterward
        # intermittently raises `MissingGreenlet` (an implicit lazy-load
        # attempted outside an awaited context) — a known rough edge of
        # SQLAlchemy's asyncio support, not something worth fighting.

        # --- 1. Organization + admin user -------------------------------
        org = (
            await session.execute(select(Organization).where(Organization.slug == E2E_ORG_SLUG))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(
                name=E2E_ORG_NAME, slug=E2E_ORG_SLUG, organization_type=OrganizationType.CLINIC
            )
            session.add(org)
            await session.flush()
        org_id = org.id

        admin = (
            await session.execute(select(User).where(User.email == E2E_ADMIN_EMAIL))
        ).scalar_one_or_none()
        if admin is None:
            admin = User(email=E2E_ADMIN_EMAIL, password_hash=hash_password(admin_password))
            session.add(admin)
            await session.flush()
            session.add(
                OrganizationMembership(organization_id=org_id, user_id=admin.id, role=Role.ADMIN)
            )
        admin_id = admin.id
        await session.commit()
        print(f"Organization: {org_id} ({E2E_ORG_SLUG})")
        print(f"Admin user: {admin_id} ({E2E_ADMIN_EMAIL})")

        # --- 2. Scheduling resources -------------------------------------
        facility = (
            await session.execute(
                select(Facility).where(Facility.organization_id == org_id, Facility.code == "E2E")
            )
        ).scalar_one_or_none()
        if facility is None:
            facility = Facility(
                organization_id=org_id,
                name="AgentCare E2E Test Facility",
                code="E2E",
                facility_type=FacilityType.CLINIC,
                timezone="UTC",
            )
            session.add(facility)
            await session.flush()
        facility_id = facility.id

        department = (
            await session.execute(
                select(Department).where(
                    Department.organization_id == org_id, Department.code == "E2EDEPT"
                )
            )
        ).scalar_one_or_none()
        if department is None:
            department = Department(
                organization_id=org_id,
                facility_id=facility_id,
                name="E2E General Medicine",
                code="E2EDEPT",
            )
            session.add(department)
            await session.flush()
        department_id = department.id

        practitioner = (
            await session.execute(
                select(Practitioner).where(
                    Practitioner.organization_id == org_id,
                    Practitioner.first_name == "Taylor",
                    Practitioner.last_name == "E2E-Test",
                )
            )
        ).scalar_one_or_none()
        if practitioner is None:
            practitioner = Practitioner(
                organization_id=org_id,
                first_name="Taylor",
                last_name="E2E-Test",
                practitioner_type=PractitionerType.PHYSICIAN,
            )
            session.add(practitioner)
            await session.flush()
            practitioner_id = practitioner.id

            session.add(
                PractitionerDepartment(
                    organization_id=org_id,
                    practitioner_id=practitioner_id,
                    department_id=department_id,
                )
            )
            # Flushed before any availability row: the composite FK on
            # `practitioner_availability` requires the assignment row to
            # already exist (see `scripts/seed_hackathon_demo.py`).
            await session.flush()

            for day in (
                DayOfWeek.MONDAY,
                DayOfWeek.TUESDAY,
                DayOfWeek.WEDNESDAY,
                DayOfWeek.THURSDAY,
                DayOfWeek.FRIDAY,
            ):
                session.add(
                    PractitionerAvailability(
                        organization_id=org_id,
                        practitioner_id=practitioner_id,
                        department_id=department_id,
                        day_of_week=day,
                        start_time=time(0, 0),
                        end_time=time(23, 59, 59),
                        timezone="UTC",
                    )
                )
        else:
            practitioner_id = practitioner.id
        await session.commit()
        print(
            f"Facility/department/practitioner: "
            f"{facility_id} / {department_id} / {practitioner_id}"
        )

        # --- 3. Patient ----------------------------------------------------
        patient_service = PatientService(session)
        patient = (
            await session.execute(
                select(Patient).where(
                    Patient.organization_id == org_id,
                    Patient.patient_number == E2E_PATIENT_NUMBER,
                )
            )
        ).scalar_one_or_none()
        if patient is None:
            patient = await patient_service.create_patient(
                organization_id=org_id,
                patient_number=E2E_PATIENT_NUMBER,
                first_name="Jordan",
                last_name="E2E-Patient",
                date_of_birth=date(1990, 1, 1),
            )
        patient_id = patient.id
        print(f"Patient: {patient_id} ({E2E_PATIENT_NUMBER})")

        # --- 4. A deterministic, real AI-orchestrated workflow -------------
        # Real orchestrator + real WorkflowService transitions, driven by a
        # FakeLLMProvider (never a real LLM call) — so
        # `workflow-execution.spec.ts` can view a genuine Coordinator ->
        # Scheduling handoff, tool invocation, and completion.
        #
        # `weeks_ahead` is offset by how many appointments this seeded
        # patient already has: every field/value here is otherwise fixed,
        # so re-running this script against a database that already has a
        # prior run's data (never happens in CI's always-fresh container,
        # but does happen for a local developer re-seeding by hand) would
        # otherwise try to book the exact same slot twice, collide, and
        # leave the "deterministic" workflow in a genuine FAILED state
        # instead of COMPLETED.
        appointment_service = AppointmentService(session)
        existing_appointments = await appointment_service.list_appointments_for_patient(
            organization_id=org_id, patient_id=patient_id
        )
        slot_offset = len(existing_appointments)

        tool_registry = build_full_tool_registry()
        agent_registry = build_default_agent_registry()
        booking_start = _next_weekday_at(0, 10, weeks_ahead=2 + slot_offset)
        provider = FakeLLMProvider(
            coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
            decision=ToolCallDecision(
                tool_name="book_appointment",
                arguments={
                    "practitioner_id": str(practitioner_id),
                    "department_id": str(department_id),
                    "start_at": booking_start.isoformat(),
                    "duration_minutes": 30,
                    "patient_id": str(patient_id),
                },
            ),
        )
        orchestration = AgentOrchestrationService(session, provider, tool_registry, agent_registry)
        result = await orchestration.execute_administrative_request(
            organization_id=org_id,
            initiated_by_user_id=admin_id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
            request_text="(e2e seed) Book a follow-up appointment.",
        )
        workflow_run_id = result.workflow_run_id
        print(f"Seeded workflow run: {workflow_run_id}")

        # --- 5. A directly booked appointment (separate from the AI one,
        # for `appointments.spec.ts` to list deterministically) -----------
        direct_start = _next_weekday_at(2, 14, weeks_ahead=2 + slot_offset)
        existing_appointments = await appointment_service.list_appointments_for_patient(
            organization_id=org_id, patient_id=patient_id
        )
        if not any(
            a.start_at == direct_start and a.practitioner_id == practitioner_id
            for a in existing_appointments
        ):
            appointment = await appointment_service.book_appointment(
                organization_id=org_id,
                patient_id=patient_id,
                practitioner_id=practitioner_id,
                department_id=department_id,
                start_at=direct_start,
                duration_minutes=30,
            )
            print(f"Seeded appointment: {appointment.id}")

        await session.commit()

        print("=" * 70)
        print("E2E SEED COMPLETE")
        print(f"Organization ID: {org_id}")
        print(f"Login: {E2E_ADMIN_EMAIL} / (E2E_ADMIN_PASSWORD or default)")
        print(f"Workflow run to view: {workflow_run_id}")
        print("=" * 70)

        # Optional machine-readable summary — consumed by
        # `frontend/e2e/global-setup.ts` so Playwright specs never have to
        # guess or re-derive a database-generated UUID (the organization
        # id, above all). Only written when a caller opts in via
        # `E2E_SEED_OUTPUT_PATH`; irrelevant for a plain manual run.
        output_path = os.environ.get("E2E_SEED_OUTPUT_PATH")
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(
                json.dumps(
                    {
                        "organization_id": str(org_id),
                        "admin_email": E2E_ADMIN_EMAIL,
                        "admin_password": admin_password,
                        "patient_id": str(patient_id),
                        "practitioner_id": str(practitioner_id),
                        "department_id": str(department_id),
                        "workflow_run_id": str(workflow_run_id),
                    },
                    indent=2,
                )
            )
            print(f"Wrote machine-readable seed summary to {output_path}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
