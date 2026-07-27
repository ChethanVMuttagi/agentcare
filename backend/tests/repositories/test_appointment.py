"""app.repositories.appointment tests against real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment
from app.repositories import appointment as appointment_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeAppointment = Callable[..., Awaitable[Appointment]]

_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def test_get_by_id_returns_appointment_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org = await make_organization("repo-appt-get")
    facility = await make_facility(org, "repo-appt-get")
    department = await make_department(org, facility, "REPO-APPT-GET")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-repo-appt-get")
    appointment = await make_appointment(org, patient, practitioner, department)

    result = await appointment_repository.get_by_id(
        db_session, organization_id=org.id, appointment_id=appointment.id
    )

    assert result is not None
    assert result.id == appointment.id


async def test_get_by_id_returns_none_for_cross_tenant_appointment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org_a = await make_organization("repo-appt-cross-a")
    org_b = await make_organization("repo-appt-cross-b")
    facility_b = await make_facility(org_b, "repo-appt-cross-b")
    department_b = await make_department(org_b, facility_b, "REPO-APPT-CROSS-B")
    practitioner_b = await make_practitioner(org_b)
    await make_practitioner_department(org_b, practitioner_b, department_b)
    patient_b = await make_patient(org_b, "PN-repo-appt-cross-b")
    appointment_b = await make_appointment(org_b, patient_b, practitioner_b, department_b)

    result = await appointment_repository.get_by_id(
        db_session, organization_id=org_a.id, appointment_id=appointment_b.id
    )

    assert result is None


async def test_list_by_organization_returns_only_that_organizations_appointments(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org_a = await make_organization("repo-appt-list-a")
    facility_a = await make_facility(org_a, "repo-appt-list-a")
    department_a = await make_department(org_a, facility_a, "REPO-APPT-LIST-A")
    practitioner_a = await make_practitioner(org_a)
    await make_practitioner_department(org_a, practitioner_a, department_a)
    patient_a = await make_patient(org_a, "PN-repo-appt-list-a")
    appointment_a = await make_appointment(org_a, patient_a, practitioner_a, department_a)

    org_b = await make_organization("repo-appt-list-b")
    facility_b = await make_facility(org_b, "repo-appt-list-b")
    department_b = await make_department(org_b, facility_b, "REPO-APPT-LIST-B")
    practitioner_b = await make_practitioner(org_b)
    await make_practitioner_department(org_b, practitioner_b, department_b)
    patient_b = await make_patient(org_b, "PN-repo-appt-list-b")
    await make_appointment(org_b, patient_b, practitioner_b, department_b)

    results = await appointment_repository.list_by_organization(
        db_session, organization_id=org_a.id
    )

    assert [a.id for a in results] == [appointment_a.id]


async def test_list_by_patient_returns_only_that_patients_appointments(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org = await make_organization("repo-appt-list-patient")
    facility = await make_facility(org, "repo-appt-list-patient")
    department = await make_department(org, facility, "REPO-APPT-LIST-PATIENT")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient_a = await make_patient(org, "PN-repo-appt-list-patient-a")
    patient_b = await make_patient(org, "PN-repo-appt-list-patient-b")
    appointment_a = await make_appointment(
        org, patient_a, practitioner, department, start_at=_FUTURE
    )
    await make_appointment(
        org, patient_b, practitioner, department, start_at=_FUTURE + timedelta(hours=2)
    )

    results = await appointment_repository.list_by_patient(
        db_session, organization_id=org.id, patient_id=patient_a.id
    )

    assert [a.id for a in results] == [appointment_a.id]


async def test_list_practitioner_appointments_in_range_filters_overlap_and_status(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
) -> None:
    org = await make_organization("repo-appt-range")
    facility = await make_facility(org, "repo-appt-range")
    department = await make_department(org, facility, "REPO-APPT-RANGE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-repo-appt-range")

    in_range = await make_appointment(
        org, patient, practitioner, department, start_at=_FUTURE, duration_minutes=30
    )
    # Cancelled: must NOT be returned under the default BOOKED-only filter.
    await make_appointment(
        org,
        patient,
        practitioner,
        department,
        start_at=_FUTURE + timedelta(hours=5),
        duration_minutes=30,
        status=AppointmentStatus.CANCELLED,
    )
    # Outside the queried range entirely.
    await make_appointment(
        org,
        patient,
        practitioner,
        department,
        start_at=_FUTURE + timedelta(days=10),
        duration_minutes=30,
    )

    results = await appointment_repository.list_practitioner_appointments_in_range(
        db_session,
        organization_id=org.id,
        practitioner_id=practitioner.id,
        range_start=_FUTURE - timedelta(hours=1),
        range_end=_FUTURE + timedelta(hours=1),
    )

    assert [a.id for a in results] == [in_range.id]


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("repo-appt-create-no-commit")
    facility = await make_facility(org, "repo-appt-create-no-commit")
    department = await make_department(org, facility, "REPO-APPT-CREATE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, "PN-repo-appt-create-no-commit")

    appointment = Appointment(
        organization_id=org.id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
        start_at=_FUTURE,
        end_at=_FUTURE + timedelta(minutes=30),
    )

    created = await appointment_repository.create(db_session, appointment)
    assert created.id is not None

    await db_session.rollback()

    results = await appointment_repository.list_by_organization(db_session, organization_id=org.id)
    assert results == []
