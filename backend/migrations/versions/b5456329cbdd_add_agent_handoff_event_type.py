"""add agent handoff event type

Revision ID: b5456329cbdd
Revises: 5354c755424b
Create Date: 2026-07-28 10:44:42.770861

Extends the `workflow_event_type` CHECK constraint on `workflow_events`
with one new value, `agent_handoff` — see `app/models/workflow.py`'s
`WorkflowEventType` docstring for why (STORY-011's Coordinator ->
specialist handoff audit trail). Mirrors `5354c755424b`'s exact
technique for the same reason: bare (short) constraint names below —
NOT wrapped in `op.f()` — so Alembic's naming-convention-aware DDL
(`ck_%(table_name)s_%(constraint_name)s`, `migrations/env.py`) templates
them to the SAME final name the model's
`CheckConstraint(name="workflow_event_type")` already produces:
`ck_workflow_events_workflow_event_type`. No column change is needed
this time — only the constraint's allowed value set.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5456329cbdd"
down_revision: str | Sequence[str] | None = "5354c755424b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_EVENT_TYPES = (
    "workflow_created",
    "workflow_started",
    "step_started",
    "tool_invoked",
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
    "step_completed",
    "step_failed",
    "step_skipped",
    "workflow_waiting",
    "workflow_resumed",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
)


def _check_sql(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"event_type IN ({allowed})"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("workflow_event_type", "workflow_events", type_="check")
    op.create_check_constraint(
        "workflow_event_type",
        "workflow_events",
        _check_sql(_NEW_EVENT_TYPES),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("workflow_event_type", "workflow_events", type_="check")
    op.create_check_constraint(
        "workflow_event_type",
        "workflow_events",
        _check_sql(_OLD_EVENT_TYPES),
    )
