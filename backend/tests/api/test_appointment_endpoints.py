"""Appointment endpoint tests: booking, rescheduling, cancellation,
availability discovery — end-to-end over real HTTP (via `client_with_db`),
against real PostgreSQL. See docs/APPOINTMENTS.md for the full RBAC
matrix and privacy guarantees this covers.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeAppointment = Callable[..., Awaitable[Appointment]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"
_FUTURE = datetime.now(UTC) + timedelta(days=30)


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _appointments_url(organization: Organization, suffix: str = "") -> str:
    return f"/api/v1/organizations/{organization.id}/appointments{suffix}"


def _available_times_url(organization: Organization, practitioner: Practitioner) -> str:
    return (
        f"/api/v1/organizations/{organization.id}/practitioners/"
        f"{practitioner.id}/available-times"
    )


async def _wide_open_scenario(
    db_session: AsyncSession,
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

    for day in DayOfWeek:
        window = PractitionerAvailability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=day,
            start_time=time(0, 0),
            end_time=time(23, 59, 59),
            timezone="UTC",
        )
        db_session.add(window)
    await db_session.flush()

    return org, department, practitioner, patient


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --- GET .../practitioners/{id}/available-times --------------------------


async def test_admin_can_view_available_times(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-avt-admin",
    )
    admin = await make_user("api-avt-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.get(
        _available_times_url(org, practitioner),
        params={
            "department_id": str(department.id),
            "date": _FUTURE.date().isoformat(),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["available_times"]) > 0
    assert set(body["available_times"][0].keys()) == {"start_at", "end_at"}


async def test_patient_can_view_available_times(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-avt-patient",
    )
    patient_user = await make_user("api-avt-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(
        _available_times_url(org, practitioner),
        params={
            "department_id": str(department.id),
            "date": _FUTURE.date().isoformat(),
            "duration_minutes": 30,
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 200


async def test_available_times_unknown_practitioner_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-avt-unknown-prac")
    facility = await make_facility(org, "api-avt-unknown-prac")
    department = await make_department(org, facility, "API-AVT-UNKNOWN-PRAC")
    admin = await make_user("api-avt-unknown-prac")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/practitioners/{uuid.uuid4()}/available-times",
        params={
            "department_id": str(department.id),
            "date": _FUTURE.date().isoformat(),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 404


# --- POST .../appointments (book) -----------------------------------------


async def test_admin_can_book_for_a_patient(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-admin",
    )
    admin = await make_user("api-book-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _appointments_url(org),
        json={
            "patient_id": str(patient.id),
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["patient_id"] == str(patient.id)
    assert body["status"] == "booked"
    assert set(body.keys()) == {
        "id",
        "organization_id",
        "patient_id",
        "practitioner_id",
        "department_id",
        "start_at",
        "end_at",
        "status",
        "cancellation_reason",
        "created_at",
        "updated_at",
    }


async def test_admin_booking_without_patient_id_is_unprocessable(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-no-patient",
    )
    admin = await make_user("api-book-no-patient")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _appointments_url(org),
        json={
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 422


async def test_patient_can_book_for_self(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _unlinked_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-patient-self",
    )
    patient_user = await make_user("api-book-patient-self")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(
        org, "PN-api-book-patient-self-own", user=patient_user
    )

    response = await client_with_db.post(
        _appointments_url(org),
        json={
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 201
    assert response.json()["patient_id"] == str(own_patient.id)


async def test_patient_cannot_book_for_another_patient(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """A PATIENT-role caller supplies ANOTHER patient's id in the request
    body — it must be silently ignored, and the booking must use the
    caller's OWN linked patient record instead. See docs/APPOINTMENTS.md
    "RBAC" / "Patient Identity"."""
    org, department, practitioner, other_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-patient-spoof",
    )
    patient_user = await make_user("api-book-patient-spoof")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(
        org, "PN-api-book-patient-spoof-own", user=patient_user
    )

    response = await client_with_db.post(
        _appointments_url(org),
        json={
            "patient_id": str(other_patient.id),  # attempted spoof
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 201
    # Booked for the caller's OWN patient record, never `other_patient`.
    assert response.json()["patient_id"] == str(own_patient.id)
    assert response.json()["patient_id"] != str(other_patient.id)


async def test_patient_without_linked_record_cannot_book(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-no-link",
    )
    patient_user = await make_user("api-book-no-link")
    await make_membership(org, patient_user, role=Role.PATIENT)  # no linked Patient record

    response = await client_with_db.post(
        _appointments_url(org),
        json={
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 404


async def test_booking_conflict_returns_409(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-conflict",
    )
    patient_b = await make_patient(org, "PN-api-book-conflict-b")
    admin = await make_user("api-book-conflict")
    await make_membership(org, admin, role=Role.ADMIN)

    first = await client_with_db.post(
        _appointments_url(org),
        json={
            "patient_id": str(patient_a.id),
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin),
    )
    assert first.status_code == 201

    second = await client_with_db.post(
        _appointments_url(org),
        json={
            "patient_id": str(patient_b.id),
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _iso(_FUTURE + timedelta(minutes=15)),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin),
    )
    assert second.status_code == 409


async def test_booking_rejects_cross_tenant_practitioner(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a, department_a, _practitioner_a, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-cross-prac-a",
    )
    org_b, _department_b, practitioner_b, _patient_b = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-cross-prac-b",
    )
    admin_a = await make_user("api-book-cross-prac-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)

    response = await client_with_db.post(
        _appointments_url(org_a),
        json={
            "patient_id": str(patient_a.id),
            "practitioner_id": str(practitioner_b.id),  # belongs to org_b
            "department_id": str(department_a.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin_a),
    )

    assert response.status_code == 404


async def test_booking_rejects_cross_tenant_patient(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a, department_a, practitioner_a, _patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-book-cross-patient-a",
    )
    org_b = await make_organization("api-book-cross-patient-b")
    patient_b = await make_patient(org_b, "PN-api-book-cross-patient-b")
    admin_a = await make_user("api-book-cross-patient-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)

    response = await client_with_db.post(
        _appointments_url(org_a),
        json={
            "patient_id": str(patient_b.id),  # belongs to org_b
            "practitioner_id": str(practitioner_a.id),
            "department_id": str(department_a.id),
            "start_at": _iso(_FUTURE),
            "duration_minutes": 30,
        },
        headers=_auth_header(admin_a),
    )

    assert response.status_code == 404


# --- GET .../appointments/{id} ---------------------------------------------


async def test_admin_can_get_any_org_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-get-admin",
    )
    appointment = await make_appointment(org, patient, practitioner, department)
    admin = await make_user("api-get-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.get(
        _appointments_url(org, f"/{appointment.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(appointment.id)


async def test_patient_cannot_get_another_patients_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, other_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-get-patient-other",
    )
    appointment = await make_appointment(org, other_patient, practitioner, department)
    patient_user = await make_user("api-get-patient-other")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-get-patient-other-own", user=patient_user)

    response = await client_with_db.get(
        _appointments_url(org, f"/{appointment.id}"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 404


async def test_patient_can_get_own_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _unused_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-get-patient-own",
    )
    patient_user = await make_user("api-get-patient-own")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-get-patient-own-linked", user=patient_user)
    appointment = await make_appointment(org, own_patient, practitioner, department)

    response = await client_with_db.get(
        _appointments_url(org, f"/{appointment.id}"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(appointment.id)


async def test_cross_tenant_appointment_lookup_returns_not_found(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a = await make_organization("api-get-cross-a")
    org_b, department_b, practitioner_b, patient_b = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-get-cross-b",
    )
    appointment_b = await make_appointment(org_b, patient_b, practitioner_b, department_b)
    admin_a = await make_user("api-get-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)

    response = await client_with_db.get(
        _appointments_url(org_a, f"/{appointment_b.id}"), headers=_auth_header(admin_a)
    )

    assert response.status_code == 404


# --- GET .../appointments (list) -------------------------------------------


async def test_admin_list_is_organization_wide(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient_a = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-list-admin",
    )
    patient_b = await make_patient(org, "PN-api-list-admin-b")
    await make_appointment(org, patient_a, practitioner, department, start_at=_FUTURE)
    await make_appointment(
        org, patient_b, practitioner, department, start_at=_FUTURE + timedelta(hours=3)
    )
    admin = await make_user("api-list-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.get(_appointments_url(org), headers=_auth_header(admin))

    assert response.status_code == 200
    assert len(response.json()["appointments"]) == 2


async def test_patient_list_is_self_scoped_only(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """A PATIENT caller must NEVER receive the organization-wide list —
    only their own appointments. See docs/APPOINTMENTS.md "Listing
    Privacy"."""
    org, department, practitioner, other_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-list-patient",
    )
    await make_appointment(
        org, other_patient, practitioner, department, start_at=_FUTURE + timedelta(hours=3)
    )
    patient_user = await make_user("api-list-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-list-patient-own", user=patient_user)
    own_appointment = await make_appointment(
        org, own_patient, practitioner, department, start_at=_FUTURE
    )

    response = await client_with_db.get(
        _appointments_url(org), headers=_auth_header(patient_user)
    )

    assert response.status_code == 200
    body = response.json()["appointments"]
    assert len(body) == 1
    assert body[0]["id"] == str(own_appointment.id)


