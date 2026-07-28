"""ApprovalService: human-in-the-loop approval lifecycle, scoped to one
`AsyncSession`.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005 onward. This service owns the approval STATE
MACHINE — `app.repositories.approval` never decides whether a transition
is allowed, exactly like `app.services.workflow.WorkflowService`/
`app.services.reminder.ReminderService` already established.

An `ApprovalRequest` is never created standalone: `create_approval_request`
ALWAYS pauses the EXACT step it gates (and that step's run) in the same
call, and `approve`/`reject`/`expire_approval` ALWAYS resolve that pause
in the same call — a `PENDING` approval and a `WAITING` step/run are
always the same fact, never two independently maintained ones. See
docs/adr/ADR-0013-human-in-the-loop-approvals.md.

Terminal-state mapping (see the ADR for the full rationale):
- `approve`: resume (step+run `WAITING` -> `RUNNING`) then complete
  (step+run -> `COMPLETED`). This story scopes "resume exactly where
  paused" to the WORKFLOW's own state transitions completing — never an
  automatic tool-call replay (`ApprovalRequest` carries no tool
  payload).
- `reject`: resume, then fail the step (`failure_code="approval_rejected"`)
  and cancel the run.
- expire (lazy, checked at the top of `approve`/`reject`, or explicit via
  `expire_approval`): fail the step and the run directly from `WAITING`
  (`failure_code="approval_expired"`) — no resume, since there is no
  decision to act on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.approval import (
    REASON_MAX_LENGTH,
    REQUESTED_BY_AGENT_MAX_LENGTH,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from app.models.workflow import ActorType, WorkflowEventType
from app.repositories import approval as approval_repository
from app.repositories import workflow_run as workflow_run_repository
from app.repositories import workflow_step as workflow_step_repository
from app.services.workflow import (
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowStepNotFoundError,
)

_DEFAULT_APPROVAL_EXPIRY = timedelta(hours=24)
_APPROVAL_SERVICE_ACTOR_IDENTIFIER = "approval_service"
_MANUAL_REQUESTED_BY_AGENT = "manual"
_APPROVAL_EXPIRED_FAILURE_CODE = "approval_expired"
_APPROVAL_REJECTED_FAILURE_CODE = "approval_rejected"
_APPROVAL_EXPIRED_MESSAGE = "The approval request expired before a decision was made."
_APPROVAL_REJECTED_MESSAGE = "The approval request was rejected."


class ApprovalNotFoundError(AppException):
    """404: no approval matches, within the caller's own organization."""

    status_code = 404
    error_code = "approval_not_found"


class InvalidApprovalTransitionError(AppException):
    """422: the requested transition is not valid from the approval's
    CURRENT status."""

    status_code = 422
    error_code = "invalid_approval_transition"


class ApprovalExpiredError(AppException):
    """422: the approval was still `PENDING` but past `expires_at` — it
    has now been auto-transitioned to `EXPIRED` (lazy expiration, applied
    before this error is raised), and this approve/reject attempt is
    rejected, never silently applied to an approval no one decided on in
    time."""

    status_code = 422
    error_code = "approval_expired"


def _safe_reason(reason: str) -> str:
    return reason[:REASON_MAX_LENGTH]


def _safe_agent_name(name: str) -> str:
    return name[:REQUESTED_BY_AGENT_MAX_LENGTH]


