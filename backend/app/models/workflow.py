"""Durable workflow state: `WorkflowRun`, `WorkflowStep`, `WorkflowEvent`.

This is the persistence foundation future AgentCare agents/tools will run
on top of — see docs/WORKFLOWS.md and
docs/adr/ADR-0009-durable-workflow-state.md. STORY-009 implements
persistence and lifecycle mechanics ONLY: there is no LLM client, no
agent framework, no LangGraph, no prompts, and no autonomous
decision-making anywhere in this module or the service that manages it
(`app.services.workflow`).

Deliberately three tables, not a general-purpose BPM platform:

- `WorkflowRun` — one administrative request/execution.
- `WorkflowStep` — one durable execution unit within a run.
- `WorkflowEvent` — an APPEND-ONLY audit/history trail for a run (and,
  optionally, one specific step within it).

Healthcare safety boundary: every `WorkflowRequestType` value is
administrative (booking/rescheduling/cancelling appointments, collecting
a requested document, routing an administrative request, coordinating a
follow-up) — never diagnosis, treatment selection, medication,
dosage, or an autonomous medical-urgency judgment. See
docs/WORKFLOWS.md "Healthcare Safety Boundary".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
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
# __table_args__. Kept here so the model and its constraints reference
# the same literal values as the service-level validation
# (`app.services.workflow`) and the tests.
FAILURE_CODE_MAX_LENGTH = 64
FAILURE_MESSAGE_MAX_LENGTH = 500
ACTOR_IDENTIFIER_MAX_LENGTH = 100
IDEMPOTENCY_KEY_MAX_LENGTH = 255
SAFE_METADATA_MAX_BYTES = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowRequestType(StrEnum):
    """The administrative category a `WorkflowRun` represents.

    Deliberately small and closed to what this project's current/near-
    future roadmap actually supports — see docs/WORKFLOWS.md "Healthcare
    Safety Boundary". No diagnosis/treatment category exists, or may be
    added without a fresh, explicit design decision.
    """

    APPOINTMENT_BOOKING = "appointment_booking"
    APPOINTMENT_RESCHEDULING = "appointment_rescheduling"
    APPOINTMENT_CANCELLATION = "appointment_cancellation"
    DOCUMENT_COLLECTION = "document_collection"
    ADMINISTRATIVE_ROUTING = "administrative_routing"
    FOLLOW_UP = "follow_up"


class WorkflowStatus(StrEnum):
    """`WorkflowRun` lifecycle status. Transitions are centralized in
    `app.services.workflow.WorkflowService` — see
    `_ALLOWED_RUN_TRANSITIONS` there. Never checked/enforced by scattered
    `if status == ...` logic in routes."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """`WorkflowStep` lifecycle status — the step-level analog of
    `WorkflowStatus`. Transitions centralized in
    `app.services.workflow.WorkflowService` — see
    `_ALLOWED_STEP_TRANSITIONS`."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ActorType(StrEnum):
    """Who/what performed the action a `WorkflowEvent` records.

    `agent`/`tool` are modeled now so the schema doesn't need to change
    when a future story introduces them — but no agent or tool exists
    yet in this codebase (STORY-009 is persistence/lifecycle only).
    """

    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
    TOOL = "tool"


class WorkflowEventType(StrEnum):
    """The kind of thing a `WorkflowEvent` records. Deliberately closed —
    see docs/WORKFLOWS.md "Event Types".

    `TOOL_INVOKED` (STORY-010) is the one addition beyond STORY-009's
    original list — added specifically so the audit trail can show
    "a tool was dispatched as part of this step" as a distinct, visible
    moment, separate from the step merely starting. It carries only a
    safe tool name in `safe_metadata` (see
    `app.services.workflow.WorkflowService.record_tool_invocation`) —
    never arguments, never a result payload.
    """

    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_STARTED = "workflow_started"
    STEP_STARTED = "step_started"
    TOOL_INVOKED = "tool_invoked"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"
    WORKFLOW_WAITING = "workflow_waiting"
    WORKFLOW_RESUMED = "workflow_resumed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_CANCELLED = "workflow_cancelled"


class WorkflowRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One administrative request/execution, scoped to one `Organization`.

    Tenant/patient/initiator ownership integrity is enforced at the
    DATABASE level, the same composite-FK technique used throughout this
    codebase (see `app/models/patient_document.py`):
    `(organization_id, patient_id) -> patients(organization_id, id)`
    (only enforced when `patient_id` is set — PostgreSQL's default
    `MATCH SIMPLE` skips composite-FK validation entirely when any
    referenced column is `NULL`) and
    `(organization_id, initiated_by_user_id) ->
    organization_memberships(organization_id, user_id)` (existence of a
    membership only — NOT that it is currently active; see
    `app.services.workflow.WorkflowService` for the service-level
    activity check, the same existence-vs-activity split established for
    `PractitionerDepartment.is_active` and reused for
    `PatientDocument.uploaded_by_user_id`).

    `correlation_id` is a server-generated, globally opaque UUID hex
    string — never client-chosen, never derived from patient identity —
    used to trace one external request across `WorkflowRun` ->
    `WorkflowStep` -> `WorkflowEvent` without exposing patient
    information. `idempotency_key` is OPTIONAL and tenant-scoped
    (`UNIQUE(organization_id, idempotency_key)`, `NULL`-distinct) — a
    foundation for safe retries, not a full idempotency framework (no
    request-body hashing/replay-response-caching exists in this story).

    No raw patient request text is stored anywhere on this model — see
    docs/WORKFLOWS.md "Patient Request Content".
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_workflow_runs_organization_id_id"),
        UniqueConstraint("correlation_id", name="uq_workflow_runs_correlation_id"),
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_workflow_runs_org_idempotency_key"
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_workflow_runs_org_patient_patients",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "initiated_by_user_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_workflow_runs_org_initiator_memberships",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"failure_code IS NULL OR length(failure_code) <= {FAILURE_CODE_MAX_LENGTH}",
            name="failure_code_length",
        ),
        CheckConstraint(
            "failure_message_safe IS NULL OR "
            f"length(failure_message_safe) <= {FAILURE_MESSAGE_MAX_LENGTH}",
            name="failure_message_length",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # No plain `ForeignKey("patients.id")`: enforced solely via the
    # composite ownership FK above (mirrors `Department.facility_id` —
    # see app/models/department.py). No `relationship()` is built off
    # this column for the same reason.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    # No plain FK either — see the class docstring (mirrors
    # `PatientDocument.uploaded_by_user_id`).
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    request_type: Mapped[WorkflowRequestType] = mapped_column(
        SqlEnum(
            WorkflowRequestType,
            name="workflow_request_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    status: Mapped[WorkflowStatus] = mapped_column(
        SqlEnum(
            WorkflowStatus,
            name="workflow_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        default=WorkflowStatus.PENDING,
        index=True,
    )
    # The `sequence_number` (see `WorkflowStep`) of the currently
    # active/most-recently-started step — maintained by
    # `app.services.workflow.WorkflowService`, never computed ad hoc in a
    # route. `NULL` before any step has started.
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Server-generated opaque trace id — see the class docstring. Stored
    # as the hex digits of a UUID (no dashes) purely for a compact,
    # URL-safe representation; still effectively a UUID.
    correlation_id: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(IDEMPOTENCY_KEY_MAX_LENGTH), nullable=True
    )
    # Safe failure metadata ONLY — a short category code plus a bounded,
    # pre-sanitized human-readable summary. NEVER a raw exception repr(),
    # stack trace, SQL statement, or credential — see
    # docs/WORKFLOWS.md "Failure Data".
    failure_code: Mapped[str | None] = mapped_column(String(FAILURE_CODE_MAX_LENGTH), nullable=True)
    failure_message_safe: Mapped[str | None] = mapped_column(
        String(FAILURE_MESSAGE_MAX_LENGTH), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship()

    def __repr__(self) -> str:
        return (
            f"WorkflowRun(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"request_type={self.request_type!r}, status={self.status!r})"
        )


class WorkflowStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable execution unit within a `WorkflowRun`.

    `(organization_id, workflow_run_id) ->
    workflow_runs(organization_id, id)` is a composite FK guaranteeing a
    step can never be attached to a run in a different organization —
    raw SQL cannot bypass this. `UNIQUE(workflow_run_id,
    sequence_number)` guarantees steps within one run have a well-defined
    order with no duplicate position.

    `step_type` is a bounded, free-form string, NOT a closed enum — no
    agent/tool vocabulary exists yet (STORY-009 is persistence/lifecycle
    only), and speculatively enumerating one now would guess at a
    vocabulary a future agent-integration story should define instead.
    `agent_name`/`tool_name` are likewise bounded, safe LOGICAL
    identifiers only (e.g. `"appointment_agent"`, `"appointment.book"`)
    — never a prompt, a credential, or any other sensitive value. No
    structured input/output JSON is persisted on this model at all — see
    the module docstring and docs/WORKFLOWS.md "Step Metadata: A
    Deliberate Omission".
    """

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "workflow_run_id", "id", name="uq_workflow_steps_org_run_id"
        ),
        UniqueConstraint(
            "workflow_run_id", "sequence_number", name="uq_workflow_steps_run_sequence"
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_workflow_steps_org_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"failure_code IS NULL OR length(failure_code) <= {FAILURE_CODE_MAX_LENGTH}",
            name="failure_code_length",
        ),
        CheckConstraint(
            "failure_message_safe IS NULL OR "
            f"length(failure_message_safe) <= {FAILURE_MESSAGE_MAX_LENGTH}",
            name="failure_message_length",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("ix_workflow_steps_run_sequence", "workflow_run_id", "sequence_number"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # No plain FK: enforced solely via the composite ownership FK above
    # (same rationale as `WorkflowRun.patient_id`).
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        SqlEnum(
            StepStatus,
            name="workflow_step_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        default=StepStatus.PENDING,
        index=True,
    )
    agent_name: Mapped[str | None] = mapped_column(
        String(ACTOR_IDENTIFIER_MAX_LENGTH), nullable=True
    )
    tool_name: Mapped[str | None] = mapped_column(
        String(ACTOR_IDENTIFIER_MAX_LENGTH), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(FAILURE_CODE_MAX_LENGTH), nullable=True)
    failure_message_safe: Mapped[str | None] = mapped_column(
        String(FAILURE_MESSAGE_MAX_LENGTH), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"WorkflowStep(id={self.id!r}, workflow_run_id={self.workflow_run_id!r}, "
            f"sequence_number={self.sequence_number!r}, status={self.status!r})"
        )


class WorkflowEvent(UUIDPrimaryKeyMixin, Base):
    """An APPEND-ONLY audit/history event for a `WorkflowRun` (and,
    optionally, one specific `WorkflowStep` within it).

    Deliberately does NOT use `TimestampMixin`: an append-only record has
    no meaningful "last updated" — only `created_at` exists, and no
    application code ever updates a `WorkflowEvent` row after creation
    (`app.repositories.workflow_event` exposes `create`/read functions
    only — no `update`). This is an application-architecture guarantee,
    not a database trigger — see docs/WORKFLOWS.md "Event Immutability"
    for why a trigger was judged unnecessary infrastructure for this
    story's scope.

    `(organization_id, workflow_run_id) ->
    workflow_runs(organization_id, id)` and, when `workflow_step_id` is
    set, `(organization_id, workflow_run_id, workflow_step_id) ->
    workflow_steps(organization_id, workflow_run_id, id)` are both
    composite FKs — the second guarantees a linked step genuinely
    belongs to the SAME run and organization this event does, not merely
    to the same organization. Raw SQL cannot attach an Org A event to an
    Org B run, or link it to a step from a different run.

    `safe_metadata` is an OPTIONAL, size-bounded (`SAFE_METADATA_MAX_BYTES`)
    JSON object — bounded structured detail only (e.g.
    `{"appointment_id": "..."}`), never a prompt, a full tool payload
    dump, a credential, or PII/PHI beyond what the event inherently
    already references via its FKs. See docs/WORKFLOWS.md "Safe
    Metadata".
    """

    __tablename__ = "workflow_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_workflow_events_org_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id", "workflow_step_id"],
            [
                "workflow_steps.organization_id",
                "workflow_steps.workflow_run_id",
                "workflow_steps.id",
            ],
            name="fk_workflow_events_org_run_step_workflow_steps",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "actor_identifier IS NOT NULL AND "
            f"length(actor_identifier) <= {ACTOR_IDENTIFIER_MAX_LENGTH}",
            name="actor_identifier_length",
        ),
        CheckConstraint(
            "safe_metadata IS NULL OR "
            f"octet_length(safe_metadata::text) <= {SAFE_METADATA_MAX_BYTES}",
            name="safe_metadata_size",
        ),
        Index("ix_workflow_events_run_created_at", "workflow_run_id", "created_at"),
        Index("ix_workflow_events_run_sequence", "workflow_run_id", "sequence"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    event_type: Mapped[WorkflowEventType] = mapped_column(
        SqlEnum(
            WorkflowEventType,
            name="workflow_event_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    actor_type: Mapped[ActorType] = mapped_column(
        SqlEnum(
            ActorType,
            name="workflow_actor_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    actor_identifier: Mapped[str] = mapped_column(
        String(ACTOR_IDENTIFIER_MAX_LENGTH), nullable=False
    )
    safe_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # Server-generated, strictly monotonically increasing (PostgreSQL
    # `GENERATED BY DEFAULT AS IDENTITY`) — the AUTHORITATIVE ordering key
    # for `list_by_run`, not `created_at`. Added in STORY-010 after a
    # genuine flaky-ordering bug surfaced: two events created in rapid
    # succession (exactly what tool-call event sequences do) can share
    # the same `created_at` value at Python `datetime.now()` resolution,
    # and the id (a random UUID) is not a valid tiebreaker. A
    # database-assigned identity column is immune to both problems —
    # concurrent inserts can never receive the same value, and its order
    # always matches insertion order.
    sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False, unique=True
    )

    def __repr__(self) -> str:
        return (
            f"WorkflowEvent(id={self.id!r}, workflow_run_id={self.workflow_run_id!r}, "
            f"event_type={self.event_type!r})"
        )
