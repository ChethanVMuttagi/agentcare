"""create workflow_runs steps and events tables

Revision ID: 291a26a9ce1b
Revises: f4702bbc0be1
Create Date: 2026-07-27 17:32:46.349350

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "291a26a9ce1b"
down_revision: str | Sequence[str] | None = "f4702bbc0be1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workflow_runs",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("initiated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "request_type",
            sa.Enum(
                "appointment_booking",
                "appointment_rescheduling",
                "appointment_cancellation",
                "document_collection",
                "administrative_routing",
                "follow_up",
                name="workflow_request_type",
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
                "running",
                "waiting",
                "completed",
                "failed",
                "cancelled",
                name="workflow_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("current_step", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message_safe", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failure_code IS NULL OR length(failure_code) <= 64",
            name=op.f("ck_workflow_runs_failure_code_length"),
        ),
        sa.CheckConstraint(
            "failure_message_safe IS NULL OR length(failure_message_safe) <= 500",
            name=op.f("ck_workflow_runs_failure_message_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "initiated_by_user_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_workflow_runs_org_initiator_memberships",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_workflow_runs_org_patient_patients",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workflow_runs_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_runs")),
        sa.UniqueConstraint("correlation_id", name="uq_workflow_runs_correlation_id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_workflow_runs_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_workflow_runs_org_idempotency_key"
        ),
    )
    op.create_index(
        op.f("ix_workflow_runs_initiated_by_user_id"), "workflow_runs", ["initiated_by_user_id"]
    )
    op.create_index(
        op.f("ix_workflow_runs_organization_id"), "workflow_runs", ["organization_id"]
    )
    op.create_index(op.f("ix_workflow_runs_patient_id"), "workflow_runs", ["patient_id"])
    op.create_index(op.f("ix_workflow_runs_status"), "workflow_runs", ["status"])

    op.create_table(
        "workflow_steps",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "waiting",
                "completed",
                "failed",
                "skipped",
                name="workflow_step_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message_safe", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_workflow_steps_attempt_count_non_negative")
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR length(failure_code) <= 64",
            name=op.f("ck_workflow_steps_failure_code_length"),
        ),
        sa.CheckConstraint(
            "failure_message_safe IS NULL OR length(failure_message_safe) <= 500",
            name=op.f("ck_workflow_steps_failure_message_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_workflow_steps_org_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workflow_steps_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_steps")),
        sa.UniqueConstraint(
            "organization_id", "workflow_run_id", "id", name="uq_workflow_steps_org_run_id"
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "sequence_number", name="uq_workflow_steps_run_sequence"
        ),
    )
    op.create_index(
        op.f("ix_workflow_steps_organization_id"), "workflow_steps", ["organization_id"]
    )
    op.create_index(
        "ix_workflow_steps_run_sequence", "workflow_steps", ["workflow_run_id", "sequence_number"]
    )
    op.create_index(op.f("ix_workflow_steps_status"), "workflow_steps", ["status"])
    op.create_index(
        op.f("ix_workflow_steps_workflow_run_id"), "workflow_steps", ["workflow_run_id"]
    )

    op.create_table(
        "workflow_events",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_step_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "workflow_created",
                "workflow_started",
                "step_started",
                "step_completed",
                "step_failed",
                "step_skipped",
                "workflow_waiting",
                "workflow_resumed",
                "workflow_completed",
                "workflow_failed",
                "workflow_cancelled",
                name="workflow_event_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_type",
            sa.Enum(
                "user",
                "system",
                "agent",
                "tool",
                name="workflow_actor_type",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("actor_identifier", sa.String(length=100), nullable=False),
        sa.Column("safe_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "actor_identifier IS NOT NULL AND length(actor_identifier) <= 100",
            name=op.f("ck_workflow_events_actor_identifier_length"),
        ),
        sa.CheckConstraint(
            "safe_metadata IS NULL OR octet_length(safe_metadata::text) <= 2000",
            name=op.f("ck_workflow_events_safe_metadata_size"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id", "workflow_step_id"],
            [
                "workflow_steps.organization_id",
                "workflow_steps.workflow_run_id",
                "workflow_steps.id",
            ],
            name="fk_workflow_events_org_run_step_workflow_steps",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_runs.organization_id", "workflow_runs.id"],
            name="fk_workflow_events_org_run_workflow_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workflow_events_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_events")),
    )
    op.create_index(
        op.f("ix_workflow_events_event_type"), "workflow_events", ["event_type"]
    )
    op.create_index(
        op.f("ix_workflow_events_organization_id"), "workflow_events", ["organization_id"]
    )
    op.create_index(
        "ix_workflow_events_run_created_at", "workflow_events", ["workflow_run_id", "created_at"]
    )
    op.create_index(
        op.f("ix_workflow_events_workflow_run_id"), "workflow_events", ["workflow_run_id"]
    )
    op.create_index(
        op.f("ix_workflow_events_workflow_step_id"), "workflow_events", ["workflow_step_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_workflow_events_workflow_step_id"), table_name="workflow_events")
    op.drop_index(op.f("ix_workflow_events_workflow_run_id"), table_name="workflow_events")
    op.drop_index("ix_workflow_events_run_created_at", table_name="workflow_events")
    op.drop_index(op.f("ix_workflow_events_organization_id"), table_name="workflow_events")
    op.drop_index(op.f("ix_workflow_events_event_type"), table_name="workflow_events")
    op.drop_table("workflow_events")

    op.drop_index(op.f("ix_workflow_steps_workflow_run_id"), table_name="workflow_steps")
    op.drop_index(op.f("ix_workflow_steps_status"), table_name="workflow_steps")
    op.drop_index("ix_workflow_steps_run_sequence", table_name="workflow_steps")
    op.drop_index(op.f("ix_workflow_steps_organization_id"), table_name="workflow_steps")
    op.drop_table("workflow_steps")

    op.drop_index(op.f("ix_workflow_runs_status"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_patient_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_organization_id"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_initiated_by_user_id"), table_name="workflow_runs")
    op.drop_table("workflow_runs")
