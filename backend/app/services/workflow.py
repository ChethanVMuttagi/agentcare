"""WorkflowService: durable workflow lifecycle, transitions, and audit events.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005/006/007/008. This service owns the workflow and
step STATE MACHINES (see `_ALLOWED_RUN_TRANSITIONS`/
`_ALLOWED_STEP_TRANSITIONS` below) — `app.repositories.workflow_run`,
`app.repositories.workflow_step`, and `app.repositories.workflow_event`
never decide whether a transition is allowed.

## Concurrency Strategy

Every lifecycle transition (run or step) locks its row with
`SELECT ... FOR UPDATE` (see `app.repositories.workflow_run.get_by_id_for_update`
and `app.repositories.workflow_step.get_by_id_for_update`) before
checking whether the requested transition is currently allowed. A second
concurrent transition attempt on the SAME row blocks on that lock until
the first commits, then re-reads the now-current (possibly
already-transitioned) status — so a losing caller deterministically sees
its own transition rejected as `WorkflowConflictError` (409) rather than
silently double-applying it or overwriting the winner. See
docs/WORKFLOWS.md "Concurrency Strategy" and
`tests/db/test_workflow_concurrency.py`.

## Transition + Event Atomicity

Every transition method updates the row's status AND appends the
corresponding `WorkflowEvent` within the SAME database transaction,
committed together. A transition is never left partially recorded: the
status change and its audit event either both persist or neither does.

## Audit Boundary

`WorkflowEvent` is this workflow's own lifecycle audit trail — it is NOT
a general-purpose security/compliance audit log, and persisting these
events does not, by itself, constitute HIPAA compliance. See
docs/WORKFLOWS.md "Audit Service Boundary".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.membership import OrganizationMembership
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.repositories import patient as patient_repository
from app.repositories import workflow_event as workflow_event_repository
from app.repositories import workflow_run as workflow_run_repository
from app.repositories import workflow_step as workflow_step_repository

_TERMINAL_RUN_STATUSES = frozenset(
    {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
)

# Centralized run-level state machine (see the module docstring). Keys are
# the CURRENT status; values are the set of statuses a transition may move
# to from there. Any transition not listed here is rejected as a conflict.
_ALLOWED_RUN_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.PENDING: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.RUNNING: frozenset(
        {
            WorkflowStatus.WAITING,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.WAITING: frozenset(
        {WorkflowStatus.RUNNING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}

# Centralized step-level state machine. `WAITING` is intentionally
# reachable in the enum/database CHECK constraint (future tool-call retry
# support) but no service method moves a step into or out of it yet — see
# docs/WORKFLOWS.md "Step Lifecycle" for this deliberate scope limit.
_ALLOWED_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED}),
    StepStatus.RUNNING: frozenset(
        {StepStatus.WAITING, StepStatus.COMPLETED, StepStatus.FAILED}
    ),
    StepStatus.WAITING: frozenset({StepStatus.RUNNING, StepStatus.FAILED}),
    StepStatus.COMPLETED: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
}


class WorkflowNotFoundError(AppException):
    """404: no workflow run matches, within the caller's own organization."""

    status_code = 404
    error_code = "workflow_not_found"


class WorkflowStepNotFoundError(AppException):
    """404: no step matches, within the caller's own workflow run."""

    status_code = 404
    error_code = "workflow_step_not_found"


class WorkflowPatientNotFoundError(AppException):
    """404: no patient matches, within the caller's own organization."""

    status_code = 404
    error_code = "workflow_patient_not_found"


class WorkflowPatientInactiveError(AppException):
    """422: the patient exists but is not active."""

    status_code = 422
    error_code = "workflow_patient_inactive"


class WorkflowInitiatorNotActiveMemberError(AppException):
    """422: the initiating user has no ACTIVE membership in this organization.

    Defense-in-depth, not the primary enforcement point — see
    `app.services.document.UploaderNotActiveMemberError` for the same
    reasoning applied here.
    """

    status_code = 422
    error_code = "workflow_initiator_not_active_member"


class WorkflowConflictError(AppException):
    """409: the requested transition is not valid from the row's CURRENT
    status, observed under a row lock. Covers both a genuine invalid
    transition and a losing concurrent race — the two are
    indistinguishable (and do not need to be distinguished) once the
    lock is held: whatever the current status turned out to be, this
    transition is not allowed from it right now."""

    status_code = 409
    error_code = "workflow_conflict"


class WorkflowIdempotencyKeyConflictError(AppException):
    """409: `idempotency_key` is already in use within this organization."""

    status_code = 409
    error_code = "workflow_idempotency_key_conflict"


