"""Patient endpoint tests: the first tenant-scoped business API.

Exercises `app.api.v1.endpoints.patients` end-to-end over real HTTP
(via `client_with_db` — see tests/conftest.py for why this is
`httpx.AsyncClient` rather than `starlette.testclient.TestClient`),
against real PostgreSQL.

Covers the full authorization matrix (docs/PATIENTS.md): ADMIN/STAFF may
create/list/get-by-id; PATIENT may only reach its own linked record via
`/patients/me`; membership is always re-resolved from the database, never
trusted from the token; and cross-tenant lookups are indistinguishable
from "doesn't exist" (404), never disclosing that a resource exists under
another organization.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]

# 32+ chars so PyJWT doesn't emit its InsecureKeyLengthWarning — still
# obviously synthetic. Scoped to this module only, same pattern as
# tests/api/test_auth_endpoints.py.
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


def _patients_url(organization: Organization, suffix: str = "") -> str:
    return f"/api/v1/organizations/{organization.id}/patients{suffix}"


# --- POST .../patients (create) ---------------------------------------------


async def test_admin_can_create_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-create-admin")
    admin = await make_user("api-create-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-ADMIN-CREATE",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["patient_number"] == "PN-API-ADMIN-CREATE"
    assert body["organization_id"] == str(org.id)


async def test_staff_can_create_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-create-staff")
    staff = await make_user("api-create-staff")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-STAFF-CREATE",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
        },
        headers=_auth_header(staff),
    )

    assert response.status_code == 201


async def test_patient_role_cannot_create_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-create-patient-forbidden")
    patient_user = await make_user("api-create-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-PATIENT-CREATE",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
        },
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 403


async def test_create_patient_requires_authentication(
    client_with_db: AsyncClient, make_organization: MakeOrganization
) -> None:
    org = await make_organization("api-create-unauth")

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-UNAUTH",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
        },
    )

    assert response.status_code == 401


async def test_create_patient_response_contains_no_medical_information(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-create-shape")
    admin = await make_user("api-create-shape")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-SHAPE",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    assert set(response.json().keys()) == {
        "id",
        "organization_id",
        "user_id",
        "patient_number",
        "first_name",
        "last_name",
        "date_of_birth",
        "is_active",
        "created_at",
        "updated_at",
    }


async def test_duplicate_patient_number_returns_conflict(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-dup-number")
    admin = await make_user("api-dup-number")
    await make_membership(org, admin, role=Role.ADMIN)
    await make_patient(org, "PN-API-DUP")

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-DUP",
            "first_name": "Another",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 409


async def test_invalid_user_link_returns_unprocessable(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-invalid-link")
    admin = await make_user("api-invalid-link-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    unrelated_user = await make_user("api-invalid-link-target")  # no membership at all

    response = await client_with_db.post(
        _patients_url(org),
        json={
            "patient_number": "PN-API-INVALID-LINK",
            "first_name": "Synthetic",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
            "user_id": str(unrelated_user.id),
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 422


# --- GET .../patients (list) -------------------------------------------------


async def test_admin_can_list_patients(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-list-admin")
    admin = await make_user("api-list-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    await make_patient(org, "PN-API-LIST-ADMIN")

    response = await client_with_db.get(_patients_url(org), headers=_auth_header(admin))

    assert response.status_code == 200
    assert len(response.json()["patients"]) == 1


async def test_staff_can_list_patients(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-list-staff")
    staff = await make_user("api-list-staff")
    await make_membership(org, staff, role=Role.STAFF)
    await make_patient(org, "PN-API-LIST-STAFF")

    response = await client_with_db.get(_patients_url(org), headers=_auth_header(staff))

    assert response.status_code == 200


async def test_patient_role_cannot_list_patients(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-list-patient-forbidden")
    patient_user = await make_user("api-list-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(_patients_url(org), headers=_auth_header(patient_user))

    assert response.status_code == 403


async def test_cross_tenant_list_never_includes_other_organizations_patients(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("api-list-cross-a")
    org_b = await make_organization("api-list-cross-b")
    admin_a = await make_user("api-list-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    await make_patient(org_a, "PN-API-LIST-CROSS-A")
    await make_patient(org_b, "PN-API-LIST-CROSS-B")

    response = await client_with_db.get(_patients_url(org_a), headers=_auth_header(admin_a))

    assert response.status_code == 200
    numbers = {p["patient_number"] for p in response.json()["patients"]}
    assert numbers == {"PN-API-LIST-CROSS-A"}


# --- GET .../patients/{patient_id} -------------------------------------------


async def test_admin_can_get_patient_by_id(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-get-admin")
    admin = await make_user("api-get-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-API-GET-ADMIN")

    response = await client_with_db.get(
        _patients_url(org, f"/{patient.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(patient.id)


async def test_staff_can_get_patient_by_id(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-get-staff")
    staff = await make_user("api-get-staff")
    await make_membership(org, staff, role=Role.STAFF)
    patient = await make_patient(org, "PN-API-GET-STAFF")

    response = await client_with_db.get(
        _patients_url(org, f"/{patient.id}"), headers=_auth_header(staff)
    )

    assert response.status_code == 200


async def test_patient_role_cannot_get_arbitrary_patient_by_id(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-get-patient-forbidden")
    patient_user = await make_user("api-get-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)
    other_patient = await make_patient(org, "PN-API-GET-PATIENT-FORBIDDEN")

    response = await client_with_db.get(
        _patients_url(org, f"/{other_patient.id}"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 403


async def test_get_patient_with_no_membership_is_forbidden(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-get-no-membership")
    outsider = await make_user("api-get-no-membership")  # no membership in org at all
    patient = await make_patient(org, "PN-API-GET-NO-MEMBERSHIP")

    response = await client_with_db.get(
        _patients_url(org, f"/{patient.id}"), headers=_auth_header(outsider)
    )

    assert response.status_code == 403


async def test_get_patient_with_inactive_membership_is_forbidden(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-get-inactive-membership")
    admin = await make_user("api-get-inactive-membership")
    await make_membership(org, admin, role=Role.ADMIN, is_active=False)
    patient = await make_patient(org, "PN-API-GET-INACTIVE-MEMBERSHIP")

    response = await client_with_db.get(
        _patients_url(org, f"/{patient.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 403


async def test_cross_tenant_patient_lookup_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("api-get-cross-a")
    org_b = await make_organization("api-get-cross-b")
    admin_a = await make_user("api-get-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    patient_b = await make_patient(org_b, "PN-API-GET-CROSS-B")

    # Admin of org_a, valid membership there, but the patient UUID belongs
    # to org_b -- knowing the UUID must not bypass tenant ownership.
    response = await client_with_db.get(
        _patients_url(org_a, f"/{patient_b.id}"), headers=_auth_header(admin_a)
    )

    assert response.status_code == 404


# --- GET .../patients/me (self-access) --------------------------------------


async def test_patient_me_returns_own_linked_record(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-me-success")
    patient_user = await make_user("api-me-success")
    await make_membership(org, patient_user, role=Role.PATIENT)
    patient = await make_patient(org, "PN-API-ME-SUCCESS", user=patient_user)

    response = await client_with_db.get(
        _patients_url(org, "/me"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(patient.id)


async def test_patient_me_without_linked_record_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-me-unlinked")
    patient_user = await make_user("api-me-unlinked")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(
        _patients_url(org, "/me"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 404


async def test_admin_me_without_linked_record_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """ADMIN/STAFF may call `/me` too (see docs/RBAC.md) -- it never
    exposes more than their own linked record, and an admin has none by
    construction (linkage requires a Role.PATIENT membership)."""
    org = await make_organization("api-me-admin")
    admin = await make_user("api-me-admin")
    await make_membership(org, admin, role=Role.ADMIN)

    response = await client_with_db.get(_patients_url(org, "/me"), headers=_auth_header(admin))

    assert response.status_code == 404


async def test_patient_me_cannot_see_another_organizations_linked_record(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("api-me-cross-a")
    org_b = await make_organization("api-me-cross-b")
    patient_user = await make_user("api-me-cross")
    await make_membership(org_a, patient_user, role=Role.PATIENT)
    await make_membership(org_b, patient_user, role=Role.PATIENT)
    await make_patient(org_a, "PN-API-ME-CROSS-A", user=patient_user)
    # No patient record linked in org_b.

    response = await client_with_db.get(
        _patients_url(org_b, "/me"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 404
