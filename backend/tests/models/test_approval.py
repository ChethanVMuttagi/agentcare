"""`ApprovalRequest` model tests against real PostgreSQL.

See tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL
and how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRequest, ApprovalStatus, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import WorkflowRun, WorkflowStep

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakeWorkflowRun = Callable[..., Awaitable[WorkflowRun]]
MakeWorkflowStep = Callable[..., Awaitable[WorkflowStep]]
MakeApproval = Callable[..., Awaitable[ApprovalRequest]]


async def _scenario(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    suffix: str,
) -> tuple[Organization, User, WorkflowRun, WorkflowStep]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    run = await make_workflow_run(org, user.id)
    step = await make_workflow_step(org, run, 1, step_type="coordination")
    return org, user, run, step


async def test_id_is_generated_uuid_and_defaults(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, _user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-defaults",
    )
    approval = await make_approval_request(org, run, step)

    assert isinstance(approval.id, uuid.UUID)
    assert approval.status is ApprovalStatus.PENDING
    assert approval.approved_by_user is None
    assert approval.approved_at is None
    assert approval.rejected_at is None
    assert approval.created_at is not None
    assert approval.created_at.tzinfo is not None


async def test_reason_empty_string_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, _user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-reason-empty",
    )
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.PENDING,
        reason="",
        requested_by_agent="coordinator",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_reason_oversized_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, _user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-reason-oversized",
    )
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.PENDING,
        reason="x" * 501,
        requested_by_agent="coordinator",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(approval)
    # Exceeds the `String(500)` column bound itself (a
    # `StringDataRightTruncationError`, surfaced as the broader
    # `DBAPIError` rather than a CHECK-constraint `IntegrityError`) —
    # never reaches the `reason_length` CHECK constraint at all.
    with pytest.raises(DBAPIError):
        await db_session.flush()


async def test_requested_by_agent_empty_string_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, _user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-agent-empty",
    )
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.PENDING,
        reason="A reason.",
        requested_by_agent="",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_pending_with_approved_by_user_set_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    """`approval_status_actor_consistency`: a `PENDING` approval must
    never have a resolving user set."""
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-consistency-pending",
    )
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.PENDING,
        reason="A reason.",
        requested_by_agent="coordinator",
        approved_by_user=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_approved_without_approved_by_user_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    """`approval_status_actor_consistency`: an `APPROVED` approval must
    always have a resolving user and `approved_at` set."""
    org, _user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-consistency-approved",
    )
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.APPROVED,
        reason="A reason.",
        requested_by_agent="coordinator",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_approved_with_both_approved_and_rejected_at_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-consistency-both",
    )
    now = datetime.now(UTC)
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.APPROVED,
        reason="A reason.",
        requested_by_agent="coordinator",
        approved_by_user=user.id,
        approved_at=now,
        rejected_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_expired_with_approved_by_user_set_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    """`EXPIRED` never has a human actor — auto-transitioned, not decided."""
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-consistency-expired",
    )
    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.EXPIRED,
        reason="A reason.",
        requested_by_agent="coordinator",
        approved_by_user=user.id,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_rejected_reuses_approved_by_user_column(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    """A `REJECTED` approval is a valid state with `approved_by_user`
    set — see the model docstring for why there is no separate
    `rejected_by_user` column."""
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-rejected-actor",
    )
    now = datetime.now(UTC)
    approval = await make_approval_request(
        org, run, step,
        status=ApprovalStatus.REJECTED,
        approved_by_user=user.id,
        rejected_at=now,
    )
    assert approval.status is ApprovalStatus.REJECTED
    assert approval.approved_by_user == user.id
    assert approval.approved_at is None


async def test_step_must_belong_to_same_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    """The composite FK guarantees `workflow_step_id` genuinely belongs
    to `workflow_run_id`, not merely to the same organization."""
    org, user, run_a, _step_a = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-step-run-a",
    )
    run_b = await make_workflow_run(org, user.id)
    step_b = await make_workflow_step(org, run_b, 1, step_type="coordination")

    approval = ApprovalRequest(
        organization_id=org.id,
        workflow_run_id=run_a.id,
        workflow_step_id=step_b.id,
        approval_type=ApprovalType.CUSTOM,
        status=ApprovalStatus.PENDING,
        reason="A reason.",
        requested_by_agent="coordinator",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(approval)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_repr_includes_status_and_type(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, _user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "appr-repr",
    )
    approval = await make_approval_request(org, run, step)
    text = repr(approval)
    assert "ApprovalRequest" in text
    assert "pending" in text.lower()
