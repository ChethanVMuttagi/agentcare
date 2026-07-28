"""`app.services.approval.ApprovalService` tests against real PostgreSQL.

Sequential correctness only — the mandatory GENUINE concurrency proof
lives in tests/db/test_approval_concurrency.py, and the mandatory
full-lifecycle proof (Coordinator pauses -> human decides -> workflow
resumes/completes) lives in tests/db/test_approval_e2e.py. This file
proves business-rule validation, state transitions, transition+event
atomicity, workflow pause/resume orchestration, and lazy expiration.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalStatus, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.services.approval import (
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalService,
    InvalidApprovalTransitionError,
)
from app.services.workflow import (
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowStepNotFoundError,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]

_ACTOR_ID = "synthetic-approval-service-test"


async def _running_scenario(
    workflow_service: WorkflowService,
    *,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, User, WorkflowRun, WorkflowStep]:
    """An org, an ADMIN member, and a RUNNING run/step ready to be
    paused for approval — mirrors the coordination-step shape
    `app.ai.orchestration.AgentOrchestrationService` always produces
    before a Coordinator decision is made."""
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    run = await workflow_service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    await workflow_service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    step = await workflow_service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="coordination"
    )
    step = await workflow_service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="coordinator",
    )
    return org, user, run, step


# --- create_approval_request ---


async def test_create_approval_request_pauses_step_and_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, _user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-create",
    )

    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.HIGH_RISK_ACTION,
        reason="Needs a second sign-off.",
        actor_identifier="coordinator",
        requested_by_agent="coordinator",
        actor_type=ActorType.AGENT,
    )

    assert approval.status is ApprovalStatus.PENDING
    assert approval.requested_by_agent == "coordinator"

    refreshed_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_run.status is WorkflowStatus.WAITING
    refreshed_steps = await workflow_service.list_steps(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_steps[0].status is StepStatus.WAITING

    events = await workflow_service.list_events(organization_id=org.id, workflow_run_id=run.id)
    event_types = [e.event_type for e in events]
    assert WorkflowEventType.APPROVAL_REQUESTED in event_types
    assert WorkflowEventType.STEP_WAITING in event_types
    assert WorkflowEventType.WORKFLOW_WAITING in event_types


async def test_create_approval_request_defaults_requested_by_agent_to_manual(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-manual",
    )

    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Staff flagged this manually.",
        actor_identifier=str(user.id),
    )
    assert approval.requested_by_agent == "manual"


async def test_create_approval_request_unknown_run_raises_not_found(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-appr-create-unknown-run")
    service = ApprovalService(db_session)
    with pytest.raises(WorkflowNotFoundError):
        await service.create_approval_request(
            organization_id=org.id,
            workflow_run_id=uuid.uuid4(),
            workflow_step_id=uuid.uuid4(),
            approval_type=ApprovalType.CUSTOM,
            reason="Reason.",
            actor_identifier="tester",
        )


async def test_create_approval_request_unknown_step_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, _step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-create-unknown-step",
    )

    with pytest.raises(WorkflowStepNotFoundError):
        await service.create_approval_request(
            organization_id=org.id,
            workflow_run_id=run.id,
            workflow_step_id=uuid.uuid4(),
            approval_type=ApprovalType.CUSTOM,
            reason="Reason.",
            actor_identifier=str(user.id),
        )


async def test_create_approval_request_rejects_when_step_not_running(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org = await make_organization("svc-appr-create-not-running")
    user = await make_user("svc-appr-create-not-running")
    await make_membership(org, user, role=Role.ADMIN)
    run = await workflow_service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    step = await workflow_service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="coordination"
    )

    with pytest.raises(WorkflowConflictError):
        await service.create_approval_request(
            organization_id=org.id,
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            approval_type=ApprovalType.CUSTOM,
            reason="Reason.",
            actor_identifier=str(user.id),
        )


# --- get / list ---


async def test_get_approval_unknown_raises_not_found(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-appr-get-unknown")
    service = ApprovalService(db_session)
    with pytest.raises(ApprovalNotFoundError):
        await service.get_approval(organization_id=org.id, approval_id=uuid.uuid4())


async def test_list_pending_approvals_scoped_and_ordered(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-list",
    )
    first = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="First.",
        actor_identifier=str(user.id),
    )

    results = await service.list_pending_approvals(organization_id=org.id)
    assert [a.id for a in results] == [first.id]


# --- approve ---


async def test_approve_resumes_and_completes_workflow(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-approve",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
    )

    resolved = await service.approve(
        organization_id=org.id,
        approval_id=approval.id,
        approved_by_user=user.id,
        actor_identifier=str(user.id),
    )
    assert resolved.status is ApprovalStatus.APPROVED
    assert resolved.approved_by_user == user.id
    assert resolved.approved_at is not None

    refreshed_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_run.status is WorkflowStatus.COMPLETED
    refreshed_steps = await workflow_service.list_steps(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_steps[0].status is StepStatus.COMPLETED

    events = await workflow_service.list_events(organization_id=org.id, workflow_run_id=run.id)
    event_types = [e.event_type for e in events]
    assert WorkflowEventType.APPROVAL_GRANTED in event_types
    assert WorkflowEventType.STEP_RESUMED in event_types
    assert WorkflowEventType.WORKFLOW_RESUMED in event_types
    assert WorkflowEventType.WORKFLOW_COMPLETED in event_types


async def test_approve_unknown_approval_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("svc-appr-approve-unknown")
    user = await make_user("svc-appr-approve-unknown")
    await make_membership(org, user, role=Role.ADMIN)
    service = ApprovalService(db_session)
    with pytest.raises(ApprovalNotFoundError):
        await service.approve(
            organization_id=org.id,
            approval_id=uuid.uuid4(),
            approved_by_user=user.id,
            actor_identifier=str(user.id),
        )


async def test_approve_already_approved_raises_invalid_transition(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-approve-twice",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
    )
    await service.approve(
        organization_id=org.id,
        approval_id=approval.id,
        approved_by_user=user.id,
        actor_identifier=str(user.id),
    )

    with pytest.raises(InvalidApprovalTransitionError):
        await service.approve(
            organization_id=org.id,
            approval_id=approval.id,
            approved_by_user=user.id,
            actor_identifier=str(user.id),
        )


async def test_approve_expired_approval_auto_expires_and_raises(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-approve-expired",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
        expiry=timedelta(seconds=-1),
    )

    with pytest.raises(ApprovalExpiredError):
        await service.approve(
            organization_id=org.id,
            approval_id=approval.id,
            approved_by_user=user.id,
            actor_identifier=str(user.id),
        )

    expired = await service.get_approval(organization_id=org.id, approval_id=approval.id)
    assert expired.status is ApprovalStatus.EXPIRED

    refreshed_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_run.status is WorkflowStatus.FAILED
    assert refreshed_run.failure_code == "approval_expired"


# --- reject ---


async def test_reject_resumes_and_cancels_workflow(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-reject",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
    )

    resolved = await service.reject(
        organization_id=org.id,
        approval_id=approval.id,
        rejected_by_user=user.id,
        actor_identifier=str(user.id),
    )
    assert resolved.status is ApprovalStatus.REJECTED
    assert resolved.approved_by_user == user.id
    assert resolved.rejected_at is not None

    refreshed_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_run.status is WorkflowStatus.CANCELLED
    refreshed_steps = await workflow_service.list_steps(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_steps[0].status is StepStatus.FAILED
    assert refreshed_steps[0].failure_code == "approval_rejected"

    events = await workflow_service.list_events(organization_id=org.id, workflow_run_id=run.id)
    event_types = [e.event_type for e in events]
    assert WorkflowEventType.APPROVAL_REJECTED in event_types
    assert WorkflowEventType.WORKFLOW_CANCELLED in event_types


async def test_reject_already_rejected_raises_invalid_transition(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-reject-twice",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
    )
    await service.reject(
        organization_id=org.id,
        approval_id=approval.id,
        rejected_by_user=user.id,
        actor_identifier=str(user.id),
    )

    with pytest.raises(InvalidApprovalTransitionError):
        await service.reject(
            organization_id=org.id,
            approval_id=approval.id,
            rejected_by_user=user.id,
            actor_identifier=str(user.id),
        )


async def test_reject_expired_approval_auto_expires_and_raises(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-reject-expired",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
        expiry=timedelta(seconds=-1),
    )

    with pytest.raises(ApprovalExpiredError):
        await service.reject(
            organization_id=org.id,
            approval_id=approval.id,
            rejected_by_user=user.id,
            actor_identifier=str(user.id),
        )

    expired = await service.get_approval(organization_id=org.id, approval_id=approval.id)
    assert expired.status is ApprovalStatus.EXPIRED


# --- expire_approval ---


async def test_expire_approval_fails_step_and_workflow_directly_from_waiting(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-expire-explicit",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
    )

    expired = await service.expire_approval(organization_id=org.id, approval_id=approval.id)
    assert expired.status is ApprovalStatus.EXPIRED

    refreshed_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_run.status is WorkflowStatus.FAILED
    refreshed_steps = await workflow_service.list_steps(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert refreshed_steps[0].status is StepStatus.FAILED
    assert refreshed_steps[0].failure_code == "approval_expired"

    # No resume event: expiry never resumes execution to act on.
    events = await workflow_service.list_events(organization_id=org.id, workflow_run_id=run.id)
    event_types = [e.event_type for e in events]
    assert WorkflowEventType.STEP_RESUMED not in event_types
    assert WorkflowEventType.WORKFLOW_RESUMED not in event_types


async def test_expire_approval_unknown_raises_not_found(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-appr-expire-unknown")
    service = ApprovalService(db_session)
    with pytest.raises(ApprovalNotFoundError):
        await service.expire_approval(organization_id=org.id, approval_id=uuid.uuid4())


async def test_expire_approval_already_approved_raises_invalid_transition(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    workflow_service = WorkflowService(db_session)
    service = ApprovalService(db_session)
    org, user, run, step = await _running_scenario(
        workflow_service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="svc-appr-expire-already-approved",
    )
    approval = await service.create_approval_request(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        reason="Reason.",
        actor_identifier=str(user.id),
    )
    await service.approve(
        organization_id=org.id,
        approval_id=approval.id,
        approved_by_user=user.id,
        actor_identifier=str(user.id),
    )

    with pytest.raises(InvalidApprovalTransitionError):
        await service.expire_approval(organization_id=org.id, approval_id=approval.id)
