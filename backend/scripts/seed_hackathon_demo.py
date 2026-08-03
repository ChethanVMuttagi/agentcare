"""One-off local dev script: populate the demo organization with rich,
realistic data for hackathon screenshots/a live demo.

Not part of the application itself — mirrors `scripts/seed_demo_user.py`'s
"one-off script, not application code" status. Builds every record through
the SAME service layer (and, for the AI-driven workflows, the SAME
`AgentOrchestrationService` orchestrator) the real API endpoints use —
never a raw SQL insert of "shaped like the real thing" data — so
everything created here is indistinguishable, in the running app, from
data a real user session would have produced. The only exception is
historical ("completed"/"cancelled") appointments and a handful of
already-final approvals, which have no service-level transition to
reach that status directly (see inline notes) and are inserted via the
repository layer instead, exactly as `AppointmentService` itself would.

Run from `backend/` with:
    python scripts/seed_hackathon_demo.py

Requires DATABASE_URL configured (via backend/.env or the environment)
and `demo.admin@example.com` to already exist (see
`scripts/seed_demo_user.py`'s sibling, the ad hoc admin user created for
this hackathon submission).
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.agents.definitions import build_default_agent_registry
from app.ai.coordinator_decisions import (
    CoordinatorClarificationRequiredDecision,
    CoordinatorRequiresApprovalDecision,
    HandoffDecision,
    TargetAgent,
)
from app.ai.decisions import ToolCallDecision
from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.fake_provider import FakeLLMProvider
from app.ai.tools.registry_builder import build_full_tool_registry
from app.core.config import Settings
from app.db.session import build_engine
from app.models.appointment import Appointment, AppointmentStatus
from app.models.approval import ApprovalType
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.patient_document import DocumentType
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User
from app.models.workflow import WorkflowRequestType
from app.repositories import appointment as appointment_repository
from app.services.appointment import AppointmentService
from app.services.approval import ApprovalService
from app.services.document import PatientDocumentService
from app.services.patient import PatientService
from app.services.patient_registration import PatientRegistrationService
from app.storage.local import LocalDocumentStorage

DEMO_ADMIN_EMAIL = "demo.admin@example.com"
DEMO_ORG_SLUG = "agentcare-demo-2"

# A minimal, genuinely valid one-page PDF (signature bytes + trailer) and a
# real 1x1 PNG — both pass `app.services.document_validation.sniff_media_type`
# the same way a real upload would (magic-byte sniffing, not a trusted
# extension/content-type).
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _pdf_bytes(label: str) -> bytes:
    return (
        f"%PDF-1.4\n% Synthetic AgentCare demo document: {label}\n".encode()
        + b"synthetic administrative content " * 40
        + b"\n%%EOF"
    )


class _BytesStream:
    """A minimal `AsyncReadable` over an in-memory buffer — the same
    stand-in `tests/services/test_document.py` uses for `UploadFile`."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk


def _next_weekday_at(weekday: int, hour: int, *, weeks_ahead: int = 1) -> datetime:
    """The next `weekday` (Mon=0..Sun=6) at `hour:00 UTC`, at least
    `weeks_ahead` full weeks out — keeps every booked demo appointment
    comfortably in the future regardless of when this script is run."""
    now = datetime.now(UTC)
    base = now + timedelta(weeks=weeks_ahead)
    days_ahead = (weekday - base.weekday()) % 7
    target = base + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)


