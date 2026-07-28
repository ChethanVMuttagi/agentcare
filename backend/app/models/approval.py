"""ApprovalRequest: durable human-in-the-loop approval gate (STORY-014).

An `ApprovalRequest` is never a standalone audit record — it always
belongs to an EXISTING `WorkflowRun`/`WorkflowStep` (composite FKs below),
the same "no parallel audit trail" discipline
`app.models.reminder.Reminder` already established for the reminder
engine (STORY-013). Creating one is always paired with pausing that run
and step (`WorkflowStatus`/`StepStatus` -> `WAITING`) — see
`app.services.approval.ApprovalService` — so a `PENDING` approval and a
paused workflow are always the same fact, never two independently
maintained ones.

`approved_by_user` doubles as the RESOLVING user for either terminal
outcome (approve OR reject) — there is deliberately no separate
`rejected_by_user` column. A rejection is still a human decision made by
one accountable user; splitting that into two mutually-exclusive nullable
actor columns would only complicate the state-consistency CHECK
constraint below for zero additional guarantee. See
docs/adr/ADR-0013-human-in-the-loop-approvals.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.organization import Organization

# Bounds applied via database CHECK constraints — see `__table_args__`.
# Kept here so the model, its constraints, and service-level validation
# all reference the same literal values, mirroring
# `app/models/reminder.py`'s identical pattern.
REASON_MAX_LENGTH = 500
REQUESTED_BY_AGENT_MAX_LENGTH = 100


class ApprovalType(StrEnum):
    """The kind of decision an `ApprovalRequest` gates. Deliberately
    small and closed — see docs/DOMAIN_MODEL.md "Enum Strategy" for the
    same controlled-evolution rationale as every other enum in this
    codebase."""

    APPOINTMENT_OVERRIDE = "appointment_override"
    MANUAL_RESCHEDULE = "manual_reschedule"
    DOCUMENT_EXCEPTION = "document_exception"
    HIGH_RISK_ACTION = "high_risk_action"
    CUSTOM = "custom"


class ApprovalStatus(StrEnum):
    """Controlled approval lifecycle status. Transitions are centralized
    in `app.services.approval.ApprovalService` — never checked/enforced
    by scattered `if status == ...` logic elsewhere, the same discipline
    `app.services.workflow.WorkflowService`/`app.services.reminder.ReminderService`
    already established.

    - `PENDING`: awaiting a human decision, not yet past `expires_at`.
    - `APPROVED`: a human granted the request; terminal.
    - `REJECTED`: a human denied the request; terminal.
    - `EXPIRED`: no human decision arrived before `expires_at`; terminal.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durably persisted human-in-the-loop approval gate, scoped to
    one `Organization`.

    Tenant/ownership integrity is enforced at the DATABASE level, the
    same composite-FK technique used throughout this codebase:
    `(organization_id, workflow_run_id) -> workflow_runs(organization_id, id)`
    and `(organization_id, workflow_run_id, workflow_step_id) ->
    workflow_steps(organization_id, workflow_run_id, id)` together
    guarantee the paused step genuinely belongs to the SAME run and
    organization this approval does — mirrors
    `app.models.workflow.WorkflowEvent`'s identical pair. An OPTIONAL
    `(organization_id, approved_by_user) ->
    organization_memberships(organization_id, user_id)` FK guarantees
    the resolving user, when one exists, is a real member of this
    organization; `NULL` (a still-`PENDING` or `EXPIRED` approval) skips
    composite-FK validation entirely, the same `MATCH SIMPLE` behavior
    `WorkflowRun.patient_id` already relies on.

    The `approval_status_actor_consistency` CHECK constraint is the
    single source of truth for which columns must/must not be set for
    each `ApprovalStatus` — never re-derived ad hoc in application code.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "id", name="uq_approval_requests_organization_id_id"
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_approval_requests_org_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id", "workflow_step_id"],
            [
                "workflow_steps.organization_id",
                "workflow_steps.workflow_run_id",
                "workflow_steps.id",
            ],
            name="fk_approval_requests_org_run_step_workflow_steps",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approved_by_user"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_approval_requests_org_resolver_memberships",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"length(reason) > 0 AND length(reason) <= {REASON_MAX_LENGTH}",
            name="reason_length",
        ),
        CheckConstraint(
            "length(requested_by_agent) > 0 AND "
            f"length(requested_by_agent) <= {REQUESTED_BY_AGENT_MAX_LENGTH}",
            name="requested_by_agent_length",
        ),
        CheckConstraint(
            "(status = 'pending' AND approved_by_user IS NULL "
            " AND approved_at IS NULL AND rejected_at IS NULL) OR "
            "(status = 'approved' AND approved_by_user IS NOT NULL "
            " AND approved_at IS NOT NULL AND rejected_at IS NULL) OR "
            "(status = 'rejected' AND approved_by_user IS NOT NULL "
            " AND rejected_at IS NOT NULL AND approved_at IS NULL) OR "
            "(status = 'expired' AND approved_by_user IS NULL "
            " AND approved_at IS NULL AND rejected_at IS NULL)",
            name="approval_status_actor_consistency",
        ),
        Index("ix_approval_requests_org_status", "organization_id", "status"),
        Index(
            "ix_approval_requests_workflow_run_id_workflow_step_id",
            "workflow_run_id",
            "workflow_step_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # No plain FK for `workflow_run_id`/`workflow_step_id`/`approved_by_user`:
    # enforced solely via the composite ownership FKs above (mirrors
    # `Reminder.workflow_run_id` — see app/models/reminder.py). No
    # `relationship()` is built off any of these for the same reason.
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    approval_type: Mapped[ApprovalType] = mapped_column(
        SqlEnum(
            ApprovalType,
            name="approval_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        SqlEnum(
            ApprovalStatus,
            name="approval_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
        index=True,
    )
    # Why this approval was requested — bounded, developer/agent-composed
    # text (e.g. "Discount exceeds the automatic approval threshold."),
    # NEVER a raw prompt, chain-of-thought, or free-form patient text.
    reason: Mapped[str] = mapped_column(String(REASON_MAX_LENGTH), nullable=False)
    # The stable, logical agent name that requested this approval (e.g.
    # `"coordinator"`) — mirrors `WorkflowStep.agent_name` — or
    # `"manual"` for an approval created directly via the API by a human
    # caller rather than the Coordinator agent (see
    # `app.services.approval.ApprovalService.create_approval_request`).
    requested_by_agent: Mapped[str] = mapped_column(
        String(REQUESTED_BY_AGENT_MAX_LENGTH), nullable=False
    )
    # The resolving user for EITHER terminal outcome — see the class
    # docstring for why there is no separate `rejected_by_user` column.
    approved_by_user: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NOT NULL: every `ApprovalRequest` this codebase ever creates is
    # created BY `ApprovalService.create_approval_request`, which always
    # computes a deadline — an approval that can never expire is not a
    # case this story supports. See docs/adr/ADR-0013-human-in-the-loop-approvals.md.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    organization: Mapped[Organization] = relationship()

    def __repr__(self) -> str:
        return (
            f"ApprovalRequest(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"status={self.status!r}, approval_type={self.approval_type!r})"
        )