class ApprovalService:
    """Approval lifecycle, scoped to one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._workflow_service = WorkflowService(session)

    async def create_approval_request(
        self,
        *,
        organization_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
        workflow_step_id: uuid.UUID,
        approval_type: ApprovalType,
        reason: str,
        actor_identifier: str,
        requested_by_agent: str = _MANUAL_REQUESTED_BY_AGENT,
        actor_type: ActorType = ActorType.USER,
        expiry: timedelta = _DEFAULT_APPROVAL_EXPIRY,
    ) -> ApprovalRequest:
        """Create a new `PENDING` `ApprovalRequest` for an EXISTING,
        currently `RUNNING` step (and run), and pause both — a
        `STEP_WAITING`/`WORKFLOW_WAITING` pair.

        Pauses the step/run BEFORE creating the `ApprovalRequest` row,
        deliberately the reverse of `ReminderService.schedule_reminder`'s
        "durable record first" ordering: `mark_step_waiting` is also
        this method's CONCURRENCY GATE (its row lock is what serializes
        two callers racing to pause the same step — see
        `app.services.workflow.WorkflowService._transition_step`). If a
        concurrent racer already paused the step, this call fails with
        `WorkflowConflictError` at that gate and NO `ApprovalRequest` row
        is ever created for the loser — an orphaned `PENDING` approval
        with no real paused step behind it is structurally impossible,
        not merely made unlikely.

        `requested_by_agent` defaults to `"manual"` for a human-initiated
        request (e.g. via the API) — the Coordinator agent's own calls
        (see `app.ai.orchestration.AgentOrchestrationService`) always
        pass its stable agent name explicitly. Raises
        `WorkflowNotFoundError`/`WorkflowStepNotFoundError` if the run/
        step does not exist in this organization, or
        `WorkflowConflictError` if either is not currently `RUNNING` —
        an approval can only pause a step that is actively executing.
        """
        run = await workflow_run_repository.get_by_id(
            self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
        )
        if run is None:
            raise WorkflowNotFoundError("No workflow found with this id in this organization.")
        step = await workflow_step_repository.get_by_id(
            self._session,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=workflow_step_id,
        )
        if step is None:
            raise WorkflowStepNotFoundError("No step found with this id in this workflow.")

        await self._workflow_service.mark_step_waiting(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=workflow_step_id,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.mark_waiting(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
        )

        now = datetime.now(UTC)
        approval = ApprovalRequest(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            approval_type=approval_type,
            status=ApprovalStatus.PENDING,
            reason=_safe_reason(reason),
            requested_by_agent=_safe_agent_name(requested_by_agent),
            expires_at=now + expiry,
        )
        await approval_repository.create(self._session, approval)
        await self._session.commit()

        await self._workflow_service.record_approval_event(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            step_id=workflow_step_id,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.APPROVAL_REQUESTED,
            safe_metadata={
                "approval_id": str(approval.id),
                "approval_type": approval_type.value,
            },
        )
        return approval

    async def get_approval(
        self, *, organization_id: uuid.UUID, approval_id: uuid.UUID
    ) -> ApprovalRequest:
        """Tenant-scoped retrieval by id."""
        approval = await approval_repository.get_by_id(
            self._session, organization_id=organization_id, approval_id=approval_id
        )
        if approval is None:
            raise ApprovalNotFoundError("No approval found with this id in this organization.")
        return approval

    async def get_pending_for_run(
        self, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
    ) -> ApprovalRequest | None:
        """STORY-015: is `workflow_run_id` currently gated by a `PENDING`
        approval? `None` if not — see
        `app.repositories.approval.get_pending_for_workflow_run`."""
        return await approval_repository.get_pending_for_workflow_run(
            self._session, organization_id=organization_id, workflow_run_id=workflow_run_id
        )

    async def list_pending_approvals(
        self, *, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[ApprovalRequest]:
        """The actionable approval queue: `PENDING` approvals only,
        oldest first."""
        return list(
            await approval_repository.list_pending(
                self._session, organization_id=organization_id, limit=limit, offset=offset
            )
        )

    async def approve(
        self,
        *,
        organization_id: uuid.UUID,
        approval_id: uuid.UUID,
        approved_by_user: uuid.UUID,
        actor_identifier: str,
    ) -> ApprovalRequest:
        """`PENDING` -> `APPROVED`, then resume and complete the paused
        step/run. Lazily expires (and rejects this attempt with
        `ApprovalExpiredError`) if `expires_at` has already passed — see
        the module docstring."""
        approval = await self._lock_pending_or_expire(
            organization_id=organization_id, approval_id=approval_id
        )

        approved_at = datetime.now(UTC)
        await approval_repository.approve(
            self._session,
            organization_id=organization_id,
            approval_id=approval_id,
            approved_by_user=approved_by_user,
            approved_at=approved_at,
        )
        await self._session.commit()

        await self._workflow_service.record_approval_event(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.APPROVAL_GRANTED,
            safe_metadata={"approval_id": str(approval.id)},
        )
        await self._workflow_service.resume_step(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.resume_workflow(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            safe_metadata={"approval_id": str(approval.id)},
        )
        await self._workflow_service.complete_workflow(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        return approval

    async def reject(
        self,
        *,
        organization_id: uuid.UUID,
        approval_id: uuid.UUID,
        rejected_by_user: uuid.UUID,
        actor_identifier: str,
    ) -> ApprovalRequest:
        """`PENDING` -> `REJECTED`, then resume the paused step/run and
        bring both to a terminal, cancelled state
        (`failure_code="approval_rejected"`). Lazily expires the same
        way `approve` does."""
        approval = await self._lock_pending_or_expire(
            organization_id=organization_id, approval_id=approval_id
        )

        rejected_at = datetime.now(UTC)
        await approval_repository.reject(
            self._session,
            organization_id=organization_id,
            approval_id=approval_id,
            rejected_by_user=rejected_by_user,
            rejected_at=rejected_at,
        )
        await self._session.commit()

        await self._workflow_service.record_approval_event(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.APPROVAL_REJECTED,
            safe_metadata={"approval_id": str(approval.id)},
        )
        await self._workflow_service.resume_step(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.resume_workflow(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.fail_step(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            failure_code=_APPROVAL_REJECTED_FAILURE_CODE,
            failure_message_safe=_APPROVAL_REJECTED_MESSAGE,
        )
        await self._workflow_service.cancel_workflow(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        return approval

    async def expire_approval(
        self, *, organization_id: uuid.UUID, approval_id: uuid.UUID
    ) -> ApprovalRequest:
        """Explicitly expire a `PENDING` approval past its deadline.
        Called internally by `approve`/`reject`'s lazy check, and usable
        directly (e.g. by an operator or a future scheduled sweep) —
        this story ships no background expiry worker; see
        docs/adr/ADR-0013-human-in-the-loop-approvals.md."""
        approval = await approval_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, approval_id=approval_id
        )
        if approval is None:
            raise ApprovalNotFoundError("No approval found with this id in this organization.")
        if approval.status is not ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError("Only a pending approval can expire.")

        await approval_repository.expire(
            self._session, organization_id=organization_id, approval_id=approval_id
        )
        await self._session.commit()
        await self._fail_step_and_workflow(
            organization_id=organization_id,
            approval=approval,
            failure_code=_APPROVAL_EXPIRED_FAILURE_CODE,
            failure_message=_APPROVAL_EXPIRED_MESSAGE,
        )
        return approval

    async def _lock_pending_or_expire(
        self, *, organization_id: uuid.UUID, approval_id: uuid.UUID
    ) -> ApprovalRequest:
        """Lock the approval row and return it if it is genuinely
        actionable (`PENDING` and not past `expires_at`). Auto-expires
        (and raises `ApprovalExpiredError`) a `PENDING`-but-overdue
        approval instead of letting `approve`/`reject` silently act on
        it. Raises `InvalidApprovalTransitionError` if already resolved
        (`APPROVED`/`REJECTED`) or already `EXPIRED`."""
        approval = await approval_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, approval_id=approval_id
        )
        if approval is None:
            raise ApprovalNotFoundError("No approval found with this id in this organization.")

        if approval.status is ApprovalStatus.PENDING and approval.expires_at <= datetime.now(UTC):
            await approval_repository.expire(
                self._session, organization_id=organization_id, approval_id=approval_id
            )
            await self._session.commit()
            await self._fail_step_and_workflow(
                organization_id=organization_id,
                approval=approval,
                failure_code=_APPROVAL_EXPIRED_FAILURE_CODE,
                failure_message=_APPROVAL_EXPIRED_MESSAGE,
            )
            raise ApprovalExpiredError(
                "This approval request has expired and can no longer be decided."
            )

        if approval.status is not ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError(
                f"Cannot transition approval from '{approval.status.value}' — only a "
                "pending approval can be approved or rejected."
            )
        return approval

    async def _fail_step_and_workflow(
        self,
        *,
        organization_id: uuid.UUID,
        approval: ApprovalRequest,
        failure_code: str,
        failure_message: str,
    ) -> None:
        """Bring an approval's paused step/run directly from `WAITING`
        to a terminal `FAILED` state — no resume, since expiry means no
        decision was ever made to act on."""
        await self._workflow_service.fail_step(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            step_id=approval.workflow_step_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_APPROVAL_SERVICE_ACTOR_IDENTIFIER,
            failure_code=failure_code,
            failure_message_safe=failure_message,
        )
        await self._workflow_service.fail_workflow(
            organization_id=organization_id,
            workflow_run_id=approval.workflow_run_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_APPROVAL_SERVICE_ACTOR_IDENTIFIER,
            failure_code=failure_code,
            failure_message_safe=failure_message,
        )


__all__ = [
    "ApprovalExpiredError",
    "ApprovalNotFoundError",
    "ApprovalService",
    "InvalidApprovalTransitionError",
]
