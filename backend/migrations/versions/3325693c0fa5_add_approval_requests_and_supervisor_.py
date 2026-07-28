"""add approval_requests and supervisor role

Revision ID: 3325693c0fa5
Revises: 242a55f46528
Create Date: 2026-07-28 15:40:00.000000

STORY-014 (Human-in-the-Loop, Approval Engine & Workflow Pause/Resume).
Three changes, all in this one migration:

1. Creates `approval_requests` — the durable human-in-the-loop approval
   gate (`app.models.approval.ApprovalRequest`), referencing an EXISTING
   `workflow_runs`/`workflow_steps` row via composite FKs (an approval is
   never a parallel audit trail — see
   `app.services.approval.ApprovalService`) and, optionally,
   `organization_memberships` for the resolving user.
2. Extends `membership_role` (on `organization_memberships`) with
   `supervisor` — a new `Role` authorized, alongside `admin`, to
   approve/reject a paused approval (see `app.models.membership.Role`).
3. Extends `workflow_event_type` (on `workflow_events`) with five new
   values: `step_waiting`/`step_resumed` (the step-level analogs of the
   existing `workflow_waiting`/`workflow_resumed`) and
   `approval_requested`/`approval_granted`/`approval_rejected` — see
   `app/models/workflow.py`'s `WorkflowEventType` docstring.

Both CHECK constraint extensions mirror the exact bare-name-templating
technique `5354c755424b`/`b5456329cbdd`/`242a55f46528` already
established.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3325693c0fa5"
down_revision: str | Sequence[str] | None = "242a55f46528"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ROLES = ("admin", "staff", "patient")
_NEW_ROLES = (*_OLD_ROLES, "supervisor")

_OLD_EVENT_TYPES = (
    "workflow_created",
    "workflow_started",
    "step_started",
    "tool_invoked",
    "agent_handoff",
    "reminder_scheduled",
    "reminder_started",
    "reminder_sent",
    "reminder_failed",
    "reminder_cancelled",
    "step_completed",
    "step_failed",
    "step_skipped",
    "workflow_waiting",
    "workflow_resumed",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
)
_NEW_EVENT_TYPES = (
    "workflow_created",
    "workflow_started",
    "step_started",
    "tool_invoked",
    "agent_handoff",
    "reminder_scheduled",
    "reminder_started",
    "reminder_sent",
    "reminder_failed",
    "reminder_cancelled",
    "step_completed",
    "step_failed",
    "step_skipped",
    "step_waiting",
    "step_resumed",
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "workflow_waiting",
    "workflow_resumed",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
)


def _check_sql(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "approval_requests",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_step_id", sa.Uuid(), nullable=False),
        sa.Column(
            "approval_type",
            sa.Enum(
                "appointment_override",
                "manual_reschedule",
                "document_exception",
                "high_risk_action",
                "custom",
                name="approval_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "expired",
                name="approval_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("requested_by_agent", sa.String(length=100), nullable=False),
        sa.Column("approved_by_user", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'pending' AND approved_by_user IS NULL "
            " AND approved_at IS NULL AND rejected_at IS NULL) OR "
            "(status = 'approved' AND approved_by_user IS NOT NULL "
            " AND approved_at IS NOT NULL AND rejected_at IS NULL) OR "
            "(status = 'rejected' AND approved_by_user IS NOT NULL "
            " AND rejected_at IS NOT NULL AND approved_at IS NULL) OR "
            "(status = 'expired' AND approved_by_user IS NULL "
            " AND approved_at IS NULL AND rejected_at IS NULL)",
            name=op.f("ck_approval_requests_approval_status_actor_consistency"),
        ),
        sa.CheckConstraint(
            "length(reason) > 0 AND length(reason) <= 500",
            name=op.f("ck_approval_requests_reason_length"),
        ),
        sa.CheckConstraint(
            "length(requested_by_agent) > 0 AND length(requested_by_agent) <= 100",
            name=op.f("ck_approval_requests_requested_by_agent_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approved_by_user"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_approval_requests_org_resolver_memberships",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id", "workflow_step_id"],
            [
                "workflow_steps.organization_id",
                "workflow_steps.workflow_run_id",
                "workflow_steps.id",
            ],
            name="fk_approval_requests_org_run_step_workflow_steps",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_approval_requests_org_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_approval_requests_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_approval_requests_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_approval_requests_approved_by_user"),
        "approval_requests",
        ["approved_by_user"],
        unique=False,
    )
    op.create_index(
        op.f("ix_approval_requests_organization_id"),
        "approval_requests",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_org_status",
        "approval_requests",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_approval_requests_status"), "approval_requests", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_approval_requests_workflow_run_id"),
        "approval_requests",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_approval_requests_workflow_step_id"),
        "approval_requests",
        ["workflow_step_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_workflow_run_id_workflow_step_id",
        "approval_requests",
        ["workflow_run_id", "workflow_step_id"],
        unique=False,
    )

    # ### CHECK constraint extensions (bare/short names — see the module docstring) ###
    op.drop_constraint("membership_role", "organization_memberships", type_="check")
    op.create_check_constraint(
        "membership_role",
        "organization_memberships",
        _check_sql("role", _NEW_ROLES),
    )
    op.drop_constraint("workflow_event_type", "workflow_events", type_="check")
    op.create_check_constraint(
        "workflow_event_type",
        "workflow_events",
        _check_sql("event_type", _NEW_EVENT_TYPES),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("workflow_event_type", "workflow_events", type_="check")
    op.create_check_constraint(
        "workflow_event_type",
        "workflow_events",
        _check_sql("event_type", _OLD_EVENT_TYPES),
    )
    op.drop_constraint("membership_role", "organization_memberships", type_="check")
    op.create_check_constraint(
        "membership_role",
        "organization_memberships",
        _check_sql("role", _OLD_ROLES),
    )

    op.drop_index(
        "ix_approval_requests_workflow_run_id_workflow_step_id", table_name="approval_requests"
    )
    op.drop_index(op.f("ix_approval_requests_workflow_step_id"), table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_workflow_run_id"), table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_status"), table_name="approval_requests")
    op.drop_index("ix_approval_requests_org_status", table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_organization_id"), table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_approved_by_user"), table_name="approval_requests")
    op.drop_table("approval_requests")
