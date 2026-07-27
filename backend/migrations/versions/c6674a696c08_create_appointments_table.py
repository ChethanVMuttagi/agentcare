"""create appointments table

Revision ID: c6674a696c08
Revises: 6251a20d9632
Create Date: 2026-07-27 14:14:07.310811

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c6674a696c08"
down_revision: str | Sequence[str] | None = "6251a20d9632"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Required for the GiST-backed EXCLUDE constraints below: PostgreSQL's
    # GiST index access method has no native "=" operator support for
    # plain scalar types (e.g. uuid) out of the box — `btree_gist` adds
    # btree-equivalent operator classes usable inside a GiST index, which
    # is what lets us combine "same practitioner_id" (equality) with
    # "overlapping time range" (range overlap, `&&`) in a single index.
    # See docs/adr/ADR-0007-appointment-concurrency.md.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # Must run BEFORE `appointments` is created: its composite FK
    # `(organization_id, patient_id) -> patients(organization_id, id)`
    # requires this unique constraint to already exist on `patients` —
    # same ordering requirement as `uq_facilities_organization_id_id` in
    # migration 6251a20d9632.
    op.create_unique_constraint(
        "uq_patients_organization_id_id", "patients", ["organization_id", "id"]
    )

    op.create_table(
        "appointments",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("practitioner_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "booked",
                "cancelled",
                "completed",
                name="appointment_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("start_at < end_at", name=op.f("ck_appointments_start_before_end")),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_appointments_department_id_departments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_appointments_org_patient_patients",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "practitioner_id", "department_id"],
            [
                "practitioner_departments.organization_id",
                "practitioner_departments.practitioner_id",
                "practitioner_departments.department_id",
            ],
            name="fk_appointments_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_appointments_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_appointments_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["practitioner_id"],
            ["practitioners.id"],
            name=op.f("fk_appointments_practitioner_id_practitioners"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
        # Race-safe practitioner double-booking prevention: no two BOOKED
        # appointments for the same practitioner may have overlapping
        # `[start_at, end_at)` ranges. `WHERE status = 'booked'` means
        # cancelled/completed appointments never participate — see
        # docs/APPOINTMENTS.md "Collision Prevention".
        postgresql.ExcludeConstraint(
            ("practitioner_id", "="),
            (sa.text("tstzrange(start_at, end_at, '[)')"), "&&"),
            where=sa.text("status = 'booked'"),
            using="gist",
            name="ex_appointments_practitioner_no_overlap",
        ),
        # Same guarantee, for the patient — see docs/APPOINTMENTS.md
        # "Patient Double-Booking" for the policy decision to enforce this
        # too, not just for practitioners.
        postgresql.ExcludeConstraint(
            ("patient_id", "="),
            (sa.text("tstzrange(start_at, end_at, '[)')"), "&&"),
            where=sa.text("status = 'booked'"),
            using="gist",
            name="ex_appointments_patient_no_overlap",
        ),
    )
    op.create_index(op.f("ix_appointments_department_id"), "appointments", ["department_id"])
    op.create_index(op.f("ix_appointments_organization_id"), "appointments", ["organization_id"])
    op.create_index(op.f("ix_appointments_patient_id"), "appointments", ["patient_id"])
    op.create_index("ix_appointments_patient_start_at", "appointments", ["patient_id", "start_at"])
    op.create_index(op.f("ix_appointments_practitioner_id"), "appointments", ["practitioner_id"])
    op.create_index(
        "ix_appointments_practitioner_start_at",
        "appointments",
        ["practitioner_id", "start_at"],
    )
    op.create_index(op.f("ix_appointments_status"), "appointments", ["status"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_appointments_status"), table_name="appointments")
    op.drop_index("ix_appointments_practitioner_start_at", table_name="appointments")
    op.drop_index(op.f("ix_appointments_practitioner_id"), table_name="appointments")
    op.drop_index("ix_appointments_patient_start_at", table_name="appointments")
    op.drop_index(op.f("ix_appointments_patient_id"), table_name="appointments")
    op.drop_index(op.f("ix_appointments_organization_id"), table_name="appointments")
    op.drop_index(op.f("ix_appointments_department_id"), table_name="appointments")
    op.drop_table("appointments")

    # Must run AFTER `appointments` is dropped: its composite FK depended
    # on this unique constraint existing on `patients`.
    op.drop_constraint("uq_patients_organization_id_id", "patients", type_="unique")

    # AgentCare's migrations are the only owner of this extension in this
    # database (nothing else in this schema uses GiST/btree_gist) — safe
    # to drop on downgrade. If a future story introduces another
    # GiST-dependent feature, this line must be reconsidered rather than
    # left to silently drop an extension something else now depends on.
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
