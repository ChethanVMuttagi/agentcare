"""Approval endpoint tests: creation, inspection, approve/reject —
end-to-end over real HTTP (via `client_with_db`), against real
PostgreSQL. See docs/adr/ADR-0013-human-in-the-loop-approvals.md for the
full RBAC matrix.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.approval import ApprovalRequest, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import ActorType, WorkflowRequestType, WorkflowRun, WorkflowStep
from app.services.approval import ApprovalService
from app.services.workflow import WorkflowService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakeApproval = Callable[..., Awaitable[ApprovalRequest]]

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


def _approvals_url(organization: Organization, suffix: str = "") -> str:
    return f"/api/v1/organizations/{organization.id}/approvals{suffix}"


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


async def _running_run_and_step(
    db_session: AsyncSession, org: Organization, admin: User
) -> tuple[WorkflowRun, WorkflowStep]:
    """A `RUNNING` run/step, ready to be paused via `POST .../approvals`
    — mirrors the coordination-step shape a real Coordinator decision
    would find."""
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
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="coordination"
    )
    step = await service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="coordinator",
    )
    return run, step


async def _paused_approval(
    db_session: AsyncSession, org: Organization, admin: User
) -> ApprovalRequest:
    """A REAL, genuinely-paused `PENDING` approval — created through
    `ApprovalService.create_approval_request` (not the raw
    `make_approval_request` fixture) so the underlying step/run are
    ACTUALLY `WAITING`, exactly as `approve`/`reject` require. Used by
    every test below that goes on to call approve/reject — see
    `app.services.approval.ApprovalService` for why a `PENDING` approval
    and a `WAITING` step/run are always the same fact."""
    run, step = await _running_run_and_step(db_session, org, admin)
    service = ApprovalService(db_session)
    return await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.HIGH_RISK_ACTION,
        reason="Needs a decision.",
        actor_identifier=str(admin.id),
    )


# --- POST .../approvals ---


async def test_admin_can_create_approval(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-create-admin"
    )
    run, step = await _running_run_and_step(db_session, org, admin)

    response = await client_with_db.post(
        _approvals_url(org),
        json={
            "workflow_run_id": str(run.id),
            "workflow_step_id": str(step.id),
            "approval_type": "high_risk_action",
            "reason": "Needs a second sign-off.",
        },
        headers=_auth_header(admin),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["requested_by_agent"] == "manual"


async def test_staff_can_create_approval(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-create-staff-admin"
    )
    staff = await make_user("api-appr-create-staff")
    await make_membership(org, staff, role=Role.STAFF)
    run, step = await _running_run_and_step(db_session, org, admin)

    response = await client_with_db.post(
        _approvals_url(org),
        json={
            "workflow_run_id": str(run.id),
            "workflow_step_id": str(step.id),
            "approval_type": "custom",
            "reason": "Reason.",
        },
        headers=_auth_header(staff),
    )
    assert response.status_code == 201


async def test_patient_cannot_create_approval(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-create-patient-admin"
    )
    patient_user = await make_user("api-appr-create-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)
    run, step = await _running_run_and_step(db_session, org, admin)

    response = await client_with_db.post(
        _approvals_url(org),
        json={
            "workflow_run_id": str(run.id),
            "workflow_step_id": str(step.id),
            "approval_type": "custom",
            "reason": "Reason.",
        },
        headers=_auth_header(patient_user),
    )
    assert response.status_code == 403


async def test_create_approval_for_non_running_step_returns_conflict(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-create-conflict"
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
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="coordination"
    )

    response = await client_with_db.post(
        _approvals_url(org),
        json={
            "workflow_run_id": str(run.id),
            "workflow_step_id": str(step.id),
            "approval_type": "custom",
            "reason": "Reason.",
        },
        headers=_auth_header(admin),
    )
    assert response.status_code == 409


# --- GET .../approvals (list) ---


async def test_list_approvals_returns_only_pending(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-list"
    )
    run, step = await _running_run_and_step(db_session, org, admin)
    pending = await make_approval_request(org, run, step)

    response = await client_with_db.get(_approvals_url(org), headers=_auth_header(admin))
    assert response.status_code == 200
    ids = [a["id"] for a in response.json()["approvals"]]
    assert ids == [str(pending.id)]


async def test_list_approvals_is_tenant_scoped(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org_a, admin_a = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-list-tenant-a"
    )
    org_b, admin_b = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-list-tenant-b"
    )
    run_b, step_b = await _running_run_and_step(db_session, org_b, admin_b)
    await make_approval_request(org_b, run_b, step_b)

    response = await client_with_db.get(_approvals_url(org_a), headers=_auth_header(admin_a))
    assert response.status_code == 200
    assert response.json()["approvals"] == []


async def test_patient_cannot_list_approvals(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-appr-list-patient")
    patient_user = await make_user("api-appr-list-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(_approvals_url(org), headers=_auth_header(patient_user))
    assert response.status_code == 403


# --- GET .../approvals/{id} ---


async def test_admin_can_get_approval_by_id(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-get"
    )
    run, step = await _running_run_and_step(db_session, org, admin)
    approval = await make_approval_request(org, run, step)

    response = await client_with_db.get(
        _approvals_url(org, f"/{approval.id}"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(approval.id)


async def test_get_unknown_approval_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-get-unknown"
    )
    response = await client_with_db.get(
        _approvals_url(org, f"/{uuid.uuid4()}"), headers=_auth_header(admin)
    )
    assert response.status_code == 404


async def test_cross_tenant_approval_lookup_returns_not_found(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org_a, admin_a = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-cross-a"
    )
    org_b, admin_b = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-cross-b"
    )
    run_b, step_b = await _running_run_and_step(db_session, org_b, admin_b)
    approval_b = await make_approval_request(org_b, run_b, step_b)

    response = await client_with_db.get(
        _approvals_url(org_a, f"/{approval_b.id}"), headers=_auth_header(admin_a)
    )
    assert response.status_code == 404


# --- POST .../approvals/{id}/approve ---


async def test_admin_can_approve(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-approve-admin"
    )
    approval = await _paused_approval(db_session, org, admin)

    response = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/approve"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_by_user"] == str(admin.id)


async def test_supervisor_can_approve(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-approve-supervisor-admin"
    )
    supervisor = await make_user("api-appr-approve-supervisor")
    await make_membership(org, supervisor, role=Role.SUPERVISOR)
    approval = await _paused_approval(db_session, org, admin)

    response = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/approve"), headers=_auth_header(supervisor)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_staff_cannot_approve(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-approve-staff-admin"
    )
    staff = await make_user("api-appr-approve-staff")
    await make_membership(org, staff, role=Role.STAFF)
    run, step = await _running_run_and_step(db_session, org, admin)
    approval = await make_approval_request(org, run, step)

    response = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/approve"), headers=_auth_header(staff)
    )
    assert response.status_code == 403


async def test_patient_cannot_approve(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-approve-patient-admin"
    )
    patient_user = await make_user("api-appr-approve-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)
    run, step = await _running_run_and_step(db_session, org, admin)
    approval = await make_approval_request(org, run, step)

    response = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/approve"), headers=_auth_header(patient_user)
    )
    assert response.status_code == 403


async def test_approve_already_resolved_returns_unprocessable(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-approve-resolved"
    )
    approval = await _paused_approval(db_session, org, admin)

    first = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/approve"), headers=_auth_header(admin)
    )
    assert first.status_code == 200

    second = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/approve"), headers=_auth_header(admin)
    )
    assert second.status_code == 422


async def test_approve_unknown_approval_returns_not_found(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-approve-unknown"
    )
    response = await client_with_db.post(
        _approvals_url(org, f"/{uuid.uuid4()}/approve"), headers=_auth_header(admin)
    )
    assert response.status_code == 404


# --- POST .../approvals/{id}/reject ---


async def test_admin_can_reject(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-reject-admin"
    )
    approval = await _paused_approval(db_session, org, admin)

    response = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/reject"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["approved_by_user"] == str(admin.id)
    assert body["rejected_at"] is not None


async def test_staff_cannot_reject(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-reject-staff-admin"
    )
    staff = await make_user("api-appr-reject-staff")
    await make_membership(org, staff, role=Role.STAFF)
    run, step = await _running_run_and_step(db_session, org, admin)
    approval = await make_approval_request(org, run, step)

    response = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/reject"), headers=_auth_header(staff)
    )
    assert response.status_code == 403


async def test_reject_already_resolved_returns_unprocessable(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-reject-resolved"
    )
    approval = await _paused_approval(db_session, org, admin)

    first = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/reject"), headers=_auth_header(admin)
    )
    assert first.status_code == 200

    second = await client_with_db.post(
        _approvals_url(org, f"/{approval.id}/reject"), headers=_auth_header(admin)
    )
    assert second.status_code == 422


async def test_approval_response_never_leaks_unexpected_fields(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_approval_request: MakeApproval,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-appr-fields"
    )
    run, step = await _running_run_and_step(db_session, org, admin)
    approval = await make_approval_request(org, run, step)

    response = await client_with_db.get(
        _approvals_url(org, f"/{approval.id}"), headers=_auth_header(admin)
    )
    assert response.status_code == 200
    expected_keys = {
        "id",
        "organization_id",
        "workflow_run_id",
        "workflow_step_id",
        "approval_type",
        "status",
        "reason",
        "requested_by_agent",
        "approved_by_user",
        "approved_at",
        "rejected_at",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert set(response.json().keys()) == expected_keys
