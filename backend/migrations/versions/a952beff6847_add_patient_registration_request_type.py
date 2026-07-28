"""add patient_registration request type

Revision ID: a952beff6847
Revises: 3325693c0fa5
Create Date: 2026-07-28 16:30:00.000000

STORY-015 (End-to-End AI Administrative Workflow Engine). Extends
`workflow_request_type` (on `workflow_runs`) with `patient_registration`
— the new Patient Registration workflow template's request type (see
`app.services.patient_registration.PatientRegistrationService` and
docs/adr/ADR-0014-end-to-end-administrative-workflows.md). Mirrors the
exact bare-name-templating technique
`5354c755424b`/`b5456329cbdd`/`242a55f46528`/`3325693c0fa5` already
established. No table changes — the Patient Registration workflow reuses
`workflow_runs`/`workflow_steps`/`workflow_events` exactly like every
other workflow kind.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a952beff6847"
down_revision: str | Sequence[str] | None = "3325693c0fa5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_REQUEST_TYPES = (
    "appointment_booking",
    "appointment_rescheduling",
    "appointment_cancellation",
    "document_collection",
    "administrative_routing",
    "follow_up",
    "reminder_delivery",
)
_NEW_REQUEST_TYPES = (*_OLD_REQUEST_TYPES, "patient_registration")


def _check_sql(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("workflow_request_type", "workflow_runs", type_="check")
    op.create_check_constraint(
        "workflow_request_type",
        "workflow_runs",
        _check_sql("request_type", _NEW_REQUEST_TYPES),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("workflow_request_type", "workflow_runs", type_="check")
    op.create_check_constraint(
        "workflow_request_type",
        "workflow_runs",
        _check_sql("request_type", _OLD_REQUEST_TYPES),
    )
