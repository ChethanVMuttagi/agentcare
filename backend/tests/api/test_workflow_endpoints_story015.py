"""STORY-015 workflow endpoint tests: the Patient Registration trigger
(`POST .../workflows/patient-registrations`) and the timeline inspection
endpoint (`GET .../workflows/{workflow_id}/timeline`) — end-to-end over
real HTTP (via `client_with_db`), against real PostgreSQL. Mirrors
`tests/api/test_workflow_endpoints.py`'s established pattern exactly.
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
from app.models.workflow import ActorType, WorkflowRequestType
from app.services.workflow import WorkflowService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]

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


# --- POST .../workflows/patient-registrations ---


async def test_admin_can_start_patient_registration(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-reg-admin"
    )

    response = await client_with_db.post(
        _workflows_url(org, "/patient-registrations"),
        json={
            "patient_number": "PN-API-REG-1",
            "first_name": "Drew",
            "last_name": "Okafor",
            "date_of_birth": "1994-05-12",
        },
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["request_type"] == "patient_registration"
    assert body["patient_id"] is None


async def test_staff_can_start_patient_registration(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-wf-reg-staff")
    staff = await make_user("api-wf-reg-staff")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.post(
        _workflows_url(org, "/patient-registrations"),
        json={
            "patient_number": "PN-API-REG-2",
            "first_name": "Sasha",
            "last_name": "Ivanov",
            "date_of_birth": "1990-01-01",
        },
        headers=_auth_header(staff),
    )
    assert response.status_code == 201


async def test_patient_cannot_start_patient_registration(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-wf-reg-patient")
    patient_user = await make_user("api-wf-reg-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.post(
        _workflows_url(org, "/patient-registrations"),
        json={
            "patient_number": "PN-API-REG-3",
            "first_name": "Nour",
            "last_name": "Haddad",
            "date_of_birth": "2000-06-06",
        },
        headers=_auth_header(patient_user),
    )
    assert response.status_code == 403


async def test_patient_registration_with_conflict_still_returns_201_with_failed_status(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-reg-conflict"
    )
    await make_patient(org, "PN-API-REG-CONFLICT")

    response = await client_with_db.post(
        _workflows_url(org, "/patient-registrations"),
        json={
            "patient_number": "PN-API-REG-CONFLICT",
            "first_name": "Kai",
            "last_name": "Tanaka",
            "date_of_birth": "1985-09-09",
        },
        headers=_auth_header(admin),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_code"] == "patient_number_conflict"


async def test_patient_registration_rejects_future_date_of_birth(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-reg-future-dob"
    )
    response = await client_with_db.post(
        _workflows_url(org, "/patient-registrations"),
        json={
            "patient_number": "PN-API-REG-FUTURE",
            "first_name": "Future",
            "last_name": "Person",
            "date_of_birth": "2999-01-01",
        },
        headers=_auth_header(admin),
    )
    assert response.status_code == 422


# --- GET .../workflows/{workflow_id}/timeline ---


async def test_admin_can_get_timeline(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-timeline-admin"
    )
    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/timeline"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"] == str(run.id)
    assert body["status"] == "running"
    entries = body["entries"]
    assert [e["event_type"] for e in entries] == ["workflow_created", "workflow_started"]
    for entry in entries:
        assert entry["workflow_step_id"] is None
        assert entry["step_type"] is None
    assert entries[0]["sequence"] < entries[1]["sequence"]


async def test_timeline_entry_denormalizes_step_summary(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-timeline-step"
    )
    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    step = await service.create_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        sequence_number=1,
        step_type="coordination",
        agent_name="coordinator",
    )
    await service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="coordinator",
    )

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/timeline"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    step_started_entry = next(e for e in entries if e["event_type"] == "step_started")
    assert step_started_entry["workflow_step_id"] == str(step.id)
    assert step_started_entry["step_sequence_number"] == 1
    assert step_started_entry["step_type"] == "coordination"
    assert step_started_entry["step_agent_name"] == "coordinator"


async def test_patient_cannot_get_timeline_for_another_patients_workflow(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-timeline-other"
    )
    other_patient = await make_patient(org, "PN-API-TIMELINE-OTHER")
    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        patient_id=other_patient.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )

    patient_user = await make_user("api-wf-timeline-other-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-API-TIMELINE-OTHER-CALLER", user=patient_user)

    response = await client_with_db.get(
        _workflows_url(org, f"/{run.id}/timeline"), headers=_auth_header(patient_user)
    )
    assert response.status_code == 404


async def test_get_timeline_unknown_workflow_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-timeline-unknown"
    )
    response = await client_with_db.get(
        _workflows_url(org, f"/{uuid.uuid4()}/timeline"), headers=_auth_header(admin)
    )
    assert response.status_code == 404
