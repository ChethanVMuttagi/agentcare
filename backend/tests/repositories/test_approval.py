"""`app.repositories.approval` tests against real PostgreSQL.

Covers tenant-scoped CRUD/lookup, `get_by_id_for_update`'s row lock,
`list_pending`'s scoping/ordering, and the `approve`/`reject`/`expire`
mutators (each proven to apply the correct columns; transition VALIDITY
is `app.services.approval.ApprovalService`'s job, tested separately).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import WorkflowRun, WorkflowStep
from app.repositories import approval as approval_repository

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


async def test_create_and_get_by_id(
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
        "repo-appr-create",
    )
    approval = await make_approval_request(org, run, step)

    fetched = await approval_repository.get_by_id(
        db_session, organization_id=org.id, approval_id=approval.id
    )
    assert fetched is not None
    assert fetched.id == approval.id
    assert fetched.status is ApprovalStatus.PENDING


async def test_get_by_id_returns_none_for_wrong_organization(
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
        "repo-appr-wrong-org",
    )
    approval = await make_approval_request(org, run, step)
    other_org = await make_organization("repo-appr-wrong-org-other")

    fetched = await approval_repository.get_by_id(
        db_session, organization_id=other_org.id, approval_id=approval.id
    )
    assert fetched is None


async def test_get_by_id_returns_none_for_unknown_id(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-appr-unknown")
    fetched = await approval_repository.get_by_id(
        db_session, organization_id=org.id, approval_id=uuid.uuid4()
    )
    assert fetched is None


async def test_get_by_id_for_update_locks_and_returns_the_row(
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
        "repo-appr-lock",
    )
    approval = await make_approval_request(org, run, step)

    locked = await approval_repository.get_by_id_for_update(
        db_session, organization_id=org.id, approval_id=approval.id
    )
    assert locked is not None
    assert locked.id == approval.id


async def test_list_pending_returns_only_pending_oldest_first(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-list-pending",
    )
    base = datetime.now(UTC)
    older = await make_approval_request(org, run, step, status=ApprovalStatus.PENDING)
    older.created_at = base
    run2 = await make_workflow_run(org, user.id)
    step2 = await make_workflow_step(org, run2, 1, step_type="coordination")
    newer = await make_approval_request(org, run2, step2, status=ApprovalStatus.PENDING)
    newer.created_at = base + timedelta(seconds=1)
    run3 = await make_workflow_run(org, user.id)
    step3 = await make_workflow_step(org, run3, 1, step_type="coordination")
    await make_approval_request(
        org,
        run3,
        step3,
        status=ApprovalStatus.APPROVED,
        approved_by_user=user.id,
        approved_at=datetime.now(UTC),
    )
    await db_session.flush()

    results = await approval_repository.list_pending(db_session, organization_id=org.id)
    assert [a.id for a in results] == [older.id, newer.id]


async def test_list_pending_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org_a, user_a, run_a, step_a = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-tenant-a",
    )
    org_b, user_b, run_b, step_b = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-tenant-b",
    )
    approval_a = await make_approval_request(org_a, run_a, step_a)
    await make_approval_request(org_b, run_b, step_b)

    results = await approval_repository.list_pending(db_session, organization_id=org_a.id)
    assert [a.id for a in results] == [approval_a.id]


async def test_list_pending_respects_limit_and_offset(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, user, _run, _step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-list-limit",
    )
    # Explicit, strictly increasing `created_at` values: Windows' system
    # clock resolution is coarse enough that three back-to-back
    # `datetime.now()` calls can tie, which would make `list_pending`'s
    # `(created_at, id)` ordering flaky (`id` is a random UUID, not a
    # valid tiebreaker) — never rely on wall-clock granularity alone.
    base = datetime.now(UTC)
    ids: list[uuid.UUID] = []
    for i in range(3):
        run = await make_workflow_run(org, user.id)
        step = await make_workflow_step(org, run, 1, step_type="coordination")
        approval = await make_approval_request(org, run, step)
        approval.created_at = base + timedelta(seconds=i)
        await db_session.flush()
        ids.append(approval.id)

    page = await approval_repository.list_pending(
        db_session, organization_id=org.id, limit=1, offset=1
    )
    assert [a.id for a in page] == [ids[1]]


async def test_approve_sets_status_actor_and_timestamp(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-approve",
    )
    approval = await make_approval_request(org, run, step)

    approved_at = datetime.now(UTC)
    result = await approval_repository.approve(
        db_session,
        organization_id=org.id,
        approval_id=approval.id,
        approved_by_user=user.id,
        approved_at=approved_at,
    )
    assert result is not None
    assert result.status is ApprovalStatus.APPROVED
    assert result.approved_by_user == user.id
    assert result.approved_at == approved_at
    assert result.rejected_at is None


async def test_approve_returns_none_for_unknown_approval(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("repo-appr-approve-unknown")
    user = await make_user("repo-appr-approve-unknown")
    await make_membership(org, user, role=Role.ADMIN)

    result = await approval_repository.approve(
        db_session,
        organization_id=org.id,
        approval_id=uuid.uuid4(),
        approved_by_user=user.id,
        approved_at=datetime.now(UTC),
    )
    assert result is None


async def test_reject_sets_status_actor_and_timestamp(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-reject",
    )
    approval = await make_approval_request(org, run, step)

    rejected_at = datetime.now(UTC)
    result = await approval_repository.reject(
        db_session,
        organization_id=org.id,
        approval_id=approval.id,
        rejected_by_user=user.id,
        rejected_at=rejected_at,
    )
    assert result is not None
    assert result.status is ApprovalStatus.REJECTED
    assert result.approved_by_user == user.id
    assert result.rejected_at == rejected_at
    assert result.approved_at is None


async def test_reject_returns_none_for_unknown_approval(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("repo-appr-reject-unknown")
    user = await make_user("repo-appr-reject-unknown")
    await make_membership(org, user, role=Role.ADMIN)

    result = await approval_repository.reject(
        db_session,
        organization_id=org.id,
        approval_id=uuid.uuid4(),
        rejected_by_user=user.id,
        rejected_at=datetime.now(UTC),
    )
    assert result is None


async def test_expire_sets_status_with_no_actor(
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
        "repo-appr-expire",
    )
    approval = await make_approval_request(
        org, run, step, expires_at=datetime.now(UTC) - timedelta(hours=1)
    )

    result = await approval_repository.expire(
        db_session, organization_id=org.id, approval_id=approval.id
    )
    assert result is not None
    assert result.status is ApprovalStatus.EXPIRED
    assert result.approved_by_user is None
    assert result.approved_at is None
    assert result.rejected_at is None


async def test_expire_returns_none_for_unknown_approval(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-appr-expire-unknown")
    result = await approval_repository.expire(
        db_session, organization_id=org.id, approval_id=uuid.uuid4()
    )
    assert result is None


# --- get_pending_for_workflow_run (STORY-015) ---


async def test_get_pending_for_workflow_run_returns_pending_approval(
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
        "repo-appr-pending-for-run",
    )
    approval = await make_approval_request(org, run, step, status=ApprovalStatus.PENDING)

    result = await approval_repository.get_pending_for_workflow_run(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert result is not None
    assert result.id == approval.id


async def test_get_pending_for_workflow_run_ignores_resolved_approvals(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
    make_approval_request: MakeApproval,
) -> None:
    org, user, run, step = await _scenario(
        make_organization, make_user, make_membership, make_workflow_run, make_workflow_step,
        "repo-appr-pending-resolved",
    )
    await make_approval_request(
        org,
        run,
        step,
        status=ApprovalStatus.APPROVED,
        approved_by_user=user.id,
        approved_at=datetime.now(UTC),
    )

    result = await approval_repository.get_pending_for_workflow_run(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert result is None


async def test_get_pending_for_workflow_run_returns_none_for_unknown_run(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-appr-pending-unknown-run")
    result = await approval_repository.get_pending_for_workflow_run(
        db_session, organization_id=org.id, workflow_run_id=uuid.uuid4()
    )
    assert result is None
