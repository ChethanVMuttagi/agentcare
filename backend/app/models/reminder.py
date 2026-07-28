"""Reminder / ReminderAttempt: durable, restart-safe background reminder
delivery (STORY-013).

A `Reminder` is a durably persisted unit of future work — "notify this
patient about this appointment at this time" — processed by
`app.workers.reminder_worker.ReminderWorker`, never an in-memory queue or
a background thread that could lose work on a crash or restart. Every
reminder always belongs to exactly one `Organization` (tenant isolation),
references the `Appointment`/`Patient` it concerns, and is linked to its
OWN `WorkflowRun` (`workflow_run_id`, NOT NULL) — see
`app.services.reminder.ReminderService.schedule_reminder` — so its full
lifecycle (scheduled -> started -> sent/failed -> [cancelled]) is always
visible in the SAME workflow audit trail STORY-009/010/011 already
established, never a parallel, undiscoverable side channel.

`ReminderAttempt` is a separate, append-only per-attempt audit row —
distinct from `Reminder.attempt_count`/`last_error` (which reflect only
the LATEST state): it records EVERY attempt's outcome, mirroring
`WorkflowEvent`'s append-only design (STORY-009 Section 5) at the
reminder-attempt granularity.

Row-level concurrency (`locked_at`/`locked_by`) implements the standard
PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` job-queue pattern — see
`app.repositories.reminder.acquire_pending` and docs/adr/ADR-0012-reminder-engine.md
"Concurrency Strategy".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.organization import Organization

# Bounds applied via database CHECK constraints — see each model's
# __table_args__. Kept here so the model, its constraints, and
# service-level validation reference the same literal values, mirroring
# `app/models/workflow.py`'s identical pattern.
LOCKED_BY_MAX_LENGTH = 100
LAST_ERROR_MAX_LENGTH = 500
SAFE_ERROR_MESSAGE_MAX_LENGTH = 500
PROVIDER_NAME_MAX_LENGTH = 100
PAYLOAD_MAX_BYTES = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReminderType(StrEnum):
    """The kind of reminder a `Reminder` represents.

    Deliberately small and closed — see docs/DOMAIN_MODEL.md "Enum
    Strategy" for the same controlled-evolution rationale as every other
    enum in this codebase. Only `APPOINTMENT_REMINDER` is scheduled
    automatically in this story (see
    `app.services.reminder_scheduler.ReminderScheduler`); the type
    exists as a real enum (not a hardcoded literal) so a future reminder
    kind (e.g. a document-collection follow-up) is an additive enum
    value plus a new `ReminderScheduler` method, not a schema change.
    """

    APPOINTMENT_REMINDER = "appointment_reminder"


class ReminderStatus(StrEnum):
    """Controlled reminder lifecycle status. Transitions are centralized
    in `app.services.reminder.ReminderService` — see
    `_ALLOWED_REMINDER_TRANSITIONS` there. Never checked/enforced by
    scattered `if status == ...` logic elsewhere, the same discipline
    `app.services.workflow.WorkflowService` already established for
    `WorkflowStatus`.

    - `PENDING`: scheduled, not yet due or not yet picked up by a worker.
    - `PROCESSING`: currently locked by a worker (`locked_at`/`locked_by`
      set) — mid-delivery attempt.
    - `SENT`: delivered successfully; terminal.
    - `FAILED`: delivery attempts exhausted (`attempt_count >=
      max_attempts`); terminal.
    - `CANCELLED`: explicitly cancelled (e.g. the appointment it concerns
      was rescheduled or cancelled) before it was ever sent; terminal.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReminderAttemptStatus(StrEnum):
    """The outcome of one single delivery attempt."""

    SENT = "sent"
    FAILED = "failed"


