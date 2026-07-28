"""ReminderService: reminder lifecycle, scoped to one `AsyncSession`.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005 onward. This service owns the reminder STATE
MACHINE (see `_ALLOWED_REMINDER_TRANSITIONS` below) — `app.repositories.reminder`
never decides whether a transition is allowed, exactly like
`app.services.workflow.WorkflowService` already established for
`WorkflowRun`/`WorkflowStep`.

Every reminder this service creates owns its own `WorkflowRun`
(`app.services.workflow.WorkflowService`) — see `schedule_reminder` — so
its FULL lifecycle is always visible in the same durable workflow audit
trail every other AgentCare capability already uses, never a parallel,
undiscoverable side channel. See docs/adr/ADR-0012-reminder-engine.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.reminder import Reminder, ReminderStatus, ReminderType
from app.models.workflow import (
    ActorType,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
)
from app.repositories import reminder as reminder_repository
from app.services.workflow import WorkflowService

_REMINDER_STEP_TYPE = "reminder_delivery"
_REMINDER_SERVICE_ACTOR_IDENTIFIER = "reminder_service"
_DEFAULT_MAX_ATTEMPTS = 5
_LAST_ERROR_MAX_LENGTH = 500

# Centralized reminder-level state machine (see the module docstring).
# Keys are the CURRENT status; values are the set of statuses a
# transition may move to from there. Any transition not listed here is
# rejected as a conflict — mirrors
# `app.services.workflow.WorkflowService._ALLOWED_RUN_TRANSITIONS`
# exactly.
_ALLOWED_REMINDER_TRANSITIONS: dict[ReminderStatus, frozenset[ReminderStatus]] = {
    ReminderStatus.PENDING: frozenset({ReminderStatus.PROCESSING, ReminderStatus.CANCELLED}),
    ReminderStatus.PROCESSING: frozenset(
        {
            ReminderStatus.SENT,
            ReminderStatus.PENDING,
            ReminderStatus.FAILED,
            ReminderStatus.CANCELLED,
        }
    ),
    ReminderStatus.SENT: frozenset(),
    ReminderStatus.FAILED: frozenset(),
    ReminderStatus.CANCELLED: frozenset(),
}


class ReminderNotFoundError(AppException):
    """404: no reminder matches, within the caller's own organization."""

    status_code = 404
    error_code = "reminder_not_found"


class InvalidReminderTransitionError(AppException):
    """422: the requested transition is not valid from the reminder's
    CURRENT status."""

    status_code = 422
    error_code = "invalid_reminder_transition"


class ReminderInitiatorNotActiveMemberError(AppException):
    """422: the initiating user has no ACTIVE membership in this
    organization. Defense-in-depth, not the primary enforcement point —
    see `WorkflowService._ensure_initiator_is_active_member`, which this
    service delegates to via `WorkflowService.create_workflow`."""

    status_code = 422
    error_code = "reminder_initiator_not_active_member"


def _safe_error(message: str) -> str:
    return message[:_LAST_ERROR_MAX_LENGTH]


