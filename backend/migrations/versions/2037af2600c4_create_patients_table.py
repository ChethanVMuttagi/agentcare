"""create patients table

Revision ID: 2037af2600c4
Revises: 445bcf7d22b9
Create Date: 2026-07-26 02:04:23.372024

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2037af2600c4"
down_revision: str | Sequence[str] | None = "445bcf7d22b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "patients",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("patient_number", sa.String(length=64), nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_patients_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_patients_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
        sa.UniqueConstraint(
            "organization_id",
            "patient_number",
            name="uq_patients_organization_id_patient_number",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_patients_organization_id_user_id",
        ),
    )
    op.create_index(
        op.f("ix_patients_organization_id"), "patients", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_patients_user_id"), "patients", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_patients_user_id"), table_name="patients")
    op.drop_index(op.f("ix_patients_organization_id"), table_name="patients")
    op.drop_table("patients")
