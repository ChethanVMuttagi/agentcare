"""Agent execution API schemas.

`AgentExecuteRequest.request_text` is the ONLY untrusted free-form input
in this API — bounded in length, required non-empty, and never persisted
by default (see `app.services.workflow` — no field anywhere in this
story's persistence layer holds raw request text). `AgentExecuteResponse`
never exposes a prompt, a provider response, or any reasoning/
chain-of-thought content — only the safe, bounded decision outcome.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.ai.decisions import DecisionKind
from app.models.workflow import WorkflowRequestType, WorkflowStatus

_REQUEST_TEXT_MAX_LENGTH = 2000


class AgentExecuteRequest(BaseModel):
    """`POST .../agent/execute` request body.

    `patient_id`, when supplied by an `ADMIN`/`STAFF` caller, must name
    a patient in the same organization. A `PATIENT`-role caller's value
    here is ignored (never trusted) — the route always substitutes their
    own linked patient id, the same rule
    `app.schemas.workflow.WorkflowRunCreate` already applies. No
    conversation-history field exists — this endpoint accepts exactly
    one request, decides exactly one thing (per turn — see
    `workflow_run_id` below).

    `workflow_run_id` (STORY-015), when supplied, RESUMES that existing
    workflow run instead of starting a new one — only valid when the run
    is currently paused (`WAITING`) on a Coordinator clarification, not
    an approval decision (use the approvals API for that — see
    `app.ai.orchestration.AgentOrchestrationService._resume_run_and_step`).
    `request_type` is ignored when resuming (the run already has one).
    """

    request_type: WorkflowRequestType
    request_text: str = Field(min_length=1, max_length=_REQUEST_TEXT_MAX_LENGTH)
    patient_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None


class AgentExecuteResponse(BaseModel):
    """The safe, structured outcome of one administrative request.

    `message` is always a safe, bounded string — the model's
    clarification/refusal/safe-response text, or a tool's own safe
    result message. `tool_result_data`, when present, is the same
    bounded, pre-sanitized `dict` a tool handler returned (see
    `app.ai.tools.base.ToolResult`) — never a raw ORM object, SQL error,
    or arbitrary payload. `handled_by_agent` (STORY-011) is the stable
    agent name that produced the final outcome — `"coordinator"`,
    `"scheduling"`, `"document"`, or `"routing"` — never a provider or
    model name, and never anything resembling the Coordinator's
    reasoning for its choice.
    """

    workflow_id: uuid.UUID
    workflow_status: WorkflowStatus
    decision_kind: DecisionKind
    handled_by_agent: str
    message: str
    tool_name: str | None
    tool_result_code: str | None
    tool_result_data: dict[str, Any] | None
    approval_id: uuid.UUID | None = None
    """STORY-014: set only when `decision_kind` is `requires_approval` —
    the id of the `ApprovalRequest` now pausing this workflow. See
    `app.api.v1.endpoints.approvals`."""

    model_config = {"from_attributes": True}
