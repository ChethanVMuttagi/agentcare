"""Workflow API schemas.

Never returns SQLAlchemy models directly. These response models expose
only administrative/state fields — no raw exception detail, no prompts,
no chain-of-thought, and (for STORY-009) no client-supplied
`safe_metadata` input surface at all: nothing in this module lets an API
caller write arbitrary JSON into `WorkflowEvent.safe_metadata`. See
docs/WORKFLOWS.md "Privacy".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowStatus,
)


class WorkflowRunCreate(BaseModel):
    """`POST .../workflows` request body.

    `patient_id`, when supplied by an `ADMIN`/`STAFF` caller, must name a
    patient in the same organization. A `PATIENT`-role caller's value
    here is ignored (never trusted) — the route always substitutes their
    own linked patient id; see `app.api.v1.endpoints.workflows`. There is
    deliberately no `correlation_id` field: correlation ids are always
    server-generated (see docs/WORKFLOWS.md "Correlation Strategy").
    """

    request_type: WorkflowRequestType
    patient_id: uuid.UUID | None = None
    idempotency_key: str | None = None


class WorkflowRunResponse(BaseModel):
    """A workflow run's state, as returned by the API."""

    id: uuid.UUID
    organization_id: uuid.UUID
    patient_id: uuid.UUID | None
    initiated_by_user_id: uuid.UUID
    request_type: WorkflowRequestType
    status: WorkflowStatus
    current_step: int | None
    correlation_id: str
    idempotency_key: str | None
    failure_code: str | None
    failure_message_safe: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowRunListResponse(BaseModel):
    """`GET .../workflows` response envelope."""

    workflows: list[WorkflowRunResponse]


class WorkflowStepResponse(BaseModel):
    """A workflow step's state, as returned by the API."""

    id: uuid.UUID
    organization_id: uuid.UUID
    workflow_run_id: uuid.UUID
    sequence_number: int
    step_type: str
    status: StepStatus
    agent_name: str | None
    tool_name: str | None
    attempt_count: int
    failure_code: str | None
    failure_message_safe: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowStepListResponse(BaseModel):
    """`GET .../workflows/{workflow_id}/steps` response envelope."""

    steps: list[WorkflowStepResponse]


class WorkflowEventResponse(BaseModel):
    """A workflow audit event, as returned by the API. `safe_metadata` is
    whatever bounded, pre-sanitized structured data the writing service
    call chose to attach — never a raw prompt, exception, or credential
    (enforced at write time, not by this schema — see
    docs/WORKFLOWS.md "Safe Failure Metadata")."""

    id: uuid.UUID
    organization_id: uuid.UUID
    workflow_run_id: uuid.UUID
    workflow_step_id: uuid.UUID | None
    event_type: WorkflowEventType
    actor_type: ActorType
    actor_identifier: str
    safe_metadata: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowEventListResponse(BaseModel):
    """`GET .../workflows/{workflow_id}/events` response envelope."""

    events: list[WorkflowEventResponse]
