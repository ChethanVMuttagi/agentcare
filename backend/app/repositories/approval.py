"""Tenant-scoped persistence/query operations for `ApprovalRequest`.

Every read here REQUIRES an `organization_id` — same discipline as every
other repository in this codebase (see `app.repositories.reminder`). This
module only adds, flushes, locks, and queries — it never commits, and it
makes no decision about whether a transition is currently ALLOWED (e.g.
"can a `REJECTED` approval be approved?") — see
`app.services.approval.ApprovalService`, which owns that decision, the
same layering `app.services.workflow.WorkflowService`/
`app.services.reminder.ReminderService` already established.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRequest, ApprovalStatus


async def create(session: AsyncSession, approval: ApprovalRequest) -> ApprovalRequest:
    """Add and flush a new `ApprovalRequest`. Does NOT commit — see
    `app.services.approval.ApprovalService.create_approval_request`."""
    session.add(approval)
    await session.flush()
    return approval


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, approval_id: uuid.UUID
) -> ApprovalRequest | None:
    """Return the approval with `approval_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.id == approval_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_id_for_update(
    session: AsyncSession, *, organization_id: uuid.UUID, approval_id: uuid.UUID
) -> ApprovalRequest | None:
    """Same as `get_by_id`, but locks the row (`SELECT ... FOR UPDATE`) —
    the primitive `ApprovalService` builds race-safe approve/reject/expire
    transitions on, mirroring
    `app.repositories.reminder.get_by_id_for_update` exactly, including
    its `populate_existing=True` (see
    `app.repositories.workflow_run.get_by_id_for_update`'s docstring for
    why a "for update" read must never return a session-cached,
    stale-attribute copy of an already-loaded row)."""
    result = await session.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.id == approval_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_pending_for_workflow_run(
    session: AsyncSession, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
) -> ApprovalRequest | None:
    """STORY-015: is this workflow run currently gated by a `PENDING`
    approval? Used by `app.ai.orchestration.AgentOrchestrationService`'s
    resume path to reject an attempt to resume via a plain follow-up
    request when the run is actually waiting on an approval DECISION,
    not additional natural-language input — those two "waiting" reasons
    share the same `WorkflowStatus.WAITING` value and must be
    disambiguated before resuming (see
    docs/adr/ADR-0014-end-to-end-administrative-workflows.md)."""
    result = await session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.workflow_run_id == workflow_run_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    )
    return result.scalar_one_or_none()


async def list_pending(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ApprovalRequest]:
    """The actionable approval queue for one organization: `PENDING`
    approvals only, oldest first (the ones that have waited longest are
    reviewed first) — never an already-resolved or expired approval. See
    `app.api.v1.endpoints.approvals.list_approvals`."""
    result = await session.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
        .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def count_by_status(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[ApprovalStatus, int]:
    """Organization-wide approval counts grouped by `status` — the
    approvals breakdown for the Milestone B analytics summary
    (`app.api.v1.endpoints.analytics`)."""
    result = await session.execute(
        select(ApprovalRequest.status, func.count())
        .where(ApprovalRequest.organization_id == organization_id)
        .group_by(ApprovalRequest.status)
    )
    return {status: count for status, count in result.all()}


async def approve(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    approval_id: uuid.UUID,
    approved_by_user: uuid.UUID,
    approved_at: datetime,
) -> ApprovalRequest | None:
    """`PENDING` -> `APPROVED`. Returns `None` if no such approval exists
    in this organization. Does NOT validate that the CURRENT status is
    actually approvable — see `ApprovalService.approve`, which checks
    that (and lazy-expiration) before ever calling this. Does NOT
    commit."""
    approval = await get_by_id_for_update(
        session, organization_id=organization_id, approval_id=approval_id
    )
    if approval is None:
        return None
    approval.status = ApprovalStatus.APPROVED
    approval.approved_by_user = approved_by_user
    approval.approved_at = approved_at
    await session.flush()
    return approval


async def reject(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    approval_id: uuid.UUID,
    rejected_by_user: uuid.UUID,
    rejected_at: datetime,
) -> ApprovalRequest | None:
    """`PENDING` -> `REJECTED`. `rejected_by_user` is stored in
    `approved_by_user` — see `app.models.approval.ApprovalRequest`'s
    class docstring for why one column records the resolving user for
    EITHER terminal outcome. Returns `None` if no such approval exists in
    this organization. Does NOT validate the current status — see
    `ApprovalService.reject`. Does NOT commit."""
    approval = await get_by_id_for_update(
        session, organization_id=organization_id, approval_id=approval_id
    )
    if approval is None:
        return None
    approval.status = ApprovalStatus.REJECTED
    approval.approved_by_user = rejected_by_user
    approval.rejected_at = rejected_at
    await session.flush()
    return approval


async def expire(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    approval_id: uuid.UUID,
) -> ApprovalRequest | None:
    """`PENDING` -> `EXPIRED`. No human actor is recorded (see the
    `approval_status_actor_consistency` CHECK constraint). Returns `None`
    if no such approval exists in this organization. Does NOT validate
    the current status or that `expires_at` has actually passed — see
    `ApprovalService`, which checks both before ever calling this. Does
    NOT commit."""
    approval = await get_by_id_for_update(
        session, organization_id=organization_id, approval_id=approval_id
    )
    if approval is None:
        return None
    approval.status = ApprovalStatus.EXPIRED
    await session.flush()
    return approval
