"""Pagination validation (Sprint 2): every list endpoint's `limit` query
parameter is bounded (`Query(ge=1, le=MAX_PAGE_SIZE)`, see
`app.core.pagination`) — previously unbounded, so a caller could request
an effectively unlimited `SELECT ... LIMIT n`. `offset` is bounded to
`>= 0`. Covers all seven endpoints the fix touched: approvals,
appointments, departments, documents, patients, practitioners (both its
list and its availability-list routes), and workflows.

Real HTTP, real PostgreSQL (skipped without `AGENTCARE_TEST_POSTGRES_URL`
— see tests/conftest.py). FastAPI validates `Query` bounds before the
route body ever runs, so these are true 422s from the framework's own
request-parsing layer, not a `WorkflowConflictError`/service-level
rejection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.core.pagination import MAX_PAGE_SIZE
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[Any]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePatient = Callable[..., Awaitable[Patient]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


async def _admin_context(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, dict[str, str]]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    return org, _auth_header(user)


# --- Endpoints needing only an organization + admin membership --------------


@pytest.mark.parametrize(
    "path_suffix",
    ["approvals", "appointments", "departments", "patients", "workflows", "practitioners"],
)
async def test_limit_over_max_is_rejected(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    path_suffix: str,
) -> None:
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, f"pg-over-{path_suffix}"
    )

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/{path_suffix}",
        params={"limit": MAX_PAGE_SIZE + 1},
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path_suffix",
    ["approvals", "appointments", "departments", "patients", "workflows", "practitioners"],
)
async def test_default_limit_still_works(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    path_suffix: str,
) -> None:
    """Regression guard for the fix itself: an ordinary request with no
    `limit` at all (using the unchanged default of 50) must still
    succeed — this is the "preserve defaults" requirement."""
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, f"pg-default-{path_suffix}"
    )

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/{path_suffix}", headers=headers
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "path_suffix",
    ["approvals", "appointments", "departments", "patients", "workflows", "practitioners"],
)
async def test_negative_offset_is_rejected(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    path_suffix: str,
) -> None:
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, f"pg-negoff-{path_suffix}"
    )

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/{path_suffix}",
        params={"offset": -1},
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path_suffix",
    ["approvals", "appointments", "departments", "patients", "workflows", "practitioners"],
)
async def test_limit_at_max_is_accepted(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    path_suffix: str,
) -> None:
    """The boundary itself is inclusive — `le=MAX_PAGE_SIZE`, not `lt`."""
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, f"pg-atmax-{path_suffix}"
    )

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/{path_suffix}",
        params={"limit": MAX_PAGE_SIZE},
        headers=headers,
    )

    assert response.status_code == 200


# --- Documents: needs a real patient_id path segment -------------------------


async def test_document_list_limit_over_max_is_rejected(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, "pg-over-documents"
    )
    patient = await make_patient(org, "PG-DOC-001")

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/patients/{patient.id}/documents",
        params={"limit": MAX_PAGE_SIZE + 1},
        headers=headers,
    )

    assert response.status_code == 422


async def test_document_list_default_limit_still_works(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, "pg-default-documents"
    )
    patient = await make_patient(org, "PG-DOC-002")

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/patients/{patient.id}/documents", headers=headers
    )

    assert response.status_code == 200


# --- Practitioner availability: needs a real practitioner_id path segment ---


async def test_practitioner_availability_limit_over_max_is_rejected(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, "pg-over-availability"
    )
    practitioner = await make_practitioner(org)

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/practitioners/{practitioner.id}/availability",
        params={"limit": MAX_PAGE_SIZE + 1},
        headers=headers,
    )

    assert response.status_code == 422


async def test_practitioner_availability_default_limit_still_works(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_practitioner: MakePractitioner,
) -> None:
    org, headers = await _admin_context(
        make_organization, make_user, make_membership, "pg-default-availability"
    )
    practitioner = await make_practitioner(org)

    response = await client_with_db.get(
        f"/api/v1/organizations/{org.id}/practitioners/{practitioner.id}/availability",
        headers=headers,
    )

    assert response.status_code == 200
