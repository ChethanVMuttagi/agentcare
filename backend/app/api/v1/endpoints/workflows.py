"""Workflow endpoints: administrative workflow creation and inspection.

RBAC (see docs/WORKFLOWS.md "RBAC" and docs/RBAC.md): `ADMIN`/`STAFF` may
create a workflow for any patient (or none) in their organization, list
and inspect any workflow (and its steps/events) in the organization, and
cancel a workflow. `PATIENT` may create a workflow only for themselves,
and may list/inspect ONLY their own workflows (and their steps/events) —
never another patient's, and never by supplying another patient's id.
`PATIENT` may not cancel a workflow (a deliberately conservative policy,
the same one `app.api.v1.endpoints.documents` applies to deletion).

## Patient Identity: Never Trusted From The Client

For every route a `PATIENT`-role caller can reach, this module resolves
`patient_id` from the caller's OWN linked `Patient` record
(`PatientService.get_own_patient_record`, keyed off the authenticated
`User.id`), the same pattern `app.api.v1.endpoints.appointments` and
`app.api.v1.endpoints.documents` use. `WorkflowRunCreate.patient_id`
(client-supplied) is read but never trusted for a `PATIENT` caller — see
`_resolve_creation_patient_id`.

## No Internal Mutation Surface

This module deliberately exposes only creation, read, and cancellation.
It does NOT expose `start`/`mark_waiting`/`resume`/`complete`/`fail`, or
any step-level transition, as HTTP endpoints — those are internal
lifecycle operations for future agent/orchestration code to call
directly through `WorkflowService`, never something an API client
triggers. See docs/WORKFLOWS.md "API Surface".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.models.workflow import ActorType
from app.schemas.workflow import (
    WorkflowEventListResponse,
    WorkflowEventResponse,
    WorkflowRunCreate,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowStepListResponse,
    WorkflowStepResponse,
)
from app.services.patient import PatientService
from app.services.workflow import WorkflowService

router = APIRouter(prefix="/organizations/{organization_id}/workflows")

_require_any_access = require_roles(Role.ADMIN, Role.STAFF, Role.PATIENT)
_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)


async def _own_patient_id(
    session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    patient_service = PatientService(session)
    own_patient = await patient_service.get_own_patient_record(
        organization_id=organization_id, user_id=user_id
    )
    return own_patient.id


async def _resolve_creation_patient_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    current_user: User,
    requested_patient_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """`PATIENT` role: ALWAYS the caller's own linked patient id —
    `requested_patient_id` is deliberately ignored, not merely
    validated. `ADMIN`/`STAFF`: `requested_patient_id` as given
    (`None` is allowed — not every workflow is patient-tied)."""
    if membership.role is Role.PATIENT:
        return await _own_patient_id(
            session, organization_id=organization_id, user_id=current_user.id
        )
    return requested_patient_id


async def _restriction_patient_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    current_user: User,
) -> uuid.UUID | None:
    """`None` for ADMIN/STAFF (no ownership restriction); the caller's own
    patient id for PATIENT — passed to `WorkflowService` so a lookup
    targeting another patient's workflow 404s, the same non-disclosing
    shape as any other not-found case."""
    if membership.role is Role.PATIENT:
        return await _own_patient_id(
            session, organization_id=organization_id, user_id=current_user.id
        )
    return None


@router.post("", response_model=WorkflowRunResponse, status_code=201)
async def create_workflow(
    organization_id: uuid.UUID,
    payload: WorkflowRunCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowRunResponse:
    """Create a new (`PENDING`) workflow run. ADMIN/STAFF may create for
    any patient in the organization or none at all. PATIENT may create
    only for themselves (`patient_id`, if supplied, is ignored — see the
    module docstring)."""
    patient_id = await _resolve_creation_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        requested_patient_id=payload.patient_id,
    )
    service = WorkflowService(session)
    run = await service.create_workflow(
        organization_id=organization_id,
        initiated_by_user_id=current_user.id,
        request_type=payload.request_type,
        patient_id=patient_id,
        actor_type=ActorType.USER,
        actor_identifier=str(current_user.id),
        idempotency_key=payload.idempotency_key,
    )
    return WorkflowRunResponse.model_validate(run)


@router.get("", response_model=WorkflowRunListResponse)
async def list_workflows(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = 50,
    offset: int = 0,
) -> WorkflowRunListResponse:
    """List workflow runs. ADMIN/STAFF: organization-wide. PATIENT: their
    own workflows only."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = WorkflowService(session)
    runs = await service.list_workflows(
        organization_id=organization_id, patient_id=restriction, limit=limit, offset=offset
    )
    return WorkflowRunListResponse(workflows=[WorkflowRunResponse.model_validate(r) for r in runs])


@router.get("/{workflow_id}", response_model=WorkflowRunResponse)
async def get_workflow(
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowRunResponse:
    """Retrieve a workflow run by id. ADMIN/STAFF: any workflow in the
    organization. PATIENT: only their own — any other workflow id 404s,
    identically to a truly nonexistent one."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = WorkflowService(session)
    run = await service.get_workflow(
        organization_id=organization_id, workflow_run_id=workflow_id, patient_id=restriction
    )
    return WorkflowRunResponse.model_validate(run)


@router.get("/{workflow_id}/steps", response_model=WorkflowStepListResponse)
async def list_workflow_steps(
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowStepListResponse:
    """List a workflow run's steps, in execution order. Same ownership
    check as `get_workflow` — a `PATIENT` caller cannot see another
    patient's steps by guessing/enumerating a workflow id."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = WorkflowService(session)
    await service.get_workflow(
        organization_id=organization_id, workflow_run_id=workflow_id, patient_id=restriction
    )
    steps = await service.list_steps(organization_id=organization_id, workflow_run_id=workflow_id)
    return WorkflowStepListResponse(steps=[WorkflowStepResponse.model_validate(s) for s in steps])


@router.get("/{workflow_id}/events", response_model=WorkflowEventListResponse)
async def list_workflow_events(
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowEventListResponse:
    """List a workflow run's audit events, oldest first. Same ownership
    check as `get_workflow`."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = WorkflowService(session)
    await service.get_workflow(
        organization_id=organization_id, workflow_run_id=workflow_id, patient_id=restriction
    )
    events = await service.list_events(
        organization_id=organization_id, workflow_run_id=workflow_id
    )
    return WorkflowEventListResponse(
        events=[WorkflowEventResponse.model_validate(e) for e in events]
    )


@router.post("/{workflow_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_workflow(
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowRunResponse:
    """Cancel a `PENDING`/`RUNNING`/`WAITING` workflow run. ADMIN/STAFF
    ONLY — `PATIENT` cannot reach this route at all (`require_roles`
    rejects it with 403 before any workflow is even looked up), the same
    conservative policy `app.api.v1.endpoints.documents.delete_document`
    applies to document deletion."""
    service = WorkflowService(session)
    run = await service.cancel_workflow(
        organization_id=organization_id,
        workflow_run_id=workflow_id,
        actor_type=ActorType.USER,
        actor_identifier=str(current_user.id),
    )
    return WorkflowRunResponse.model_validate(run)