def _new_correlation_id() -> str:
    """A server-generated, OPAQUE correlation id — never derived from
    patient name, email, or date of birth, and never chosen by a client.
    `uuid4().hex` is exactly 32 characters, matching the column's bound.
    """
    return uuid.uuid4().hex


class WorkflowService:
    """Workflow/step lifecycle and audit-event orchestration, scoped to
    one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _ensure_initiator_is_active_member(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        result = await self._session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None or not membership.is_active:
            raise WorkflowInitiatorNotActiveMemberError(
                "The initiating user does not have an active membership in this organization."
            )

    async def create_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        initiated_by_user_id: uuid.UUID,
        request_type: WorkflowRequestType,
        patient_id: uuid.UUID | None,
        actor_type: ActorType,
        actor_identifier: str,
        idempotency_key: str | None = None,
    ) -> WorkflowRun:
        """Create a new `WorkflowRun` (`PENDING`) and its `WORKFLOW_CREATED`
        event, committed atomically. The correlation id is always
        server-generated (see `_new_correlation_id`); a client never
        supplies one.
        """
        await self._ensure_initiator_is_active_member(
            organization_id=organization_id, user_id=initiated_by_user_id
        )
        if patient_id is not None:
            patient = await patient_repository.get_by_id(
                self._session, organization_id=organization_id, patient_id=patient_id
            )
            if patient is None:
                raise WorkflowPatientNotFoundError(
                    "No patient found with this id in this organization."
                )
            if not patient.is_active:
                raise WorkflowPatientInactiveError("This patient is not active.")

        run = WorkflowRun(
            organization_id=organization_id,
            patient_id=patient_id,
            initiated_by_user_id=initiated_by_user_id,
            request_type=request_type,
            status=WorkflowStatus.PENDING,
            correlation_id=_new_correlation_id(),
            idempotency_key=idempotency_key,
        )
        try:
            await workflow_run_repository.create(self._session, run)
        except IntegrityError as exc:
            await self._session.rollback()
            raise WorkflowIdempotencyKeyConflictError(
                "A workflow with this idempotency key already exists in this organization."
            ) from exc

        await workflow_event_repository.create(
            self._session,
            WorkflowEvent(
                organization_id=organization_id,
                workflow_run_id=run.id,
                event_type=WorkflowEventType.WORKFLOW_CREATED,
                actor_type=actor_type,
                actor_identifier=actor_identifier,
            ),
        )
        await self._session.commit()
        return run

    async def get_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
    ) -> WorkflowRun:
        """Tenant-scoped retrieval by id. `patient_id`, when given,
        additionally restricts the result to that patient's own run —
        used for `PATIENT`-role self-service callers. Raises the same
        `WorkflowNotFoundError` whether the run doesn't exist, belongs to
        another organization, or belongs to another patient."""
        run = await workflow_run_repository.get_by_id(
            self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
        )
        if run is None:
            raise WorkflowNotFoundError("No workflow found with this id in this organization.")
        if patient_id is not None and run.patient_id != patient_id:
            raise WorkflowNotFoundError("No workflow found with this id in this organization.")
        return run

    async def list_workflows(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        """Tenant-scoped listing. `patient_id`, when given, restricts to
        that patient's own runs (used for `PATIENT`-role callers)."""
        return list(
            await workflow_run_repository.list_by_organization(
                self._session,
                organization_id=organization_id,
                patient_id=patient_id,
                limit=limit,
                offset=offset,
            )
        )

    async def list_steps(
        self, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
    ) -> list[WorkflowStep]:
        """Steps for one run, in execution order. Caller must have
        already authorized access to `workflow_run_id` (e.g. via
        `get_workflow`)."""
        return list(
            await workflow_step_repository.list_by_run(
                self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
            )
        )

    async def list_events(
        self, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
    ) -> list[WorkflowEvent]:
        """Events for one run, oldest first. Caller must have already
        authorized access to `workflow_run_id` (e.g. via
        `get_workflow`)."""
        return list(
            await workflow_event_repository.list_by_run(
                self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
            )
        )

    async def _transition_run(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        to_status: WorkflowStatus,
        event_type: WorkflowEventType,
        actor_type: ActorType,
        actor_identifier: str,
        safe_metadata: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_message_safe: str | None = None,
    ) -> WorkflowRun:
        """Lock the run row, validate the transition against
        `_ALLOWED_RUN_TRANSITIONS`, apply it, append the corresponding
        event, and commit both together. See the module docstring for
        the concurrency and atomicity guarantees this provides."""
        run = await workflow_run_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
        )
        if run is None:
            raise WorkflowNotFoundError("No workflow found with this id in this organization.")

        allowed = _ALLOWED_RUN_TRANSITIONS.get(run.status, frozenset())
        if to_status not in allowed:
            raise WorkflowConflictError(
                f"Cannot transition workflow from '{run.status.value}' to '{to_status.value}'."
            )

        now = datetime.now(UTC)
        run.status = to_status
        if to_status is WorkflowStatus.RUNNING and run.started_at is None:
            run.started_at = now
        if to_status in _TERMINAL_RUN_STATUSES:
            run.completed_at = now
        if failure_code is not None:
            run.failure_code = failure_code
            run.failure_message_safe = failure_message_safe

        await workflow_event_repository.create(
            self._session,
            WorkflowEvent(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_identifier=actor_identifier,
                safe_metadata=safe_metadata,
            ),
        )
        await self._session.flush()
        await self._session.commit()
        return run

    async def start_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
    ) -> WorkflowRun:
        """`PENDING` -> `RUNNING`, with a `WORKFLOW_STARTED` event."""
        return await self._transition_run(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            to_status=WorkflowStatus.RUNNING,
            event_type=WorkflowEventType.WORKFLOW_STARTED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )

    async def mark_waiting(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
        safe_metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        """`RUNNING` -> `WAITING`, with a `WORKFLOW_WAITING` event."""
        return await self._transition_run(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            to_status=WorkflowStatus.WAITING,
            event_type=WorkflowEventType.WORKFLOW_WAITING,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            safe_metadata=safe_metadata,
        )

    async def resume_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
    ) -> WorkflowRun:
        """`WAITING` -> `RUNNING`, with a `WORKFLOW_RESUMED` event."""
        return await self._transition_run(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            to_status=WorkflowStatus.RUNNING,
            event_type=WorkflowEventType.WORKFLOW_RESUMED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )

    async def complete_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
    ) -> WorkflowRun:
        """`RUNNING` -> `COMPLETED`, with a `WORKFLOW_COMPLETED` event."""
        return await self._transition_run(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            to_status=WorkflowStatus.COMPLETED,
            event_type=WorkflowEventType.WORKFLOW_COMPLETED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )

    async def fail_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
        failure_code: str,
        failure_message_safe: str | None = None,
    ) -> WorkflowRun:
        """`RUNNING`/`WAITING` -> `FAILED`, with a `WORKFLOW_FAILED`
        event. `failure_code`/`failure_message_safe` must already be
        bounded, safe values (see docs/WORKFLOWS.md "Safe Failure
        Metadata") — never a raw exception, stack trace, or SQL
        statement."""
        return await self._transition_run(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            to_status=WorkflowStatus.FAILED,
            event_type=WorkflowEventType.WORKFLOW_FAILED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            failure_code=failure_code,
            failure_message_safe=failure_message_safe,
        )

    async def cancel_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
    ) -> WorkflowRun:
        """`PENDING`/`RUNNING`/`WAITING` -> `CANCELLED`, with a
        `WORKFLOW_CANCELLED` event."""
        return await self._transition_run(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            to_status=WorkflowStatus.CANCELLED,
            event_type=WorkflowEventType.WORKFLOW_CANCELLED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )

    async def create_step(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        sequence_number: int,
        step_type: str,
        agent_name: str | None = None,
        tool_name: str | None = None,
    ) -> WorkflowStep:
        """Create a new `PENDING` step for an existing, non-terminal run,
        and advance `current_step` to it. No event is recorded here — no
        `step_created` event type exists (see the module docstring's
        event-type list in docs/WORKFLOWS.md): the audit trail begins at
        `STEP_STARTED`. `sequence_number` is caller-assigned; this
        service does not implement a speculative scheduler/queue (see
        docs/WORKFLOWS.md)."""
        run = await workflow_run_repository.get_by_id(
            self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
        )
        if run is None:
            raise WorkflowNotFoundError("No workflow found with this id in this organization.")
        if run.status in _TERMINAL_RUN_STATUSES:
            raise WorkflowConflictError(
                f"Cannot add a step to a workflow in terminal status '{run.status.value}'."
            )

        step = WorkflowStep(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            sequence_number=sequence_number,
            step_type=step_type,
            status=StepStatus.PENDING,
            agent_name=agent_name,
            tool_name=tool_name,
        )
        await workflow_step_repository.create(self._session, step)
        await workflow_run_repository.set_current_step(
            self._session,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            current_step=sequence_number,
        )
        await self._session.commit()
        return step

    async def _transition_step(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        step_id: uuid.UUID,
        to_status: StepStatus,
        event_type: WorkflowEventType,
        actor_type: ActorType,
        actor_identifier: str,
        safe_metadata: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_message_safe: str | None = None,
        increment_attempt: bool = False,
    ) -> WorkflowStep:
        """Lock the step row, validate the transition against
        `_ALLOWED_STEP_TRANSITIONS`, apply it, append the corresponding
        event (linked to both the run and the step), and commit both
        together."""
        step = await workflow_step_repository.get_by_id_for_update(
            self._session,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
        if step is None:
            raise WorkflowStepNotFoundError("No step found with this id in this workflow.")

        allowed = _ALLOWED_STEP_TRANSITIONS.get(step.status, frozenset())
        if to_status not in allowed:
            raise WorkflowConflictError(
                f"Cannot transition step from '{step.status.value}' to '{to_status.value}'."
            )

        now = datetime.now(UTC)
        step.status = to_status
        if increment_attempt:
            step.attempt_count += 1
        if to_status is StepStatus.RUNNING and step.started_at is None:
            step.started_at = now
        if to_status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED):
            step.completed_at = now
        if failure_code is not None:
            step.failure_code = failure_code
            step.failure_message_safe = failure_message_safe

        await workflow_event_repository.create(
            self._session,
            WorkflowEvent(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                workflow_step_id=step_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_identifier=actor_identifier,
                safe_metadata=safe_metadata,
            ),
        )
        await self._session.flush()
        await self._session.commit()
        return step

    async def start_step(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
    ) -> WorkflowStep:
        """`PENDING` -> `RUNNING`, incrementing `attempt_count` and
        recording a `STEP_STARTED` event."""
        return await self._transition_step(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            to_status=StepStatus.RUNNING,
            event_type=WorkflowEventType.STEP_STARTED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            increment_attempt=True,
        )

    async def complete_step(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
        safe_metadata: dict[str, Any] | None = None,
    ) -> WorkflowStep:
        """`RUNNING` -> `COMPLETED`, with a `STEP_COMPLETED` event."""
        return await self._transition_step(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            to_status=StepStatus.COMPLETED,
            event_type=WorkflowEventType.STEP_COMPLETED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            safe_metadata=safe_metadata,
        )

    async def fail_step(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
        failure_code: str,
        failure_message_safe: str | None = None,
    ) -> WorkflowStep:
        """`RUNNING` -> `FAILED`, with a `STEP_FAILED` event.
        `failure_code`/`failure_message_safe` must already be bounded,
        safe values — see `fail_workflow`."""
        return await self._transition_step(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            to_status=StepStatus.FAILED,
            event_type=WorkflowEventType.STEP_FAILED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            failure_code=failure_code,
            failure_message_safe=failure_message_safe,
        )

    async def skip_step(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
    ) -> WorkflowStep:
        """`PENDING` -> `SKIPPED`, with a `STEP_SKIPPED` event."""
        return await self._transition_step(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
            to_status=StepStatus.SKIPPED,
            event_type=WorkflowEventType.STEP_SKIPPED,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )

    async def record_tool_invocation(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_type: ActorType,
        actor_identifier: str,
        tool_name: str,
    ) -> WorkflowEvent:
        """Append a `TOOL_INVOKED` event for `step_id` (STORY-010) —
        does NOT change the step's status; it is a pure audit-trail
        addition recording "a tool was dispatched," distinct from the
        step merely starting (`start_step`) or finishing
        (`complete_step`/`fail_step`). `safe_metadata` carries ONLY the
        tool's name — never its arguments or result. Commits
        immediately (there is no corresponding state transition to keep
        atomic with here, unlike `_transition_run`/`_transition_step`).
        """
        step = await workflow_step_repository.get_by_id(
            self._session,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=step_id,
        )
        if step is None:
            raise WorkflowStepNotFoundError("No step found with this id in this workflow.")

        event = await workflow_event_repository.create(
            self._session,
            WorkflowEvent(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                workflow_step_id=step_id,
                event_type=WorkflowEventType.TOOL_INVOKED,
                actor_type=actor_type,
                actor_identifier=actor_identifier,
                safe_metadata={"tool_name": tool_name},
            ),
        )
        await self._session.commit()
        return event


__all__ = [
    "WorkflowConflictError",
    "WorkflowIdempotencyKeyConflictError",
    "WorkflowInitiatorNotActiveMemberError",
    "WorkflowNotFoundError",
    "WorkflowPatientInactiveError",
    "WorkflowPatientNotFoundError",
    "WorkflowService",
    "WorkflowStepNotFoundError",
]
