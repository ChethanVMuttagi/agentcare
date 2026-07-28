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

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.db.session import get_db_session, get_sessionmaker
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.models.workflow import ActorType, WorkflowEvent, WorkflowStep
from app.schemas.patient import PatientCreate
from app.schemas.workflow import (
    WorkflowEventListResponse,
    WorkflowEventResponse,
    WorkflowRunCreate,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowStepListResponse,
    WorkflowStepResponse,
    WorkflowTimelineEntry,
    WorkflowTimelineResponse,
)
from app.services.patient import PatientService
from app.services.patient_registration import PatientRegistrationService
from app.services.workflow import TERMINAL_RUN_STATUSES, WorkflowNotFoundError, WorkflowService

router = APIRouter(prefix="/organizations/{organization_id}/workflows")

_require_any_access = require_roles(Role.ADMIN, Role.STAFF, Role.PATIENT)
_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)

# Milestone B: Server-Sent Events polling cadence for `stream_workflow_events`.
# This codebase has no pub/sub or in-process event bus anywhere (a new
# `WorkflowEvent` is written from exactly one place,
# `app.services.workflow.WorkflowService` — see that module's docstring),
# so the stream polls for new rows rather than being pushed to; 1.5s is
# frequent enough to feel "live" in a demo without meaningfully loading
# the database. A comment-only heartbeat is sent every ~15s of silence so
# intermediary proxies/load balancers don't time out an idle connection.
_STREAM_POLL_INTERVAL_SECONDS = 1.5
_STREAM_HEARTBEAT_EVERY_TICKS = 10


def _build_timeline(
    steps: list[WorkflowStep], events: list[WorkflowEvent]
) -> list[WorkflowTimelineEntry]:
    """STORY-015: denormalize each event with a summary of the step it
    belongs to (if any) — pure response-shaping, no business logic (that
    lives in `WorkflowService`); mirrors this module's existing
    `_own_patient_id`-style local helper pattern. `events` is already
    ordered by `WorkflowService.list_events` (oldest first, by
    `WorkflowEvent.sequence`) — this function preserves that order."""
    steps_by_id = {step.id: step for step in steps}
    entries: list[WorkflowTimelineEntry] = []
    for event in events:
        step = steps_by_id.get(event.workflow_step_id) if event.workflow_step_id else None
        entries.append(
            WorkflowTimelineEntry(
                sequence=event.sequence,
                event_type=event.event_type,
                actor_type=event.actor_type,
                actor_identifier=event.actor_identifier,
                safe_metadata=event.safe_metadata,
                created_at=event.created_at,
                workflow_step_id=event.workflow_step_id,
                step_sequence_number=step.sequence_number if step is not None else None,
                step_type=step.step_type if step is not None else None,
                step_agent_name=step.agent_name if step is not None else None,
            )
        )
    return entries


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


# --- STORY-015: Patient Registration workflow trigger + timeline inspection ---


