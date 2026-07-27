"""create department, practitioner, and availability tables

Revision ID: 6251a20d9632
Revises: 2037af2600c4
Create Date: 2026-07-26 20:19:40.182423

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6251a20d9632"
down_revision: str | Sequence[str] | None = "2037af2600c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Must run BEFORE `departments` is created: its composite FK
    # `(organization_id, facility_id) -> facilities(organization_id, id)`
    # requires this unique constraint to already exist on `facilities`.
    op.create_unique_constraint(
        "uq_facilities_organization_id_id", "facilities", ["organization_id", "id"]
    )

    op.create_table(
        "practitioners",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=False),
        sa.Column(
            "practitioner_type",
            sa.Enum(
                "physician",
                "physiotherapist",
                "counselor",
                "therapist",
                "other",
                name="practitioner_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_practitioners_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_practitioners")),
        sa.UniqueConstraint("organization_id", "id", name="uq_practitioners_organization_id_id"),
    )
    op.create_index(
        op.f("ix_practitioners_organization_id"), "practitioners", ["organization_id"]
    )

    op.create_table(
        "departments",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "facility_id"],
            ["facilities.organization_id", "facilities.id"],
            name="fk_departments_organization_id_facility_id_facilities",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_departments_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("facility_id", "code", name="uq_departments_facility_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_departments_organization_id_id"),
    )
    op.create_index(op.f("ix_departments_facility_id"), "departments", ["facility_id"])
    op.create_index(op.f("ix_departments_organization_id"), "departments", ["organization_id"])

    op.create_table(
        "practitioner_departments",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("practitioner_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            name="fk_practitioner_departments_org_department_departments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "practitioner_id"],
            ["practitioners.organization_id", "practitioners.id"],
            name="fk_practitioner_departments_org_practitioner_practitioners",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_practitioner_departments_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_practitioner_departments")),
        sa.UniqueConstraint(
            "organization_id",
            "practitioner_id",
            "department_id",
            name="uq_practitioner_departments_org_practitioner_department",
        ),
    )
    op.create_index(
        op.f("ix_practitioner_departments_department_id"),
        "practitioner_departments",
        ["department_id"],
    )
    op.create_index(
        op.f("ix_practitioner_departments_organization_id"),
        "practitioner_departments",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_practitioner_departments_practitioner_id"),
        "practitioner_departments",
        ["practitioner_id"],
    )

    op.create_table(
        "practitioner_availability",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("practitioner_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column(
            "day_of_week",
            sa.Enum(
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                name="day_of_week",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "start_time < end_time",
            name=op.f("ck_practitioner_availability_start_before_end"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_practitioner_availability_department_id_departments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "practitioner_id", "department_id"],
            [
                "practitioner_departments.organization_id",
                "practitioner_departments.practitioner_id",
                "practitioner_departments.department_id",
            ],
            name="fk_practitioner_availability_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_practitioner_availability_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["practitioner_id"],
            ["practitioners.id"],
            name=op.f("fk_practitioner_availability_practitioner_id_practitioners"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_practitioner_availability")),
    )
    op.create_index(
        op.f("ix_practitioner_availability_day_of_week"),
        "practitioner_availability",
        ["day_of_week"],
    )
    op.create_index(
        op.f("ix_practitioner_availability_department_id"),
        "practitioner_availability",
        ["department_id"],
    )
    op.create_index(
        op.f("ix_practitioner_availability_organization_id"),
        "practitioner_availability",
        ["organization_id"],
    )
    op.create_index(
        "ix_practitioner_availability_practitioner_department_day",
        "practitioner_availability",
        ["practitioner_id", "department_id", "day_of_week"],
    )
    op.create_index(
        op.f("ix_practitioner_availability_practitioner_id"),
        "practitioner_availability",
        ["practitioner_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_practitioner_availability_practitioner_id"),
        table_name="practitioner_availability",
    )
    op.drop_index(
        "ix_practitioner_availability_practitioner_department_day",
        table_name="practitioner_availability",
    )
    op.drop_index(
        op.f("ix_practitioner_availability_organization_id"),
        table_name="practitioner_availability",
    )
    op.drop_index(
        op.f("ix_practitioner_availability_department_id"),
        table_name="practitioner_availability",
    )
    op.drop_index(
        op.f("ix_practitioner_availability_day_of_week"),
        table_name="practitioner_availability",
    )
    op.drop_table("practitioner_availability")

    op.drop_index(
        op.f("ix_practitioner_departments_practitioner_id"),
        table_name="practitioner_departments",
    )
    op.drop_index(
        op.f("ix_practitioner_departments_organization_id"),
        table_name="practitioner_departments",
    )
    op.drop_index(
        op.f("ix_practitioner_departments_department_id"),
        table_name="practitioner_departments",
    )
    op.drop_table("practitioner_departments")

    op.drop_index(op.f("ix_departments_organization_id"), table_name="departments")
    op.drop_index(op.f("ix_departments_facility_id"), table_name="departments")
    op.drop_table("departments")

    op.drop_index(op.f("ix_practitioners_organization_id"), table_name="practitioners")
    op.drop_table("practitioners")

    # Must run AFTER `departments` is dropped: its composite FK depended
    # on this unique constraint existing on `facilities`.
    op.drop_constraint("uq_facilities_organization_id_id", "facilities", type_="unique")
