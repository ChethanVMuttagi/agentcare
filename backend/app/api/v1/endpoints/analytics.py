"""Analytics endpoint: a single organization-wide observability summary
(Milestone B: the Analytics Dashboard).

ADMIN/STAFF only — the same access this codebase already gives
organization-wide (non-self-scoped) data everywhere else (e.g.
`app.api.v1.endpoints.workflows.list_workflows` for ADMIN/STAFF vs.
PATIENT). There is no PATIENT- or SUPERVISOR-scoped variant: a
PATIENT-role caller has no legitimate reason to see organization-wide
counts, and SUPERVISOR's scope elsewhere in this codebase is limited to
the approval queue specifically (see `app.api.v1.endpoints.approvals`),
not general analytics.

This module performs no writes and adds no new persisted data — every
figure is a `GROUP BY`/`COUNT(*)` aggregate over existing tables,
computed fresh on each request (no caching, no materialized view: see
docs — organization-scoped tables in this system are not large enough
yet to need one).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.models.workflow import WorkflowEventType
from app.repositories import appointment as appointment_repository
from app.repositories import approval as approval_repository
from app.repositories import patient as patient_repository
from app.repositories import patient_document as patient_document_repository
from app.repositories import workflow_event as workflow_event_repository
from app.repositories import workflow_run as workflow_run_repository
from app.schemas.analytics import AnalyticsSummaryResponse

router = APIRouter(prefix="/organizations/{organization_id}/analytics")

_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)


@router.get("/summary", response_model=AnalyticsSummaryResponse)
async def get_analytics_summary(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AnalyticsSummaryResponse:
    """One organization-wide snapshot: workflow/appointment/approval/
    patient/document counts plus agent-activity (tool invocation, agent
    handoff) totals — everything the Analytics Dashboard renders, in a
    single request rather than six separate list-and-count-client-side
    round trips."""
    workflows_by_status = await workflow_run_repository.count_by_status(
        session, organization_id=organization_id
    )
    workflows_by_request_type = await workflow_run_repository.count_by_request_type(
        session, organization_id=organization_id
    )
    appointments_by_status = await appointment_repository.count_by_status(
        session, organization_id=organization_id
    )
    approvals_by_status = await approval_repository.count_by_status(
        session, organization_id=organization_id
    )
    patients_total = await patient_repository.count(session, organization_id=organization_id)
    documents_by_status = await patient_document_repository.count_by_status(
        session, organization_id=organization_id
    )
    events_by_type = await workflow_event_repository.count_by_event_type(
        session, organization_id=organization_id
    )
    agent_handoffs_by_target = await workflow_event_repository.count_agent_handoffs_by_target(
        session, organization_id=organization_id
    )

    return AnalyticsSummaryResponse(
        workflows_total=sum(workflows_by_status.values()),
        workflows_by_status={status.value: count for status, count in workflows_by_status.items()},
        workflows_by_request_type={
            request_type.value: count for request_type, count in workflows_by_request_type.items()
        },
        appointments_total=sum(appointments_by_status.values()),
        appointments_by_status={
            status.value: count for status, count in appointments_by_status.items()
        },
        approvals_total=sum(approvals_by_status.values()),
        approvals_by_status={status.value: count for status, count in approvals_by_status.items()},
        patients_total=patients_total,
        documents_total=sum(documents_by_status.values()),
        documents_by_status={status.value: count for status, count in documents_by_status.items()},
        tool_invocations_total=events_by_type.get(WorkflowEventType.TOOL_INVOKED, 0),
        agent_handoffs_total=events_by_type.get(WorkflowEventType.AGENT_HANDOFF, 0),
        agent_handoffs_by_target=agent_handoffs_by_target,
        generated_at=datetime.now(UTC),
    )
