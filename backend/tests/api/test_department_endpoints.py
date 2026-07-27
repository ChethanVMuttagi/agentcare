"""Department endpoint tests: administrative scheduling-resource management.

Exercises `app.api.v1.endpoints.departments` end-to-end over real HTTP
(via `client_with_db` — see tests/conftest.py), against real PostgreSQL.
Covers the authorization matrix documented in docs/SCHEDULING_RESOURCES.md:
`ADMIN` may create; `ADMIN`/`STAFF` may list/get; `PATIENT` may not reach
any route here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
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


def _departments_url(organization: Organization, suffix: str = "") -> str:
    return f"/api/v1/organizations/{organization.id}/departments{suffix}"


# --- POST .../departments (create) ------------------------------------------


async def test_admin_can_create_department(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-dept-create-admin")
    facility = await make_facility(org, "api-dept-create-admin")
    admin = await make_user("api-dept-create-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _departments_url(org),
        json={"facility_id": str(facility.id), "name": "Cardiology", "code": "CARD"},
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "CARD"
    assert body["facility_id"] == str(facility.id)


async def test_staff_cannot_create_department(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-dept-create-staff-forbidden")
    facility = await make_facility(org, "api-dept-create-staff-forbidden")
    staff = await make_user("api-dept-create-staff-forbidden")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.post(
        _departments_url(org),
        json={"facility_id": str(facility.id), "name": "Cardiology", "code": "CARD"},
        headers=_auth_header(staff),
    )

    assert response.status_code == 403


async def test_patient_cannot_create_department(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-dept-create-patient-forbidden")
    facility = await make_facility(org, "api-dept-create-patient-forbidden")
    patient_user = await make_user("api-dept-create-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.post(
        _departments_url(org),
        json={"facility_id": str(facility.id), "name": "Cardiology", "code": "CARD"},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 403


async def test_create_department_requires_authentication(
    client_with_db: AsyncClient, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("api-dept-create-unauth")
    facility = await make_facility(org, "api-dept-create-unauth")

    response = await client_with_db.post(
        _departments_url(org),
        json={"facility_id": str(facility.id), "name": "Cardiology", "code": "CARD"},
    )

    assert response.status_code == 401


async def test_create_department_rejects_facility_from_another_organization(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a = await make_organization("api-dept-cross-facility-a")
    org_b = await make_organization("api-dept-cross-facility-b")
    facility_b = await make_facility(org_b, "api-dept-cross-facility-b")
    admin_a = await make_user("api-dept-cross-facility-admin")
    await make_membership(org_a, admin_a, role=Role.ADMIN)

    response = await client_with_db.post(
        _departments_url(org_a),
        json={"facility_id": str(facility_b.id), "name": "Cardiology", "code": "CARD"},
        headers=_auth_header(admin_a),
    )

    assert response.status_code == 404


async def test_duplicate_department_code_returns_conflict(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("api-dept-dup-code")
    facility = await make_facility(org, "api-dept-dup-code")
    admin = await make_user("api-dept-dup-code")
    await make_membership(org, admin, role=Role.ADMIN)
    await make_department(org, facility, "DUP")

    response = await client_with_db.post(
        _departments_url(org),
        json={"facility_id": str(facility.id), "name": "Another", "code": "DUP"},
        headers=_auth_header(admin),
    )

    assert response.status_code == 409


# --- GET .../departments (list) ----------------------------------------------


async def test_admin_can_list_departments(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("api-dept-list-admin")
    facility = await make_facility(org, "api-dept-list-admin")
    admin = await make_user("api-dept-list-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    await make_department(org, facility, "LIST-ADMIN")

    response = await client_with_db.get(_departments_url(org), headers=_auth_header(admin))

    assert response.status_code == 200
    assert len(response.json()["departments"]) == 1


async def test_staff_can_list_departments(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-dept-list-staff")
    staff = await make_user("api-dept-list-staff")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.get(_departments_url(org), headers=_auth_header(staff))

    assert response.status_code == 200


async def test_patient_cannot_list_departments(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-dept-list-patient-forbidden")
    patient_user = await make_user("api-dept-list-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(_departments_url(org), headers=_auth_header(patient_user))

    assert response.status_code == 403


async def test_cross_tenant_list_never_includes_other_organizations_departments(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("api-dept-list-cross-a")
    org_b = await make_organization("api-dept-list-cross-b")
    facility_a = await make_facility(org_a, "api-dept-list-cross-a")
    facility_b = await make_facility(org_b, "api-dept-list-cross-b")
    admin_a = await make_user("api-dept-list-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    await make_department(org_a, facility_a, "CROSS-A")
    await make_department(org_b, facility_b, "CROSS-B")

    response = await client_with_db.get(_departments_url(org_a), headers=_auth_header(admin_a))

    assert response.status_code == 200
    codes = {d["code"] for d in response.json()["departments"]}
    assert codes == {"CROSS-A"}


# --- GET .../departments/{department_id} -------------------------------------


async def test_admin_can_get_department_by_id(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("api-dept-get-admin")
    facility = await make_facility(org, "api-dept-get-admin")
    admin = await make_user("api-dept-get-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    department = await make_department(org, facility, "GET-ADMIN")

    response = await client_with_db.get(
        _departments_url(org, f"/{department.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(department.id)


async def test_patient_cannot_get_department_by_id(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("api-dept-get-patient-forbidden")
    facility = await make_facility(org, "api-dept-get-patient-forbidden")
    patient_user = await make_user("api-dept-get-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)
    department = await make_department(org, facility, "GET-PATIENT-FORBIDDEN")

    response = await client_with_db.get(
        _departments_url(org, f"/{department.id}"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 403


async def test_cross_tenant_department_lookup_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("api-dept-get-cross-a")
    org_b = await make_organization("api-dept-get-cross-b")
    facility_b = await make_facility(org_b, "api-dept-get-cross-b")
    admin_a = await make_user("api-dept-get-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    department_b = await make_department(org_b, facility_b, "GET-CROSS-B")

    response = await client_with_db.get(
        _departments_url(org_a, f"/{department_b.id}"), headers=_auth_header(admin_a)
    )

    assert response.status_code == 404