async def main() -> None:
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not configured. Set it in backend/.env first.")

    engine = build_engine(settings.database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        admin = (
            await session.execute(select(User).where(User.email == DEMO_ADMIN_EMAIL))
        ).scalar_one_or_none()
        if admin is None:
            raise SystemExit(
                f"{DEMO_ADMIN_EMAIL} does not exist yet — run the demo-user seed first."
            )
        org = (
            await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        if org is None:
            raise SystemExit(f"Organization '{DEMO_ORG_SLUG}' not found.")

        print(f"Seeding organization {org.id} ({org.name}) as {admin.email} ({admin.id})\n")

        # --- 1. Scheduling resources ------------------------------------
        facility = Facility(
            organization_id=org.id,
            name="AgentCare Demo Clinic — Main Campus",
            code="MAIN-DEMO",
            facility_type=FacilityType.CLINIC,
            timezone="UTC",
        )
        session.add(facility)
        await session.flush()

        dept_general = Department(
            organization_id=org.id, facility_id=facility.id, name="General Medicine", code="GENMED"
        )
        dept_physio = Department(
            organization_id=org.id, facility_id=facility.id, name="Physiotherapy", code="PHYSIO"
        )
        session.add_all([dept_general, dept_physio])
        await session.flush()

        practitioners_spec = [
            ("Ava", "Chen", PractitionerType.PHYSICIAN, dept_general),
            ("Marcus", "Lee", PractitionerType.PHYSICIAN, dept_general),
            ("Priya", "Nair", PractitionerType.PHYSIOTHERAPIST, dept_physio),
            ("Diego", "Alvarez", PractitionerType.THERAPIST, dept_physio),
        ]
        practitioners: list[Practitioner] = []
        for first, last, ptype, _dept in practitioners_spec:
            practitioner = Practitioner(
                organization_id=org.id, first_name=first, last_name=last, practitioner_type=ptype
            )
            session.add(practitioner)
            practitioners.append(practitioner)
        await session.flush()

        for practitioner, (_first, _last, _ptype, dept) in zip(
            practitioners, practitioners_spec, strict=True
        ):
            session.add(
                PractitionerDepartment(
                    organization_id=org.id, practitioner_id=practitioner.id, department_id=dept.id
                )
            )
        # Flushed separately, BEFORE any availability row: the
        # `practitioner_availability` composite FK requires the
        # assignment row to already exist, and SQLAlchemy has no
        # `relationship()` between these two models to infer that
        # insert order automatically (see
        # `fk_practitioner_availability_assignment`).
        await session.flush()

        for practitioner, (_first, _last, _ptype, dept) in zip(
            practitioners, practitioners_spec, strict=True
        ):
            for day in (
                DayOfWeek.MONDAY,
                DayOfWeek.TUESDAY,
                DayOfWeek.WEDNESDAY,
                DayOfWeek.THURSDAY,
                DayOfWeek.FRIDAY,
            ):
                session.add(
                    PractitionerAvailability(
                        organization_id=org.id,
                        practitioner_id=practitioner.id,
                        department_id=dept.id,
                        day_of_week=day,
                        start_time=time(9, 0),
                        end_time=time(17, 0),
                        timezone="UTC",
                    )
                )
        await session.flush()
        await session.commit()
        chen, lee, nair, alvarez = practitioners
        print(f"Created 1 facility, 2 departments, {len(practitioners)} practitioners.\n")

        # --- 2. Patients ---------------------------------------------------
        registration_service = PatientRegistrationService(session)
        patient_service = PatientService(session)

        registration_candidates = [
            ("Maria", "Gonzalez", date(1978, 3, 14), "PN-DEMO-001"),
            ("James", "O'Brien", date(1990, 11, 2), "PN-DEMO-002"),
            ("Wei", "Zhang", date(2005, 7, 22), "PN-DEMO-003"),
        ]
        direct_candidates = [
            ("Fatima", "Al-Sayed", date(1965, 1, 9), "PN-DEMO-004"),
            ("Noah", "Williams", date(2015, 5, 30), "PN-DEMO-005"),
            ("Aisha", "Bello", date(1988, 9, 17), "PN-DEMO-006"),
            ("Liam", "Murphy", date(1972, 12, 25), "PN-DEMO-007"),
            ("Sofia", "Rossi", date(1995, 4, 3), "PN-DEMO-008"),
            ("Kenji", "Tanaka", date(1983, 8, 11), "PN-DEMO-009"),
            ("Emma", "Johansson", date(2001, 2, 14), "PN-DEMO-010"),
            ("Carlos", "Mendes", date(1958, 6, 30), "PN-DEMO-011"),
            ("Grace", "Kim", date(1999, 10, 5), "PN-DEMO-012"),
            ("Samuel", "Osei", date(1945, 3, 21), "PN-DEMO-013"),
            ("Olivia", "Brown", date(2010, 12, 1), "PN-DEMO-014"),
            ("Yusuf", "Ibrahim", date(1992, 7, 19), "PN-DEMO-015"),
        ]

        patients: list[Patient] = []
        registration_workflow_ids: list[uuid.UUID] = []
        for first, last, dob, number in registration_candidates:
            run = await registration_service.start_registration(
                organization_id=org.id,
                initiated_by_user_id=admin.id,
                patient_number=number,
                first_name=first,
                last_name=last,
                date_of_birth=dob,
            )
            registration_workflow_ids.append(run.id)
            patient = (
                await session.execute(select(Patient).where(Patient.patient_number == number))
            ).scalar_one()
            patients.append(patient)

        for first, last, dob, number in direct_candidates:
            patient = await patient_service.create_patient(
                organization_id=org.id,
                patient_number=number,
                first_name=first,
                last_name=last,
                date_of_birth=dob,
            )
            patients.append(patient)

        print(
            f"Created {len(patients)} patients "
            f"({len(registration_candidates)} via Patient Registration workflow, "
            f"{len(direct_candidates)} direct)."
        )
        print(
            f"  Patient Registration workflow ids: "
            f"{[str(i) for i in registration_workflow_ids]}\n"
        )

        # --- 3. Appointments -------------------------------------------
        appointment_service = AppointmentService(session)
        booked_appointments: list[Appointment] = []

        # 7 directly booked (real service call — validates availability,
        # collision-safe) across both departments/all four practitioners.
        direct_bookings = [
            (chen, dept_general, patients[3], 0, 10, 30),
            (chen, dept_general, patients[4], 0, 14, 45),
            (lee, dept_general, patients[5], 1, 10, 30),
            (lee, dept_general, patients[6], 2, 11, 30),
            (nair, dept_physio, patients[7], 1, 15, 45),
            (nair, dept_physio, patients[8], 3, 9, 60),
            (alvarez, dept_physio, patients[9], 4, 13, 30),
        ]
        for practitioner, dept, patient, weekday, hour, duration in direct_bookings:
            start_at = _next_weekday_at(weekday, hour, weeks_ahead=1)
            appointment = await appointment_service.book_appointment(
                organization_id=org.id,
                patient_id=patient.id,
                practitioner_id=practitioner.id,
                department_id=dept.id,
                start_at=start_at,
                duration_minutes=duration,
            )
            booked_appointments.append(appointment)

        # 7 historical ("completed") appointments — no service-level
        # transition to COMPLETED exists (see AppointmentService), so
        # these are inserted directly via the repository, the same layer
        # the service itself uses, with a past start/end and status set
        # up front (never transitioned through BOOKED first).
        past_bookings = [
            (chen, dept_general, patients[0], 21, 10),
            (chen, dept_general, patients[10], 14, 11),
            (lee, dept_general, patients[11], 10, 9),
            (lee, dept_general, patients[1], 7, 15),
            (nair, dept_physio, patients[12], 28, 10),
            (nair, dept_physio, patients[13], 5, 14),
            (alvarez, dept_physio, patients[2], 3, 16),
        ]
        for practitioner, dept, patient, days_ago, hour in past_bookings:
            start_at = (datetime.now(UTC) - timedelta(days=days_ago)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            await appointment_repository.create(
                session,
                Appointment(
                    organization_id=org.id,
                    patient_id=patient.id,
                    practitioner_id=practitioner.id,
                    department_id=dept.id,
                    start_at=start_at,
                    end_at=start_at + timedelta(minutes=30),
                    status=AppointmentStatus.COMPLETED,
                ),
            )
        await session.commit()

        # 3 cancelled — book then cancel through the real service, so the
        # cancellation event/reason are produced exactly as a real
        # cancellation would be.
        cancel_bookings = [
            (chen, dept_general, patients[14], 2, 16, "Patient requested a different date."),
            (nair, dept_physio, patients[3], 3, 11, "Practitioner became unavailable."),
            (alvarez, dept_physio, patients[5], 4, 10, "Duplicate booking, cancelled by staff."),
        ]
        for practitioner, dept, patient, weekday, hour, reason in cancel_bookings:
            start_at = _next_weekday_at(weekday, hour, weeks_ahead=2)
            appointment = await appointment_service.book_appointment(
                organization_id=org.id,
                patient_id=patient.id,
                practitioner_id=practitioner.id,
                department_id=dept.id,
                start_at=start_at,
                duration_minutes=30,
            )
            await appointment_service.cancel_appointment(
                organization_id=org.id,
                appointment_id=appointment.id,
                cancellation_reason=reason,
            )

        print(
            f"Created 20 appointments: {len(direct_bookings)} booked, "
            f"{len(past_bookings)} completed, {len(cancel_bookings)} cancelled "
            "(3 more booked ones come from the AI booking workflows below).\n"
        )

        # --- 4. AI-orchestrated workflows (real orchestrator, FakeLLMProvider) ---
        tool_registry = build_full_tool_registry()
        agent_registry = build_default_agent_registry()

        def orchestration(provider: FakeLLMProvider) -> AgentOrchestrationService:
            return AgentOrchestrationService(session, provider, tool_registry, agent_registry)

        showcase_workflow_ids: dict[str, uuid.UUID] = {}

        # 3 Appointment Booking conversations (also contributes 3 more
        # real booked appointments).
        booking_conversations = [
            (
                patients[6],
                lee,
                dept_general,
                "Can you book a follow-up with Dr. Lee next week?",
                0,
                9,
            ),
            (
                patients[8],
                nair,
                dept_physio,
                "I need a physiotherapy session booked as soon as possible.",
                1,
                13,
            ),
            (
                patients[10],
                chen,
                dept_general,
                "Please schedule my annual check-up with Dr. Chen.",
                2,
                10,
            ),
        ]
        for i, (patient, practitioner, dept, prompt, weekday, hour) in enumerate(
            booking_conversations
        ):
            start_at = _next_weekday_at(weekday, hour, weeks_ahead=3)
            provider = FakeLLMProvider(
                coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
                decision=ToolCallDecision(
                    tool_name="book_appointment",
                    arguments={
                        "practitioner_id": str(practitioner.id),
                        "department_id": str(dept.id),
                        "start_at": start_at.isoformat(),
                        "duration_minutes": 30,
                        "patient_id": str(patient.id),
                    },
                ),
            )
            result = await orchestration(provider).execute_administrative_request(
                organization_id=org.id,
                initiated_by_user_id=admin.id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
                request_text=prompt,
            )
            if i == 0:
                showcase_workflow_ids["appointment_booking"] = result.workflow_run_id

        # 3 Appointment Rescheduling conversations, against real
        # already-booked appointments from step 3.
        reschedule_targets = booked_appointments[:3]
        reschedule_prompts = [
            "Can you move my appointment to a different time?",
            "I need to reschedule — something came up.",
            "Please push my appointment back a few hours.",
        ]
        for i, (appointment, prompt) in enumerate(
            zip(reschedule_targets, reschedule_prompts, strict=True)
        ):
            new_start = appointment.start_at + timedelta(hours=2)
            provider = FakeLLMProvider(
                coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
                decision=ToolCallDecision(
                    tool_name="reschedule_appointment",
                    arguments={
                        "appointment_id": str(appointment.id),
                        "start_at": new_start.isoformat(),
                        "duration_minutes": 30,
                    },
                ),
            )
            result = await orchestration(provider).execute_administrative_request(
                organization_id=org.id,
                initiated_by_user_id=admin.id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.APPOINTMENT_RESCHEDULING,
                request_text=prompt,
            )
            if i == 0:
                showcase_workflow_ids["appointment_rescheduling"] = result.workflow_run_id

        # 3 Document Collection conversations.
        doc_collection_patients = [patients[0], patients[7], patients[12]]
        doc_prompts = [
            "What documents do you already have on file for me?",
            "I want to check what paperwork is still outstanding.",
            "Can you confirm my insurance documents were received?",
        ]
        for i, (patient, prompt) in enumerate(
            zip(doc_collection_patients, doc_prompts, strict=True)
        ):
            provider = FakeLLMProvider(
                coordinator_decision=HandoffDecision(target_agent=TargetAgent.DOCUMENT),
                decision=ToolCallDecision(
                    tool_name="list_patient_documents",
                    arguments={"patient_id": str(patient.id)},
                ),
            )
            result = await orchestration(provider).execute_administrative_request(
                organization_id=org.id,
                initiated_by_user_id=admin.id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
                request_text=prompt,
            )
            if i == 0:
                showcase_workflow_ids["document_collection"] = result.workflow_run_id

        # 1 "asks a clarifying question, then resumes" conversation — a
        # second, genuinely varied conversational shape for the demo,
        # ending completed just like the rest.
        clarify_provider = FakeLLMProvider(
            coordinator_decision=CoordinatorClarificationRequiredDecision(
                message="Which practitioner would you like to see — Dr. Chen or Dr. Lee?"
            )
        )
        clarify_result = await orchestration(clarify_provider).execute_administrative_request(
            organization_id=org.id,
            initiated_by_user_id=admin.id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
            request_text="I'd like to book an appointment sometime next week.",
        )
        resume_start = _next_weekday_at(3, 11, weeks_ahead=3)
        resume_provider = FakeLLMProvider(
            coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
            decision=ToolCallDecision(
                tool_name="book_appointment",
                arguments={
                    "practitioner_id": str(chen.id),
                    "department_id": str(dept_general.id),
                    "start_at": resume_start.isoformat(),
                    "duration_minutes": 30,
                    "patient_id": str(patients[14].id),
                },
            ),
        )
        clarify_final = await orchestration(resume_provider).execute_administrative_request(
            organization_id=org.id,
            initiated_by_user_id=admin.id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
            request_text="Dr. Chen, please.",
            workflow_run_id=clarify_result.workflow_run_id,
        )
        showcase_workflow_ids["clarification_then_resume"] = clarify_final.workflow_run_id

        print(
            "Created AI-orchestrated workflows: "
            f"{len(booking_conversations)} appointment_booking, "
            f"{len(reschedule_targets)} appointment_rescheduling, "
            f"{len(doc_collection_patients)} document_collection, "
            "1 clarification-then-resume.\n"
        )

        # --- 5. Approvals: 5 pending, 5 approved, 2 rejected --------------
        approval_service = ApprovalService(session)
        approval_scenarios = [
            (ApprovalType.HIGH_RISK_ACTION, "Requested change exceeds the automatic threshold."),
            (ApprovalType.MANUAL_RESCHEDULE, "Ambiguous which practitioner the caller means."),
            (ApprovalType.DOCUMENT_EXCEPTION, "Uploaded document type does not match the request."),
            (ApprovalType.CUSTOM, "Patient details could not be confidently matched."),
            (ApprovalType.APPOINTMENT_OVERRIDE, "Requested slot conflicts with a blackout window."),
            (ApprovalType.HIGH_RISK_ACTION, "Second opinion requested before proceeding."),
            (
                ApprovalType.MANUAL_RESCHEDULE,
                "Caller requested an out-of-policy short-notice change.",
            ),
            (ApprovalType.CUSTOM, "Possible duplicate patient record detected."),
            (ApprovalType.DOCUMENT_EXCEPTION, "Document exceeds the automatic size threshold."),
            (
                ApprovalType.APPOINTMENT_OVERRIDE,
                "Requested practitioner is over capacity this week.",
            ),
            (ApprovalType.HIGH_RISK_ACTION, "Unusual request pattern flagged for review."),
            (ApprovalType.CUSTOM, "Low-confidence match against existing patient records."),
        ]
        approval_ids: list[uuid.UUID] = []
        for approval_type, reason in approval_scenarios:
            provider = FakeLLMProvider(
                coordinator_decision=CoordinatorRequiresApprovalDecision(
                    approval_type=approval_type, reason=reason
                )
            )
            result = await orchestration(provider).execute_administrative_request(
                organization_id=org.id,
                initiated_by_user_id=admin.id,
                role=Role.ADMIN,
                resolved_patient_id=None,
                request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
                request_text=f"(demo) {reason}",
            )
            assert result.approval_id is not None
            approval_ids.append(result.approval_id)

        # 5 stay PENDING (approval_ids[0:5]).
        for approval_id in approval_ids[5:10]:
            await approval_service.approve(
                organization_id=org.id,
                approval_id=approval_id,
                approved_by_user=admin.id,
                actor_identifier=str(admin.id),
            )
        for approval_id in approval_ids[10:12]:
            await approval_service.reject(
                organization_id=org.id,
                approval_id=approval_id,
                rejected_by_user=admin.id,
                actor_identifier=str(admin.id),
            )
        print(
            f"Created {len(approval_scenarios)} approvals: 5 pending, 5 approved, 2 rejected."
        )
        print(f"  Pending approval ids: {[str(i) for i in approval_ids[0:5]]}\n")

        # --- 6. Documents -------------------------------------------------
        storage = LocalDocumentStorage(settings.document_storage_path)
        document_service = PatientDocumentService(
            session, storage, max_upload_bytes=settings.document_max_upload_bytes
        )
        document_plan = [
            (patients[0], DocumentType.IDENTITY, "passport-scan.pdf", _pdf_bytes("passport")),
            (patients[0], DocumentType.INSURANCE, "insurance-card.png", _PNG_1X1),
            (patients[1], DocumentType.IDENTITY, "drivers-license.pdf", _pdf_bytes("license")),
            (patients[2], DocumentType.CONSENT, "consent-form.pdf", _pdf_bytes("consent")),
            (patients[3], DocumentType.REFERRAL, "referral-letter.pdf", _pdf_bytes("referral")),
            (patients[4], DocumentType.INSURANCE, "insurance-card.png", _PNG_1X1),
            (patients[5], DocumentType.IDENTITY, "national-id.pdf", _pdf_bytes("national id")),
            (patients[6], DocumentType.CONSENT, "consent-form.pdf", _pdf_bytes("consent")),
            (patients[7], DocumentType.REFERRAL, "referral-letter.pdf", _pdf_bytes("referral")),
            (patients[8], DocumentType.OTHER, "intake-notes.pdf", _pdf_bytes("intake notes")),
            (patients[9], DocumentType.IDENTITY, "passport-scan.pdf", _pdf_bytes("passport")),
            (patients[10], DocumentType.INSURANCE, "insurance-card.png", _PNG_1X1),
            (patients[12], DocumentType.REFERRAL, "referral-letter.pdf", _pdf_bytes("referral")),
            (patients[13], DocumentType.CONSENT, "consent-form.pdf", _pdf_bytes("consent")),
        ]
        for patient, doc_type, filename, content in document_plan:
            await document_service.upload_document(
                organization_id=org.id,
                patient_id=patient.id,
                uploaded_by_user_id=admin.id,
                document_type=doc_type,
                original_filename=filename,
                stream=_BytesStream(content),
            )
        distinct_patients = len({p.id for p, *_ in document_plan})
        print(
            f"Uploaded {len(document_plan)} documents across "
            f"{distinct_patients} patients.\n"
        )

        await session.commit()

        # --- Summary --------------------------------------------------------
        best_patient = patients[0]
        print("=" * 70)
        print("DEMO DATA SEEDED SUCCESSFULLY")
        print("=" * 70)
        print(f"Organization ID: {org.id}")
        print(f"Login: {DEMO_ADMIN_EMAIL} (see scripts/seed_demo_user.py output for password)")
        print()
        print("Best workflow to showcase (AI booking, real handoff + tool call):")
        print(f"  {showcase_workflow_ids.get('appointment_booking')}")
        print("Clarification -> resume -> completed workflow (richest trace):")
        print(f"  {showcase_workflow_ids.get('clarification_then_resume')}")
        print(f"Best patient to showcase: {best_patient.first_name} {best_patient.last_name}")
        print(f"  Patient ID: {best_patient.id}")
        print("=" * 70)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