class ReminderService:
    """Reminder lifecycle, scoped to one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._workflow_service = WorkflowService(session)

    async def schedule_reminder(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        patient_id: uuid.UUID,
        reminder_type: ReminderType,
        scheduled_at: datetime,
        initiated_by_user_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> Reminder:
        """Create a new `PENDING` `Reminder`, together with its OWN
        `WorkflowRun`/`WorkflowStep` (request_type `reminder_delivery`)
        and a `REMINDER_SCHEDULED` audit event — committed as two
        sequential, each-independently-durable steps (create the
        workflow run+step first, then the reminder row), the same
        multi-step-with-independent-commits pattern
        `app.ai.orchestration.AgentOrchestrationService` already
        established, not one large cross-service transaction — see
        docs/adr/ADR-0012-reminder-engine.md "Atomicity".

        `initiated_by_user_id` must be an ACTIVE member of
        `organization_id` — enforced by `WorkflowService.create_workflow`.
        """
        actor_identifier = str(initiated_by_user_id)
        run = await self._workflow_service.create_workflow(
            organization_id=organization_id,
            initiated_by_user_id=initiated_by_user_id,
            request_type=WorkflowRequestType.REMINDER_DELIVERY,
            patient_id=patient_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        step = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            sequence_number=1,
            step_type=_REMINDER_STEP_TYPE,
        )

        reminder = Reminder(
            organization_id=organization_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            reminder_type=reminder_type,
            scheduled_at=scheduled_at,
            status=ReminderStatus.PENDING,
            payload=payload,
            max_attempts=max_attempts,
        )
        await reminder_repository.create(self._session, reminder)
        await self._session.commit()

        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_REMINDER_SERVICE_ACTOR_IDENTIFIER,
            event_type=WorkflowEventType.REMINDER_SCHEDULED,
            safe_metadata={
                "reminder_id": str(reminder.id),
                "reminder_type": reminder_type.value,
                "scheduled_at": scheduled_at.isoformat(),
            },
        )
        return reminder

    async def get_reminder(
        self, *, organization_id: uuid.UUID, reminder_id: uuid.UUID
    ) -> Reminder:
        """Tenant-scoped retrieval by id."""
        reminder = await reminder_repository.get_by_id(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")
        return reminder

    async def cancel_reminder(
        self,
        *,
        organization_id: uuid.UUID,
        reminder_id: uuid.UUID,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_identifier: str = _REMINDER_SERVICE_ACTOR_IDENTIFIER,
    ) -> Reminder:
        """`PENDING`/`PROCESSING` -> `CANCELLED`. Rejected (not silently
        ignored) if already terminal (`SENT`/`FAILED`/`CANCELLED`) — the
        same uniform rule `app.services.appointment.AppointmentService`
        already applies to invalid transitions."""
        reminder = await reminder_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")
        self._ensure_allowed(reminder.status, ReminderStatus.CANCELLED)

        cancelled_at = datetime.now(UTC)
        run_status_before_cancel = await self._workflow_run_status(reminder.workflow_run_id)
        await reminder_repository.cancel(
            self._session,
            organization_id=organization_id,
            reminder_id=reminder_id,
            cancelled_at=cancelled_at,
        )
        await self._session.commit()

        # A run already terminal (e.g. a reminder cancelled after its
        # retry-exhausted run already failed — should not happen given
        # the transition guard above, but defended here too) is never
        # re-transitioned; `cancel_workflow` would reject it anyway.
        _terminal = frozenset(
            {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
        )
        if run_status_before_cancel is not None and run_status_before_cancel not in _terminal:
            await self._workflow_service.cancel_workflow(
                organization_id=organization_id,
                workflow_run_id=reminder.workflow_run_id,
                actor_type=actor_type,
                actor_identifier=actor_identifier,
            )
        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            step_id=reminder.workflow_step_id,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.REMINDER_CANCELLED,
            safe_metadata={"reminder_id": str(reminder.id)},
        )
        return reminder

    async def cancel_reminders_for_appointment(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_identifier: str = _REMINDER_SERVICE_ACTOR_IDENTIFIER,
    ) -> list[Reminder]:
        """Cancel every still-cancellable (`PENDING`/`PROCESSING`)
        reminder for one appointment — the automatic-scheduling
        integration point `app.services.appointment.AppointmentService`
        calls on reschedule/cancellation. Never touches an already
        `SENT`/`CANCELLED`/`FAILED` reminder."""
        candidates = await reminder_repository.list_by_appointment(
            self._session,
            organization_id=organization_id,
            appointment_id=appointment_id,
            statuses=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
        )
        cancelled: list[Reminder] = []
        for candidate in candidates:
            cancelled.append(
                await self.cancel_reminder(
                    organization_id=organization_id,
                    reminder_id=candidate.id,
                    actor_type=actor_type,
                    actor_identifier=actor_identifier,
                )
            )
        return cancelled

    async def reschedule_reminder(
        self, *, organization_id: uuid.UUID, reminder_id: uuid.UUID, scheduled_at: datetime
    ) -> Reminder:
        """Move an existing `PENDING` reminder to a new `scheduled_at`,
        IN PLACE — same id, same workflow run, no cancel-plus-recreate.
        Only legal while `PENDING` (not yet locked/processing/sent) —
        see docs/adr/ADR-0012-reminder-engine.md. Records a fresh
        `REMINDER_SCHEDULED` event noting the previous time, rather than
        a dedicated sixth event type, since "rescheduled" and "scheduled
        again" are the same fact from the audit trail's point of view.
        """
        reminder = await reminder_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")
        if reminder.status is not ReminderStatus.PENDING:
            raise InvalidReminderTransitionError("Only a pending reminder can be rescheduled.")

        previous_scheduled_at = reminder.scheduled_at
        reminder.scheduled_at = scheduled_at
        await reminder_repository.update(self._session, reminder)
        await self._session.commit()

        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            step_id=reminder.workflow_step_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_REMINDER_SERVICE_ACTOR_IDENTIFIER,
            event_type=WorkflowEventType.REMINDER_SCHEDULED,
            safe_metadata={
                "reminder_id": str(reminder.id),
                "previous_scheduled_at": previous_scheduled_at.isoformat(),
                "scheduled_at": scheduled_at.isoformat(),
            },
        )
        return reminder

    async def retry_failed(
        self,
        *,
        organization_id: uuid.UUID,
        reminder_id: uuid.UUID,
        initiated_by_user_id: uuid.UUID,
    ) -> Reminder:
        """Explicitly retry a `FAILED` (attempts-exhausted) reminder.

        The ORIGINAL `WorkflowRun` is already terminal
        (`WorkflowStatus.FAILED` allows no outgoing transition — see
        `WorkflowService._ALLOWED_RUN_TRANSITIONS`) and stays that way
        permanently, as an honest historical record of the exhausted
        attempt batch. An explicit operator-authorized retry is modeled
        as a NEW administrative execution: a fresh `WorkflowRun`/
        `WorkflowStep`, with `Reminder.workflow_run_id`/`workflow_step_id`
        reassigned to it. `max_attempts` is incremented by exactly one —
        granting exactly the single additional attempt this call
        authorizes, not an unbounded reset.
        """
        reminder = await reminder_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")
        if reminder.status is not ReminderStatus.FAILED:
            raise InvalidReminderTransitionError("Only a failed reminder can be retried.")

        actor_identifier = str(initiated_by_user_id)
        run = await self._workflow_service.create_workflow(
            organization_id=organization_id,
            initiated_by_user_id=initiated_by_user_id,
            request_type=WorkflowRequestType.REMINDER_DELIVERY,
            patient_id=reminder.patient_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        step = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            sequence_number=1,
            step_type=_REMINDER_STEP_TYPE,
        )

        reminder.workflow_run_id = run.id
        reminder.workflow_step_id = step.id
        reminder.status = ReminderStatus.PENDING
        reminder.scheduled_at = datetime.now(UTC)
        reminder.max_attempts += 1
        reminder.last_error = None
        await reminder_repository.update(self._session, reminder)
        await self._session.commit()

        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_REMINDER_SERVICE_ACTOR_IDENTIFIER,
            event_type=WorkflowEventType.REMINDER_SCHEDULED,
            safe_metadata={"reminder_id": str(reminder.id), "retry": True},
        )
        return reminder

    async def mark_sent(
        self, *, organization_id: uuid.UUID, reminder_id: uuid.UUID, actor_identifier: str
    ) -> Reminder:
        """`PROCESSING` -> `SENT`. Called by
        `app.workers.reminder_worker.ReminderWorker` after a
        `NotificationProvider.send()` call genuinely succeeds — never a
        hardcoded success path."""
        reminder = await reminder_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")
        self._ensure_allowed(reminder.status, ReminderStatus.SENT)

        sent_at = datetime.now(UTC)
        await reminder_repository.mark_sent(
            self._session, organization_id=organization_id, reminder_id=reminder_id, sent_at=sent_at
        )
        await self._session.commit()

        # `REMINDER_SENT` recorded BEFORE the generic `step_completed`/
        # `workflow_completed` transitions — mirrors STORY-010's
        # `tool_invoked` -> `step_completed` ordering exactly (the
        # domain-specific event always precedes the generic lifecycle
        # event it explains).
        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            step_id=reminder.workflow_step_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.REMINDER_SENT,
            safe_metadata={
                "reminder_id": str(reminder.id),
                "attempt_count": reminder.attempt_count,
            },
        )
        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            step_id=reminder.workflow_step_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=actor_identifier,
            safe_metadata={"attempt_count": reminder.attempt_count},
        )
        await self._workflow_service.complete_workflow(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=actor_identifier,
        )
        return reminder

    async def mark_failed(
        self,
        *,
        organization_id: uuid.UUID,
        reminder_id: uuid.UUID,
        safe_error: str,
        actor_identifier: str,
    ) -> Reminder:
        """`PROCESSING` -> `PENDING` (if attempts remain, for a future
        retry on the worker's own next poll) or `PROCESSING` -> `FAILED`
        (attempts exhausted — a terminal, permanently-recorded failure).
        Called by `ReminderWorker` after a `NotificationProvider.send()`
        call genuinely fails or raises. `safe_error` must already be a
        short, bounded, safe description — never a raw exception/stack
        trace (see `app.notifications.base.NotificationResult`)."""
        reminder = await reminder_repository.get_by_id_for_update(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")

        exhausted = reminder.attempt_count >= reminder.max_attempts
        next_status = ReminderStatus.FAILED if exhausted else ReminderStatus.PENDING
        self._ensure_allowed(reminder.status, next_status)

        bounded_error = _safe_error(safe_error)
        await reminder_repository.mark_failed(
            self._session,
            organization_id=organization_id,
            reminder_id=reminder_id,
            safe_error=bounded_error,
            next_status=next_status,
        )
        await self._session.commit()

        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            step_id=reminder.workflow_step_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.REMINDER_FAILED,
            safe_metadata={
                "reminder_id": str(reminder.id),
                "attempt_count": reminder.attempt_count,
                "exhausted": exhausted,
            },
        )

        if exhausted:
            await self._workflow_service.fail_step(
                organization_id=organization_id,
                workflow_run_id=reminder.workflow_run_id,
                step_id=reminder.workflow_step_id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=actor_identifier,
                failure_code="reminder_delivery_failed",
                failure_message_safe=bounded_error,
            )
            await self._workflow_service.fail_workflow(
                organization_id=organization_id,
                workflow_run_id=reminder.workflow_run_id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=actor_identifier,
                failure_code="reminder_delivery_failed",
                failure_message_safe=bounded_error,
            )
        return reminder

    async def mark_started(
        self, *, organization_id: uuid.UUID, reminder_id: uuid.UUID, actor_identifier: str
    ) -> None:
        """Record the FIRST attempt's start: `PENDING` -> `RUNNING` on
        both the reminder's `WorkflowRun` and its single `WorkflowStep`
        (a one-time transition — see `WorkflowService.start_workflow`/
        `start_step`), plus a `REMINDER_STARTED` audit event for EVERY
        attempt (first or retry). Called by `ReminderWorker` once per
        attempt, immediately after `acquire_pending`/`mark_processing`
        claims the reminder.

        Re-validates `reminder.status is PROCESSING` — closing a narrow
        but genuine race: a reminder could be concurrently cancelled
        between the worker's `acquire_pending` commit and this call. If
        so, this raises `InvalidReminderTransitionError` and the worker
        MUST abort delivery for this reminder rather than notify a
        patient about a cancelled appointment.
        """
        reminder = await reminder_repository.get_by_id(
            self._session, organization_id=organization_id, reminder_id=reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError("No reminder found with this id in this organization.")
        if reminder.status is not ReminderStatus.PROCESSING:
            raise InvalidReminderTransitionError(
                f"Cannot start delivery: reminder is '{reminder.status.value}', not 'processing'."
            )
        run_status = await self._workflow_run_status(reminder.workflow_run_id)

        if run_status is WorkflowStatus.PENDING:
            await self._workflow_service.start_workflow(
                organization_id=organization_id,
                workflow_run_id=reminder.workflow_run_id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=actor_identifier,
            )
            await self._workflow_service.start_step(
                organization_id=organization_id,
                workflow_run_id=reminder.workflow_run_id,
                step_id=reminder.workflow_step_id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=actor_identifier,
            )

        await self._workflow_service.record_reminder_event(
            organization_id=organization_id,
            workflow_run_id=reminder.workflow_run_id,
            step_id=reminder.workflow_step_id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=actor_identifier,
            event_type=WorkflowEventType.REMINDER_STARTED,
            safe_metadata={
                "reminder_id": str(reminder.id),
                "attempt_count": reminder.attempt_count,
            },
        )

    async def _workflow_run_status(self, workflow_run_id: uuid.UUID) -> WorkflowStatus | None:
        # Deliberately organization-agnostic here: the caller already
        # holds a row-locked, tenant-verified `Reminder` whose
        # `workflow_run_id` composite FK guarantees this run belongs to
        # the SAME organization — a second explicit tenant check would
        # be redundant, not a real additional guarantee.
        result = await self._session.execute(
            select(WorkflowRun.status).where(WorkflowRun.id == workflow_run_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _ensure_allowed(current: ReminderStatus, target: ReminderStatus) -> None:
        allowed = _ALLOWED_REMINDER_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise InvalidReminderTransitionError(
                f"Cannot transition reminder from '{current.value}' to '{target.value}'."
            )


__all__ = [
    "InvalidReminderTransitionError",
    "ReminderInitiatorNotActiveMemberError",
    "ReminderNotFoundError",
    "ReminderService",
]