# --- PATCH .../appointments/{id}/reschedule --------------------------------


async def test_admin_can_reschedule_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-reschedule-admin",
    )
    appointment = await make_appointment(org, patient, practitioner, department, start_at=_FUTURE)
    admin = await make_user("api-reschedule-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    new_start = _FUTURE + timedelta(hours=3)

    response = await client_with_db.patch(
        _appointments_url(org, f"/{appointment.id}/reschedule"),
        json={"start_at": _iso(new_start), "duration_minutes": 45},
        headers=_auth_header(admin),
    )

    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["start_at"]) == new_start


async def test_patient_cannot_reschedule_another_patients_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, other_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-reschedule-patient-other",
    )
    appointment = await make_appointment(
        org, other_patient, practitioner, department, start_at=_FUTURE
    )
    patient_user = await make_user("api-reschedule-patient-other")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-reschedule-patient-other-own", user=patient_user)

    response = await client_with_db.patch(
        _appointments_url(org, f"/{appointment.id}/reschedule"),
        json={"start_at": _iso(_FUTURE + timedelta(hours=3)), "duration_minutes": 30},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 404


# --- POST .../appointments/{id}/cancel -------------------------------------


async def test_admin_can_cancel_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-cancel-admin",
    )
    appointment = await make_appointment(org, patient, practitioner, department, start_at=_FUTURE)
    admin = await make_user("api-cancel-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _appointments_url(org, f"/{appointment.id}/cancel"),
        json={"cancellation_reason": "administrative test cancellation"},
        headers=_auth_header(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancellation_reason"] == "administrative test cancellation"


async def test_patient_can_cancel_own_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, _unused = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-cancel-patient-own",
    )
    patient_user = await make_user("api-cancel-patient-own")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-cancel-patient-own-linked", user=patient_user)
    appointment = await make_appointment(
        org, own_patient, practitioner, department, start_at=_FUTURE
    )

    response = await client_with_db.post(
        _appointments_url(org, f"/{appointment.id}/cancel"),
        json={},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_patient_cannot_cancel_another_patients_appointment(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, other_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-cancel-patient-other",
    )
    appointment = await make_appointment(
        org, other_patient, practitioner, department, start_at=_FUTURE
    )
    patient_user = await make_user("api-cancel-patient-other")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-cancel-patient-other-own", user=patient_user)

    response = await client_with_db.post(
        _appointments_url(org, f"/{appointment.id}/cancel"),
        json={},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 404


async def test_cancel_already_cancelled_appointment_returns_422(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    make_appointment: MakeAppointment,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "api-cancel-twice",
    )
    appointment = await make_appointment(
        org,
        patient,
        practitioner,
        department,
        start_at=_FUTURE,
        status=AppointmentStatus.CANCELLED,
    )
    admin = await make_user("api-cancel-twice")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _appointments_url(org, f"/{appointment.id}/cancel"),
        json={},
        headers=_auth_header(admin),
    )

    assert response.status_code == 422
