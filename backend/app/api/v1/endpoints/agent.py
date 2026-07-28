"""Agent execution endpoint: the smallest useful AI-assisted
administrative-request surface.

RBAC (see docs/AI_SAFETY.md "Authorization Boundary" and
docs/RBAC.md): `ADMIN`/`STAFF` may execute a request for any patient (or
none) in their organization. `PATIENT` may execute a request only for
themselves — their identity is always server-derived, exactly like
`app.api.v1.endpoints.workflows`, never trusted from the request body or
anything a model itself might say.

This route creates and owns exactly one `WorkflowRun`: one Coordinator
decision, at most one specialist handoff, at most one specialist model
decision, at most one tool execution — fully persisted before the
response is returned. See
`app.ai.orchestration.AgentOrchestrationService` for the full call
chain and docs/AGENTS.md for the multi-agent architecture (STORY-011).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.definitions import get_agent_registry
from app.ai.agents.registry import AgentRegistry
from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.base import LLMProvider
from app.ai.providers.factory import get_llm_provider
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.registry_builder import get_full_tool_registry
from app.auth.dependencies import get_current_user, require_roles
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.schemas.agent import AgentExecuteRequest, AgentExecuteResponse
from app.services.patient import PatientService

router = APIRouter(prefix="/organizations/{organization_id}/agent")

_require_any_access = require_roles(Role.ADMIN, Role.STAFF, Role.PATIENT)


async def _resolve_request_patient_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    current_user: User,
    requested_patient_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Same rule as `app.api.v1.endpoints.workflows._resolve_creation_patient_id`:
    a `PATIENT`-role caller's own linked patient id ALWAYS wins —
    `requested_patient_id` (whatever the client, or the model on the
    client's behalf, might have said) is ignored, not merely validated.
    `ADMIN`/`STAFF`: `requested_patient_id` as given."""
    if membership.role is Role.PATIENT:
        patient_service = PatientService(session)
        own_patient = await patient_service.get_own_patient_record(
            organization_id=organization_id, user_id=current_user.id
        )
        return own_patient.id
    return requested_patient_id


@router.post("/execute", response_model=AgentExecuteResponse, status_code=201)
async def execute_administrative_request(
    organization_id: uuid.UUID,
    payload: AgentExecuteRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    tool_registry: Annotated[ToolRegistry, Depends(get_full_tool_registry)],
    agent_registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
) -> AgentExecuteResponse:
    """Interpret one administrative request via the Coordinator agent
    and, at most, hand off to one specialist that executes at most one
    tool. ADMIN/STAFF may act for any patient in the organization (or
    none); PATIENT may act only for themselves. Creates and persists a
    new `WorkflowRun`, UNLESS `payload.workflow_run_id` is supplied, in
    which case an existing, clarification-paused run is resumed instead
    (STORY-015) — see
    `app.ai.orchestration.AgentOrchestrationService.execute_administrative_request`.
    """
    resolved_patient_id = await _resolve_request_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        requested_patient_id=payload.patient_id,
    )

    orchestration_service = AgentOrchestrationService(
        session, provider, tool_registry, agent_registry
    )
    result = await orchestration_service.execute_administrative_request(
        organization_id=organization_id,
        initiated_by_user_id=current_user.id,
        role=membership.role,
        resolved_patient_id=resolved_patient_id,
        request_type=payload.request_type,
        request_text=payload.request_text,
        workflow_run_id=payload.workflow_run_id,
    )

    return AgentExecuteResponse(
        workflow_id=result.workflow_run_id,
        workflow_status=result.workflow_status,
        decision_kind=result.decision_kind,
        handled_by_agent=result.handled_by_agent,
        message=result.safe_message,
        tool_name=result.tool_name,
        tool_result_code=result.tool_result_code,
        tool_result_data=result.tool_result_data,
        approval_id=result.approval_id,
    )
