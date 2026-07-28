"""Approval API schemas.

Never returns SQLAlchemy models directly. `ApprovalRequestCreate`
deliberately has no `requested_by_agent`/`expires_at`/`status` fields —
those are always server-derived (see `app.services.approval.ApprovalService.create_approval_request`
and docs/adr/ADR-0013-human-in-the-loop-approvals.md), never client-
supplied. `ApproveRejectRequest` has no fields at all: WHO approved/
rejected is always the authenticated caller, never a client-supplied id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.approval import REASON_MAX_LENGTH, ApprovalStatus, ApprovalType


class ApprovalRequestCreate(BaseModel):
    """`POST .../approvals` request body — manually request approval for
    an existing, currently `RUNNING` workflow step (e.g. staff flagging
    that a step needs a second sign-off). The Coordinator agent's own
    approval requests (STORY-014) go through
    `ApprovalService.create_approval_request` directly, not this route."""

    workflow_run_id: uuid.UUID
    workflow_step_id: uuid.UUID
    approval_type: ApprovalType
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)


class ApprovalRequestResponse(BaseModel):
    """An approval's state, as returned by the API."""

    id: uuid.UUID
    organization_id: uuid.UUID
    workflow_run_id: uuid.UUID
    workflow_step_id: uuid.UUID
    approval_type: ApprovalType
    status: ApprovalStatus
    reason: str
    requested_by_agent: str
    approved_by_user: uuid.UUID | None
    approved_at: datetime | None
    rejected_at: datetime | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRequestListResponse(BaseModel):
    """`GET .../approvals` response envelope — the `PENDING` approval
    queue only, see `app.repositories.approval.list_pending`."""

    approvals: list[ApprovalRequestResponse]
