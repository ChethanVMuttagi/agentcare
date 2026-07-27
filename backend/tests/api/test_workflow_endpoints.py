"""Workflow endpoint tests: creation, inspection, cancellation — end-to-end
over real HTTP (via `client_with_db`), against real PostgreSQL. See
docs/WORKFLOWS.md for the full RBAC matrix and privacy guarantees this
covers.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import ActorType, WorkflowRun
from app.services.workflow import WorkflowService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakeWorkflowRun = Callable[..., Awaitable[WorkflowRun]]

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


def _workflows_url(organization: Organization, suffix: str = "") -> str:
    return f"/api/v1/organizations/{organization.id}/workflows{suffix}"


async def _org_with_member(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
    role: Role = Role.ADMIN,
) -> tuple[Organization, User]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=role)
    return org, user


# --- POST .../workflows ---


async def test_admin_can_create_workflow_without_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-create-admin", Role.ADMIN
    )

    response = await client_with_db.post(
        _workflows_url(org),
        json={"request_type": "administrative_routing"},
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["patient_id"] is None
    assert len(body["correlation_id"]) == 32


async def test_admin_can_create_workflow_for_a_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-create-admin-pt", Role.ADMIN
    )
    patient = await make_patient(org, "PN-api-wf-create-admin-pt")

    response = await client_with_db.post(
        _workflows_url(org),
        json={"request_type": "appointment_booking", "patient_id": str(patient.id)},
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    assert response.json()["patient_id"] == str(patient.id)


async def test_patient_can_create_workflow_for_self(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-wf-create-patient")
    patient_user = await make_user("api-wf-create-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-wf-create-patient-own", user=patient_user)

    response = await client_with_db.post(
        _workflows_url(org),
        json={"request_type": "document_collection"},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 201
    assert response.json()["patient_id"] == str(own_patient.id)


async def test_patient_cannot_create_workflow_for_another_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    """A PATIENT-role caller supplies ANOTHER patient's id — it must be
    silently ignored, and the workflow must use the caller's OWN linked
    patient record instead. See docs/WORKFLOWS.md "Patient Identity"."""
    org = await make_organization("api-wf-create-spoof")
    other_patient = await make_patient(org, "PN-api-wf-create-spoof-other")
    patient_user = await make_user("api-wf-create-spoof")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-wf-create-spoof-own", user=patient_user)

    response = await client_with_db.post(
        _workflows_url(org),
        json={"request_type": "document_collection", "patient_id": str(other_patient.id)},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 201
    assert response.json()["patient_id"] == str(own_patient.id)
    assert response.json()["patient_id"] != str(other_patient.id)


async def test_patient_without_linked_record_cannot_create_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-wf-create-nolink")
    patient_user = await make_user("api-wf-create-nolink")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.post(
        _workflows_url(org),
        json={"request_type": "document_collection"},
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 404


async def test_create_workflow_rejects_cross_tenant_patient(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-create-crosstenant"
    )
    other_org = await make_organization("api-wf-create-crosstenant-other")
    other_patient = await make_patient(other_org, "PN-api-wf-create-crosstenant-other")

    response = await client_with_db.post(
        _workflows_url(org),
        json={"request_type": "appointment_booking", "patient_id": str(other_patient.id)},
        headers=_auth_header(admin),
    )

    assert response.status_code == 404


# --- GET .../workflows/{id} ---


async def test_admin_can_get_any_org_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-get-admin"
    )
    run = await make_workflow_run(org, admin.id)

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(run.id)


async def test_patient_cannot_get_another_patients_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-get-other"
    )
    other_patient = await make_patient(org, "PN-api-wf-get-other")
    run = await make_workflow_run(org, admin.id, patient=other_patient)

    patient_user = await make_user("api-wf-get-other-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-wf-get-other-caller-own", user=patient_user)

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}"), headers=_auth_header(patient_user)
    )
    assert response.status_code == 404


async def test_patient_can_get_own_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-get-own"
    )
    patient_user = await make_user("api-wf-get-own-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-wf-get-own", user=patient_user)
    run = await make_workflow_run(org, admin.id, patient=own_patient)

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}"), headers=_auth_header(patient_user)
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(run.id)


async def test_cross_tenant_workflow_lookup_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org_a, admin_a = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-cross-a"
    )
    org_b, admin_b = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-cross-b"
    )
    run_b = await make_workflow_run(org_b, admin_b.id)

    response = await client_with_db.get(
        _workflows_url(org_a, f"/{run_b.id}"), headers=_auth_header(admin_a)
    )
    assert response.status_code == 404


async def test_get_unknown_workflow_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-get-unknown"
    )
    response = await client_with_db.get(
        _workflows_url(org, f"/{uuid.uuid4()}"), headers=_auth_header(admin)
    )
    assert response.status_code == 404


# --- GET .../workflows (list) ---


async def test_admin_list_is_organization_wide(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-list-admin"
    )
    patient_a = await make_patient(org, "PN-api-wf-list-admin-a")
    patient_b = await make_patient(org, "PN-api-wf-list-admin-b")
    run_a = await make_workflow_run(org, admin.id, patient=patient_a)
    run_b = await make_workflow_run(org, admin.id, patient=patient_b)

    response = await client_with_db.get(_workflows_url(org), headers=_auth_header(admin))
    assert response.status_code == 200
    ids = {w["id"] for w in response.json()["workflows"]}
    assert {str(run_a.id), str(run_b.id)}.issubset(ids)


async def test_patient_list_is_self_scoped_only(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-list-patient"
    )
    patient_user = await make_user("api-wf-list-patient-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-wf-list-patient-own", user=patient_user)
    other_patient = await make_patient(org, "PN-api-wf-list-patient-other")

    own_run = await make_workflow_run(org, admin.id, patient=own_patient)
    await make_workflow_run(org, admin.id, patient=other_patient)

    response = await client_with_db.get(_workflows_url(org), headers=_auth_header(patient_user))
    assert response.status_code == 200
    ids = [w["id"] for w in response.json()["workflows"]]
    assert ids == [str(own_run.id)]


# --- GET .../workflows/{id}/steps and /events ---


async def test_admin_can_list_steps_and_events(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-steps-admin"
    )
    run = await make_workflow_run(org, admin.id)
    service = WorkflowService(db_session)
    await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="synthetic"
    )

    steps_response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/steps"), headers=_auth_header(admin)
    )
    assert steps_response.status_code == 200
    assert len(steps_response.json()["steps"]) == 1

    events_response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/events"), headers=_auth_header(admin)
    )
    assert events_response.status_code == 200


async def test_patient_cannot_list_steps_or_events_for_another_patients_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-steps-other"
    )
    other_patient = await make_patient(org, "PN-api-wf-steps-other")
    run = await make_workflow_run(org, admin.id, patient=other_patient)

    patient_user = await make_user("api-wf-steps-other-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-wf-steps-other-caller-own", user=patient_user)

    steps_response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/steps"), headers=_auth_header(patient_user)
    )
    assert steps_response.status_code == 404

    events_response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/events"), headers=_auth_header(patient_user)
    )
    assert events_response.status_code == 404


async def test_event_response_never_leaks_internal_fields(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    """Privacy check: an event response must never expose a raw
    exception, prompt, or chain-of-thought field — only the documented,
    bounded fields. See docs/WORKFLOWS.md "Privacy"."""
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-evt-fields"
    )
    run = await make_workflow_run(org, admin.id)
    service = WorkflowService(db_session)
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.SYSTEM,
        actor_identifier="synthetic-worker",
    )

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/events"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) >= 1
    expected_keys = {
        "id",
        "organization_id",
        "workflow_run_id",
        "workflow_step_id",
        "event_type",
        "actor_type",
        "actor_identifier",
        "safe_metadata",
        "created_at",
    }
    for event in events:
        assert set(event.keys()) == expected_keys


# --- POST .../workflows/{id}/cancel ---


async def test_admin_can_cancel_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-cancel-admin"
    )
    run = await make_workflow_run(org, admin.id)

    response = await client_with_db.post(
        _workflows_url(org, f"/{run.id}/cancel"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_patient_cannot_cancel_workflow(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-cancel-patient"
    )
    patient_user = await make_user("api-wf-cancel-patient-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-wf-cancel-patient", user=patient_user)
    run = await make_workflow_run(org, admin.id, patient=own_patient)

    response = await client_with_db.post(
        _workflows_url(org, f"/{run.id}/cancel"), headers=_auth_header(patient_user)
    )
    assert response.status_code == 403
