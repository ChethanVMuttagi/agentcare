"""Agent execution endpoint: the smallest useful AI-assisted
administrative-request surface.

RBAC (see docs/AI_SAFETY.md "Authorization Boundary" and
docs/RBAC.md): `ADMIN`/`STAFF` may execute a request for any patient (or
none) in their organization. `PATIENT` may execute a request only for
themselves — their identity is always server-derived, exactly like
`app.api.v1.endpoints.workflows`, never trusted from the request body or
anything the model itself might say.

This route creates and owns exactly one `WorkflowRun`: one model
decision, at most one tool execution, fully persisted before the
response is returned. See `app.ai.orchestration.AgentOrchestrationService`
for the full call chain.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.base import LLMProvider
from app.ai.providers.factory import get_llm_provider
from app.ai.tools.appointment_tools import get_tool_registry
from app.ai.tools.registry import ToolRegistry
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
    tool_registry: Annotated[ToolRegistry, Depends(get_tool_registry)],
) -> AgentExecuteResponse:
    """Interpret one administrative request and, at most, execute one
    tool. ADMIN/STAFF may act for any patient in the organization (or
    none); PATIENT may act only for themselves. Always creates and
    persists a `WorkflowRun` — see the module docstring."""
    resolved_patient_id = await _resolve_request_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        requested_patient_id=payload.patient_id,
    )

    orchestration_service = AgentOrchestrationService(session, provider, tool_registry)
    result = await orchestration_service.execute_administrative_request(
        organization_id=organization_id,
        initiated_by_user_id=current_user.id,
        role=membership.role,
        resolved_patient_id=resolved_patient_id,
        request_type=payload.request_type,
        request_text=payload.request_text,
    )

    return AgentExecuteResponse(
        workflow_id=result.workflow_run_id,
        workflow_status=result.workflow_status,
        decision_kind=result.decision_kind,
        message=result.safe_message,
        tool_name=result.tool_name,
        tool_result_code=result.tool_result_code,
        tool_result_data=result.tool_result_data,
    )
