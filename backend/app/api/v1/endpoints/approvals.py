"""Approval endpoints: human-in-the-loop approval creation, inspection,
and decision (STORY-014).

RBAC (see docs/RBAC.md and docs/adr/ADR-0013-human-in-the-loop-approvals.md):
`ADMIN`/`STAFF`/`SUPERVISOR` may create an approval request for an
existing, currently running workflow step, and list/inspect approvals in
their organization. Deciding an approval (`approve`/`reject`) is
restricted further, to `ADMIN`/`SUPERVISOR` ONLY — `STAFF` may raise a
request but not resolve one. `PATIENT` cannot reach any route in this
module at all — approvals are an internal operational concern, never a
patient-facing surface.

## No Business Logic Here

Every route is a thin translation from HTTP to `ApprovalService` and
back — creation, transition validation, lazy expiration, and workflow
pause/resume orchestration all live in `app.services.approval`, never
here. See that module's docstring for the full state machine.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.core.pagination import MAX_PAGE_SIZE
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.schemas.approval import (
    ApprovalRequestCreate,
    ApprovalRequestListResponse,
    ApprovalRequestResponse,
)
from app.services.approval import ApprovalService

router = APIRouter(prefix="/organizations/{organization_id}/approvals")

_require_view_access = require_roles(Role.ADMIN, Role.STAFF, Role.SUPERVISOR)
_require_decision_access = require_roles(Role.ADMIN, Role.SUPERVISOR)


@router.post("", response_model=ApprovalRequestResponse, status_code=201)
async def create_approval(
    organization_id: uuid.UUID,
    payload: ApprovalRequestCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_view_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApprovalRequestResponse:
    """Manually request approval for an existing, currently `RUNNING`
    workflow step — pauses that step and its run. `requested_by_agent`
    is always server-set to `"manual"` for this route (never client-
    supplied) — see `ApprovalService.create_approval_request`."""
    service = ApprovalService(session)
    approval = await service.create_approval_request(
        organization_id=organization_id,
        workflow_run_id=payload.workflow_run_id,
        workflow_step_id=payload.workflow_step_id,
        approval_type=payload.approval_type,
        reason=payload.reason,
        actor_identifier=str(current_user.id),
    )
    return ApprovalRequestResponse.model_validate(approval)


@router.get("", response_model=ApprovalRequestListResponse)
async def list_approvals(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_view_access)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalRequestListResponse:
    """List the organization's actionable approval queue — `PENDING`
    approvals only, oldest first."""
    service = ApprovalService(session)
    approvals = await service.list_pending_approvals(
        organization_id=organization_id, limit=limit, offset=offset
    )
    return ApprovalRequestListResponse(
        approvals=[ApprovalRequestResponse.model_validate(a) for a in approvals]
    )


@router.get("/{approval_id}", response_model=ApprovalRequestResponse)
async def get_approval(
    organization_id: uuid.UUID,
    approval_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_view_access)],
) -> ApprovalRequestResponse:
    """Retrieve one approval by id, any status — including an already
    resolved or expired one, for audit lookup."""
    service = ApprovalService(session)
    approval = await service.get_approval(organization_id=organization_id, approval_id=approval_id)
    return ApprovalRequestResponse.model_validate(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalRequestResponse)
async def approve_approval(
    organization_id: uuid.UUID,
    approval_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_decision_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApprovalRequestResponse:
    """Approve a `PENDING` approval, then resume and complete the paused
    step/run. `ADMIN`/`SUPERVISOR` ONLY. The resolving user is always the
    authenticated caller — never client-supplied."""
    service = ApprovalService(session)
    approval = await service.approve(
        organization_id=organization_id,
        approval_id=approval_id,
        approved_by_user=current_user.id,
        actor_identifier=str(current_user.id),
    )
    return ApprovalRequestResponse.model_validate(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalRequestResponse)
async def reject_approval(
    organization_id: uuid.UUID,
    approval_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_decision_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApprovalRequestResponse:
    """Reject a `PENDING` approval, then resume the paused step/run and
    bring both to a terminal, cancelled state. `ADMIN`/`SUPERVISOR`
    ONLY."""
    service = ApprovalService(session)
    approval = await service.reject(
        organization_id=organization_id,
        approval_id=approval_id,
        rejected_by_user=current_user.id,
        actor_identifier=str(current_user.id),
    )
    return ApprovalRequestResponse.model_validate(approval)