class Reminder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durably persisted reminder-delivery job, scoped to one
    `Organization`.

    Tenant/ownership integrity is enforced at the DATABASE level, the
    same composite-FK technique used throughout this codebase:
    `(organization_id, appointment_id) -> appointments(organization_id, id)`,
    `(organization_id, patient_id) -> patients(organization_id, id)`, and
    `(organization_id, workflow_run_id) -> workflow_runs(organization_id, id)`
    each guarantee the referenced row belongs to the SAME organization —
    raw SQL cannot attach an Org A reminder to an Org B appointment,
    patient, or workflow run.

    `payload` is an OPTIONAL, size-bounded (`PAYLOAD_MAX_BYTES`) JSONB
    column holding ONLY bounded, already-safe structured detail needed
    to render a notification later (e.g.
    `{"appointment_start_at": "2026-08-01T10:00:00+00:00"}`) — mirrors
    `WorkflowEvent.safe_metadata`'s exact discipline (STORY-009 Section
    12): never a patient name, contact detail, or free-form clinical
    text. See `app.notifications.base.NotificationMessage` for what a
    `NotificationProvider` actually sends — deliberately built from safe
    identifiers/timestamps only, never PHI.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_reminders_organization_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "appointment_id"],
            ["appointments.organization_id", "appointments.id"],
            name="fk_reminders_org_appointment_appointments",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_reminders_org_patient_patients",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_reminders_org_workflow_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id", "workflow_step_id"],
            [
                "workflow_steps.organization_id",
                "workflow_steps.workflow_run_id",
                "workflow_steps.id",
            ],
            name="fk_reminders_org_run_step_workflow_steps",
            ondelete="RESTRICT",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint(
            "(locked_at IS NULL) = (locked_by IS NULL)", name="lock_fields_consistent"
        ),
        CheckConstraint(
            f"last_error IS NULL OR length(last_error) <= {LAST_ERROR_MAX_LENGTH}",
            name="last_error_length",
        ),
        CheckConstraint(
            "payload IS NULL OR "
            f"octet_length(payload::text) <= {PAYLOAD_MAX_BYTES}",
            name="payload_size",
        ),
        # The worker's acquire query (`app.repositories.reminder.acquire_pending`)
        # is the hottest read path in this domain — filters on `status`
        # and `scheduled_at` together on every poll.
        Index("ix_reminders_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_reminders_org_patient", "organization_id", "patient_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # No plain FK for `appointment_id`/`patient_id`/`workflow_run_id`:
    # enforced solely via the composite ownership FKs above (mirrors
    # `WorkflowRun.patient_id` — see app/models/workflow.py). No
    # `relationship()` is built off any of these three for the same
    # reason `Department.facility_id` has none.
    appointment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # NOT NULL, deliberately: every `Reminder` this codebase ever creates
    # is created BY `ReminderService.schedule_reminder`, which ALWAYS
    # creates the reminder's own `WorkflowRun`/`WorkflowStep` first — a
    # reminder with no workflow history is never possible, not merely
    # avoided by convention. See docs/adr/ADR-0012-reminder-engine.md.
    # `workflow_run_id` is REASSIGNED (to a brand new run) by
    # `ReminderService.retry_failed` after the original run reaches the
    # terminal `FAILED` status — see that method's docstring for why an
    # explicit post-exhaustion retry is modeled as a new administrative
    # execution rather than resurrecting a terminal one.
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    reminder_type: Mapped[ReminderType] = mapped_column(
        SqlEnum(
            ReminderType,
            name="reminder_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[ReminderStatus] = mapped_column(
        SqlEnum(
            ReminderStatus,
            name="reminder_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ReminderStatus.PENDING,
        index=True,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # Set together, cleared together (see `lock_fields_consistent` CHECK
    # above) by `app.repositories.reminder.acquire_pending`. `locked_by`
    # is a short, opaque worker-instance identifier (e.g.
    # `"reminder-worker-<uuid4>"`) — never a hostname, IP, or credential.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(LOCKED_BY_MAX_LENGTH), nullable=True)
    # Safe, bounded failure summary ONLY — never a raw exception, stack
    # trace, or SQL statement (mirrors `WorkflowRun.failure_message_safe`
    # — see docs/WORKFLOWS.md Section 13).
    last_error: Mapped[str | None] = mapped_column(String(LAST_ERROR_MAX_LENGTH), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship()

    def __repr__(self) -> str:
        return (
            f"Reminder(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"status={self.status!r}, scheduled_at={self.scheduled_at!r})"
        )


class ReminderAttempt(UUIDPrimaryKeyMixin, Base):
    """One single delivery attempt's outcome for a `Reminder` — an
    APPEND-ONLY audit row, never updated after creation (same
    architectural guarantee as `WorkflowEvent` — see
    docs/WORKFLOWS.md "Event Immutability"). Deliberately does NOT use
    `TimestampMixin` for the identical reason: only `created_at` is
    meaningful here.

    `(organization_id, reminder_id) -> reminders(organization_id, id)` is
    a composite FK guaranteeing an attempt can never be attached to a
    reminder in a different organization. `UNIQUE(reminder_id,
    attempt_number)` guarantees attempts within one reminder have a
    well-defined, non-duplicated sequence.
    """

    __tablename__ = "reminder_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "reminder_id"],
            ["reminders.organization_id", "reminders.id"],
            name="fk_reminder_attempts_org_reminder_reminders",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "reminder_id", "attempt_number", name="uq_reminder_attempts_reminder_attempt_number"
        ),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "safe_error_message IS NULL OR "
            f"length(safe_error_message) <= {SAFE_ERROR_MESSAGE_MAX_LENGTH}",
            name="safe_error_message_length",
        ),
        Index("ix_reminder_attempts_reminder_id_attempt_number", "reminder_id", "attempt_number"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Plain FK (in addition to the composite ownership FK above) purely
    # so `relationship()` has an unambiguous join path — mirrors
    # `Appointment.patient_id`'s identical reasoning.
    reminder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reminders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReminderAttemptStatus] = mapped_column(
        SqlEnum(
            ReminderAttemptStatus,
            name="reminder_attempt_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    provider_name: Mapped[str] = mapped_column(String(PROVIDER_NAME_MAX_LENGTH), nullable=False)
    # Safe, bounded — never a raw exception/stack trace. NULL when
    # `status` is `SENT`.
    safe_error_message: Mapped[str | None] = mapped_column(
        String(SAFE_ERROR_MESSAGE_MAX_LENGTH), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Unlike `WorkflowEvent.sequence` (STORY-010), no separate
    # database-assigned ordering column is needed here: `attempt_number`
    # is caller-assigned, deterministic, and already tie-free within one
    # reminder (`UNIQUE(reminder_id, attempt_number)`) — it never relies
    # on `created_at` for ordering, so the same tie-at-Python-timestamp-
    # resolution problem `sequence` was added to fix cannot occur here.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    reminder: Mapped[Reminder] = relationship(foreign_keys=[reminder_id])

    def __repr__(self) -> str:
        return (
            f"ReminderAttempt(id={self.id!r}, reminder_id={self.reminder_id!r}, "
            f"attempt_number={self.attempt_number!r}, status={self.status!r})"
        )