@router.post(
    "/patient-registrations", response_model=WorkflowRunResponse, status_code=201
)
async def start_patient_registration(
    organization_id: uuid.UUID,
    payload: PatientCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowRunResponse:
    """Start the Patient Registration workflow template. ADMIN/STAFF
    ONLY — patient registration is an administrative intake action, never
    a `PATIENT`-role self-service one (there is no existing patient/self
    scope to check against yet). ALWAYS returns 201 with the resulting
    `WorkflowRun` — never raises for a business-rule outcome (a hard
    patient-number conflict fails the run; a suspected duplicate pauses
    it for approval); inspect `status`/`failure_code` to see which
    happened. See `app.services.patient_registration.PatientRegistrationService`.
    """
    service = PatientRegistrationService(session)
    run = await service.start_registration(
        organization_id=organization_id,
        initiated_by_user_id=current_user.id,
        patient_number=payload.patient_number,
        first_name=payload.first_name,
        last_name=payload.last_name,
        date_of_birth=payload.date_of_birth,
        user_id=payload.user_id,
    )
    return WorkflowRunResponse.model_validate(run)


@router.get("/{workflow_id}/timeline", response_model=WorkflowTimelineResponse)
async def get_workflow_timeline(
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowTimelineResponse:
    """A single, chronologically ordered view of a workflow run's steps
    and events merged together — the SAME data `/steps` and `/events`
    already expose, combined so a caller doesn't need a second request
    and a client-side join. Same ownership check as `get_workflow`."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = WorkflowService(session)
    run = await service.get_workflow(
        organization_id=organization_id, workflow_run_id=workflow_id, patient_id=restriction
    )
    steps = await service.list_steps(organization_id=organization_id, workflow_run_id=workflow_id)
    events = await service.list_events(
        organization_id=organization_id, workflow_run_id=workflow_id
    )
    return WorkflowTimelineResponse(
        workflow_id=run.id,
        status=run.status,
        entries=_build_timeline(list(steps), list(events)),
    )


# --- Milestone B: live Server-Sent Events stream of new timeline entries ---


async def _workflow_event_stream(
    *,
    organization_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    patient_restriction: uuid.UUID | None,
    start_after_sequence: int,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Polling-based SSE generator backing `stream_workflow_events`.

    Deliberately opens a FRESH, short-lived session each poll tick via
    `get_sessionmaker()` rather than holding the route's request-scoped
    session open for the connection's entire (potentially long) lifetime
    — the same reasoning `app.workers.reminder_worker.ReminderWorker`
    applies to its own long-running loop, the only other precedent for a
    long-lived loop in this codebase.

    Each SSE message carries an `id:` line set to the event's `sequence`
    so a reconnecting browser's `Last-Event-ID` header lets
    `stream_workflow_events` resume exactly where it left off, without
    re-sending (or losing) any event. Stops on client disconnect, on the
    workflow reaching a terminal status (after one last flush of any
    trailing events), or if the workflow disappears (cannot happen under
    normal operation — `WorkflowEvent`/`WorkflowRun` are never deleted —
    guarded anyway since this loop must never crash silently).
    """
    sessionmaker = get_sessionmaker()
    last_sequence = start_after_sequence
    ticks_since_heartbeat = 0

    while True:
        if await request.is_disconnected():
            return

        async with sessionmaker() as session:
            service = WorkflowService(session)
            try:
                run = await service.get_workflow(
                    organization_id=organization_id,
                    workflow_run_id=workflow_run_id,
                    patient_id=patient_restriction,
                )
            except WorkflowNotFoundError:
                # Deliberately NOT the bare event name "error": browsers
                # dispatch a native, data-less `error` event on the
                # `EventSource` object itself for connection-level
                # failures — reusing that name for an application-level
                # message would collide with (and be indistinguishable
                # from) that native event in client code.
                yield 'event: workflow_error\ndata: {"code": "workflow_not_found"}\n\n'
                return

            new_events = await service.list_events_since(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                after_sequence=last_sequence,
            )
            if new_events:
                steps = await service.list_steps(
                    organization_id=organization_id, workflow_run_id=workflow_run_id
                )
                for entry in _build_timeline(steps, new_events):
                    last_sequence = entry.sequence
                    payload = json.dumps(entry.model_dump(mode="json"))
                    yield f"id: {entry.sequence}\nevent: workflow_event\ndata: {payload}\n\n"
                ticks_since_heartbeat = 0

            is_terminal = run.status in TERMINAL_RUN_STATUSES

        if is_terminal:
            yield f'event: done\ndata: {{"status": "{run.status.value}"}}\n\n'
            return

        ticks_since_heartbeat += 1
        if ticks_since_heartbeat >= _STREAM_HEARTBEAT_EVERY_TICKS:
            yield ": heartbeat\n\n"
            ticks_since_heartbeat = 0

        await asyncio.sleep(_STREAM_POLL_INTERVAL_SECONDS)


@router.get("/{workflow_id}/events/stream")
async def stream_workflow_events(
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Server-Sent Events stream of new workflow timeline entries
    (Milestone B: the Live Agent Execution panel and AI Trace Viewer's
    real-time source). Same ownership check as `get_workflow_timeline`,
    performed once up front using the request-scoped session; the stream
    body itself then polls with its own short-lived sessions (see
    `_workflow_event_stream`) since it outlives this dependency's scope.

    Resumable via the standard SSE `Last-Event-ID` mechanism (sent
    automatically by the browser's `EventSource` on reconnect) or the
    `after_sequence` query parameter for an explicit initial cursor;
    `Last-Event-ID` takes precedence when both are present.
    """
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = WorkflowService(session)
    await service.get_workflow(
        organization_id=organization_id, workflow_run_id=workflow_id, patient_id=restriction
    )

    last_event_id = request.headers.get("last-event-id")
    after_sequence_param = request.query_params.get("after_sequence")
    start_after_sequence = 0
    for candidate in (last_event_id, after_sequence_param):
        if candidate is not None:
            try:
                start_after_sequence = int(candidate)
            except ValueError:
                start_after_sequence = 0
            break

    return StreamingResponse(
        _workflow_event_stream(
            organization_id=organization_id,
            workflow_run_id=workflow_id,
            patient_restriction=restriction,
            start_after_sequence=start_after_sequence,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
