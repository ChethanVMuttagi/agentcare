"""Appointment model tests against real PostgreSQL.

Covers the model/DB layer: id generation, CHECK constraints, composite-FK
tenant/assignment ownership integrity, and the two EXCLUDE constraints
(sequential proof that the constraint itself rejects/allows the expected
cases — see tests/db/test_appointment_concurrency.py for the mandatory
GENUINE concurrency proof, which this file does not attempt to
duplicate). See tests/conftest.py for why these require
AGENTCARE_TEST_POSTGRES_URL and how test data is guaranteed not to
persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeAppointment = Callable[..., Awaitable[Appointment]]

_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def _scenario(
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    suffix: str,
) -> tuple[Organization, Department, Practitioner, Patient]:
    org = await make_organization(suffix)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")
    return org, department, practitioner, patient


async def test_appointment_id_is_generated_uuid(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-uuid",
    )

    appointment = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(appointment)
    await db_session.flush()

    assert isinstance(appointment.id, uuid.UUID)


async def test_appointment_start_must_be_before_end(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-bad-range",
    )

    appointment = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE - timedelta(minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(appointment)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_appointment_rejects_cross_tenant_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    """`(organization_id, patient_id)` composite FK must reject a patient
    belonging to a DIFFERENT organization — proven at the database level,
    not merely by service-level validation."""
    org_a, department_a, practitioner_a, _patient_a = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-cross-a",
    )
    org_b = await make_organization("appt-cross-b")
    patient_b = await make_patient(org_b, "PN-appt-cross-b")

    appointment = Appointment(
        organization_id=org_a.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner_a.id,
        department_id=department_a.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(appointment)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_appointment_requires_an_existing_practitioner_department_assignment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_patient: MakePatient,
) -> None:
    """No `PractitionerDepartment` row exists for this pairing — the
    composite assignment FK must reject it, mirroring
    `PractitionerAvailability`."""
    org = await make_organization("appt-no-assignment")
    facility = await make_facility(org, "appt-no-assignment")
    department = await make_department(org, facility, "APPT-NO-ASSIGN")
    practitioner = await make_practitioner(org)  # never assigned
    patient = await make_patient(org, "PN-appt-no-assignment")

    appointment = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(appointment)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_appointment_status_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    # Proves the CHECK constraint is real, database-level enforcement.
    org, department, practitioner, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-raw-status",
    )

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO appointments "
                "(id, organization_id, patient_id, practitioner_id, department_id, "
                "start_at, end_at, status, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :patient_id, :practitioner_id, "
                ":department_id, now() + interval '1 day', now() + interval '1 day 30 minutes', "
                "'bogus_status', now(), now())"
            ),
            {
                "org_id": org.id,
                "patient_id": patient.id,
                "practitioner_id": practitioner.id,
                "department_id": department.id,
            },
        )
    await db_session.rollback()


async def test_appointment_status_defaults_to_booked(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-default-status",
    )

    appointment = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
    )
    db_session.add(appointment)
    await db_session.flush()

    assert appointment.status is AppointmentStatus.BOOKED


async def test_appointment_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-timestamps",
    )

    appointment = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
    )
    db_session.add(appointment)
    await db_session.flush()

    assert appointment.created_at is not None
    assert appointment.updated_at is not None
    assert appointment.created_at.tzinfo is not None
    assert appointment.start_at.tzinfo is not None


async def test_appointment_relationships(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-relationships",
    )
    appointment = await make_appointment(org, patient, practitioner, department)

    await db_session.refresh(
        appointment, attribute_names=["patient", "practitioner", "department", "organization"]
    )
    assert appointment.patient.id == patient.id
    assert appointment.practitioner.id == practitioner.id
    assert appointment.department.id == department.id
    assert appointment.organization.id == org.id


# --- EXCLUDE constraints (sequential proof; see
# tests/db/test_appointment_concurrency.py for the genuine-concurrency proof) --


async def test_overlapping_practitioner_booking_rejected_by_exclusion_constraint(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner, patient_a = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-excl-prac",
    )
    patient_b = await make_patient(org, "PN-appt-excl-prac-b")
    await make_appointment(
        org, patient_a, practitioner, department, start_at=_FUTURE, duration_minutes=30
    )

    overlapping = Appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE + timedelta(minutes=15),
        end_at=_FUTURE + timedelta(minutes=45),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(overlapping)

    with pytest.raises(IntegrityError, match="ex_appointments_practitioner_no_overlap"):
        await db_session.flush()
    await db_session.rollback()


async def test_adjacent_practitioner_booking_is_allowed(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner, patient_a = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-adjacent",
    )
    patient_b = await make_patient(org, "PN-appt-adjacent-b")
    await make_appointment(
        org, patient_a, practitioner, department, start_at=_FUTURE, duration_minutes=30
    )

    adjacent = Appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE + timedelta(minutes=30),
        end_at=_FUTURE + timedelta(minutes=60),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(adjacent)
    await db_session.flush()  # must NOT raise

    assert adjacent.id is not None


async def test_cancelled_appointment_does_not_block_rebooking_same_slot(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner, patient_a = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-cancelled-frees",
    )
    patient_b = await make_patient(org, "PN-appt-cancelled-frees-b")
    original = await make_appointment(
        org,
        patient_a,
        practitioner,
        department,
        start_at=_FUTURE,
        duration_minutes=30,
        status=AppointmentStatus.CANCELLED,
    )
    assert original.status is AppointmentStatus.CANCELLED

    rebooked = Appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(rebooked)
    await db_session.flush()  # must NOT raise

    assert rebooked.id is not None


async def test_completed_appointment_does_not_block_new_booking_same_slot(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    """A historical `completed` appointment must not create a
    contradictory overlap conflict merely because its status changed —
    the exclusion constraint's `WHERE status = 'booked'` predicate is
    exactly what guarantees this."""
    org, department, practitioner, patient_a = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-completed-frees",
    )
    patient_b = await make_patient(org, "PN-appt-completed-frees-b")
    await make_appointment(
        org,
        patient_a,
        practitioner,
        department,
        start_at=_FUTURE,
        duration_minutes=30,
        status=AppointmentStatus.COMPLETED,
    )

    new_booking = Appointment(
        organization_id=org.id,
        patient_id=patient_b.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(new_booking)
    await db_session.flush()  # must NOT raise

    assert new_booking.id is not None


async def test_overlapping_patient_booking_rejected_by_exclusion_constraint(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    """Same patient, two DIFFERENT practitioners, overlapping time — the
    patient-side EXCLUDE constraint must reject it. See
    docs/APPOINTMENTS.md "Patient Double-Booking"."""
    org, department, practitioner_a, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-excl-patient",
    )
    practitioner_b = await make_practitioner(org)
    await make_practitioner_department(org, practitioner_b, department)
    await make_appointment(
        org, patient, practitioner_a, department, start_at=_FUTURE, duration_minutes=30
    )

    overlapping = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner_b.id,
        department_id=department.id,
        start_at=_FUTURE + timedelta(minutes=15),
        end_at=_FUTURE + timedelta(minutes=45),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(overlapping)

    with pytest.raises(IntegrityError, match="ex_appointments_patient_no_overlap"):
        await db_session.flush()
    await db_session.rollback()


async def test_same_patient_different_non_overlapping_practitioners_allowed(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org, department, practitioner_a, patient = await _scenario(
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "appt-nonoverlap-patient",
    )
    practitioner_b = await make_practitioner(org)
    await make_practitioner_department(org, practitioner_b, department)
    await make_appointment(
        org, patient, practitioner_a, department, start_at=_FUTURE, duration_minutes=30
    )

    non_overlapping = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner_b.id,
        department_id=department.id,
        start_at=_FUTURE + timedelta(hours=2),
        end_at=_FUTURE + timedelta(hours=2, minutes=30),
        status=AppointmentStatus.BOOKED,
    )
    db_session.add(non_overlapping)
    await db_session.flush()  # must NOT raise

    assert non_overlapping.id is not None
