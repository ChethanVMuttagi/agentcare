"""Practitioner endpoint tests: administrative scheduling-resource management.

Covers practitioner CRUD-lite, department assignment, and recurring
availability end-to-end over real HTTP (via `client_with_db`), against
real PostgreSQL. See docs/SCHEDULING_RESOURCES.md for the authorization
matrix.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakeAvailability = Callable[..., Awaitable[PractitionerAvailability]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _practitioners_url(organization: Organization, suffix: str = "") -> str:
    return f"/api/v1/organizations/{organization.id}/practitioners{suffix}"


# --- POST .../practitioners (create) -----------------------------------------


async def test_admin_can_create_practitioner(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-prac-create-admin")
    admin = await make_user("api-prac-create-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _practitioners_url(org),
        json={
            "first_name": "Synthetic",
            "last_name": "Practitioner",
            "practitioner_type": "physician",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["first_name"] == "Synthetic"
    assert body["practitioner_type"] == "physician"


async def test_staff_cannot_create_practitioner(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-prac-create-staff-forbidden")
    staff = await make_user("api-prac-create-staff-forbidden")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.post(
        _practitioners_url(org),
        json={
            "first_name": "Synthetic",
            "last_name": "Practitioner",
            "practitioner_type": "physician",
        },
        headers=_auth_header(staff),
    )

    assert response.status_code == 403


async def test_patient_cannot_create_practitioner(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-prac-create-patient-forbidden")
    patient_user = await make_user("api-prac-create-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.post(
        _practitioners_url(org),
        json={
            "first_name": "Synthetic",
            "last_name": "Practitioner",
            "practitioner_type": "physician",
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 403


async def test_practitioner_response_contains_no_unsafe_fields(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-prac-shape")
    admin = await make_user("api-prac-shape")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _practitioners_url(org),
        json={
            "first_name": "Synthetic",
            "last_name": "Practitioner",
            "practitioner_type": "therapist",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    assert set(response.json().keys()) == {
        "id",
        "organization_id",
        "first_name",
        "last_name",
        "practitioner_type",
        "is_active",
        "created_at",
        "updated_at",
    }


# --- GET .../practitioners (list) --------------------------------------------


async def test_admin_can_list_practitioners(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("api-prac-list-admin")
    admin = await make_user("api-prac-list-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    await make_practitioner(org)

    response = await client_with_db.get(_practitioners_url(org), headers=_auth_header(admin))

    assert response.status_code == 200
    assert len(response.json()["practitioners"]) == 1


async def test_staff_can_list_practitioners(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-prac-list-staff")
    staff = await make_user("api-prac-list-staff")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.get(_practitioners_url(org), headers=_auth_header(staff))

    assert response.status_code == 200


async def test_patient_cannot_list_practitioners(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-prac-list-patient-forbidden")
    patient_user = await make_user("api-prac-list-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(
        _practitioners_url(org), headers=_auth_header(patient_user)
    )

    assert response.status_code == 403


# --- GET .../practitioners/{practitioner_id} ---------------------------------


async def test_admin_can_get_practitioner_by_id(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("api-prac-get-admin")
    admin = await make_user("api-prac-get-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)

    response = await client_with_db.get(
        _practitioners_url(org, f"/{practitioner.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(practitioner.id)


async def test_cross_tenant_practitioner_lookup_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("api-prac-get-cross-a")
    org_b = await make_organization("api-prac-get-cross-b")
    admin_a = await make_user("api-prac-get-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    practitioner_b = await make_practitioner(org_b)

    response = await client_with_db.get(
        _practitioners_url(org_a, f"/{practitioner_b.id}"), headers=_auth_header(admin_a)
    )

    assert response.status_code == 404


# --- POST .../practitioners/{id}/departments/{id} (assignment) --------------


async def test_admin_can_assign_practitioner_to_department(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("api-assign-admin")
    facility = await make_facility(org, "api-assign-admin")
    department = await make_department(org, facility, "ASSIGN-ADMIN")
    admin = await make_user("api-assign-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/departments/{department.id}"),
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["practitioner_id"] == str(practitioner.id)
    assert body["department_id"] == str(department.id)


async def test_staff_cannot_assign_practitioner_to_department(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("api-assign-staff-forbidden")
    facility = await make_facility(org, "api-assign-staff-forbidden")
    department = await make_department(org, facility, "ASSIGN-STAFF-FORBIDDEN")
    staff = await make_user("api-assign-staff-forbidden")
    await make_membership(org, staff, role=Role.STAFF)
    practitioner = await make_practitioner(org)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/departments/{department.id}"),
        headers=_auth_header(staff),
    )

    assert response.status_code == 403


async def test_duplicate_assignment_returns_conflict(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("api-assign-dup")
    facility = await make_facility(org, "api-assign-dup")
    department = await make_department(org, facility, "ASSIGN-DUP")
    admin = await make_user("api-assign-dup")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/departments/{department.id}"),
        headers=_auth_header(admin),
    )

    assert response.status_code == 409


async def test_assignment_rejects_cross_tenant_department(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("api-assign-cross-a")
    org_b = await make_organization("api-assign-cross-b")
    facility_b = await make_facility(org_b, "api-assign-cross-b")
    department_b = await make_department(org_b, facility_b, "ASSIGN-CROSS-B")
    admin_a = await make_user("api-assign-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    practitioner_a = await make_practitioner(org_a)

    response = await client_with_db.post(
        _practitioners_url(org_a, f"/{practitioner_a.id}/departments/{department_b.id}"),
        headers=_auth_header(admin_a),
    )

    assert response.status_code == 404


# --- POST/GET .../practitioners/{id}/availability ----------------------------


async def test_admin_can_create_availability(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("api-avail-create-admin")
    facility = await make_facility(org, "api-avail-create-admin")
    department = await make_department(org, facility, "AVAIL-CREATE-ADMIN")
    admin = await make_user("api-avail-create-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        json={
            "department_id": str(department.id),
            "day_of_week": "monday",
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "timezone": "Asia/Kolkata",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["day_of_week"] == "monday"
    assert body["timezone"] == "Asia/Kolkata"


async def test_staff_can_create_availability(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("api-avail-create-staff")
    facility = await make_facility(org, "api-avail-create-staff")
    department = await make_department(org, facility, "AVAIL-CREATE-STAFF")
    staff = await make_user("api-avail-create-staff")
    await make_membership(org, staff, role=Role.STAFF)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        json={
            "department_id": str(department.id),
            "day_of_week": "monday",
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "timezone": "UTC",
        },
        headers=_auth_header(staff),
    )

    assert response.status_code == 201


async def test_patient_cannot_create_availability(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("api-avail-create-patient-forbidden")
    facility = await make_facility(org, "api-avail-create-patient-forbidden")
    department = await make_department(org, facility, "AVAIL-CREATE-PATIENT")
    patient_user = await make_user("api-avail-create-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        json={
            "department_id": str(department.id),
            "day_of_week": "monday",
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "timezone": "UTC",
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 403


async def test_create_availability_for_unassigned_practitioner_is_unprocessable(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("api-avail-unassigned")
    facility = await make_facility(org, "api-avail-unassigned")
    department = await make_department(org, facility, "AVAIL-UNASSIGNED")
    admin = await make_user("api-avail-unassigned")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)  # never assigned

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        json={
            "department_id": str(department.id),
            "day_of_week": "monday",
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "timezone": "UTC",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 422


async def test_create_availability_rejects_invalid_time_range(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("api-avail-bad-range")
    facility = await make_facility(org, "api-avail-bad-range")
    department = await make_department(org, facility, "AVAIL-BAD-RANGE")
    admin = await make_user("api-avail-bad-range")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        json={
            "department_id": str(department.id),
            "day_of_week": "monday",
            "start_time": "12:00:00",
            "end_time": "09:00:00",
            "timezone": "UTC",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 422


async def test_create_availability_rejects_overlap(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("api-avail-overlap")
    facility = await make_facility(org, "api-avail-overlap")
    department = await make_department(org, facility, "AVAIL-OVERLAP")
    admin = await make_user("api-avail-overlap")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(org, practitioner, department)

    response = await client_with_db.post(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        json={
            "department_id": str(department.id),
            "day_of_week": "monday",
            "start_time": "11:00:00",
            "end_time": "13:00:00",
            "timezone": "UTC",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 409


async def test_admin_can_list_availability(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_availability: MakeAvailability,
) -> None:
    org = await make_organization("api-avail-list-admin")
    facility = await make_facility(org, "api-avail-list-admin")
    department = await make_department(org, facility, "AVAIL-LIST-ADMIN")
    admin = await make_user("api-avail-list-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    await make_availability(org, practitioner, department)

    response = await client_with_db.get(
        _practitioners_url(org, f"/{practitioner.id}/availability"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert len(response.json()["availability"]) == 1


async def test_patient_cannot_list_availability(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("api-avail-list-patient-forbidden")
    patient_user = await make_user("api-avail-list-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)
    practitioner = await make_practitioner(org)

    response = await client_with_db.get(
        _practitioners_url(org, f"/{practitioner.id}/availability"),
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 403


async def test_list_availability_for_unknown_practitioner_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-avail-list-unknown-prac")
    admin = await make_user("api-avail-list-unknown-prac")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.get(
        _practitioners_url(org, f"/{uuid.uuid4()}/availability"), headers=_auth_header(admin)
    )

    assert response.status_code == 404
