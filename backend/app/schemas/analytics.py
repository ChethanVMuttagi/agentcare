"""Analytics API schemas (Milestone B: the Analytics Dashboard's data
source).

Every count here is a plain aggregate (`GROUP BY status`/`event_type`,
or a bare `COUNT(*)`) over existing tables — there is no new persisted
data anywhere in this module, and nothing here exposes patient
identity, request text, or any other PHI: only counts, grouped by
already-public enum values or safe agent names (see
`app.models.workflow.WorkflowEvent.safe_metadata`'s own privacy
constraints, which this endpoint inherits by construction).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AnalyticsSummaryResponse(BaseModel):
    """`GET .../analytics/summary` response — a single organization-wide
    snapshot combining workflow, appointment, approval, patient,
    document, and agent-activity counts. Keys in each `by_*` mapping use
    the enum's wire value (e.g. `"completed"`, `"agent_handoff"`);
    statuses/types with zero occurrences are simply absent."""

    workflows_total: int
    workflows_by_status: dict[str, int]
    workflows_by_request_type: dict[str, int]

    appointments_total: int
    appointments_by_status: dict[str, int]

    approvals_total: int
    approvals_by_status: dict[str, int]

    patients_total: int

    documents_total: int
    documents_by_status: dict[str, int]

    tool_invocations_total: int
    agent_handoffs_total: int
    agent_handoffs_by_target: dict[str, int]

    generated_at: datetime
