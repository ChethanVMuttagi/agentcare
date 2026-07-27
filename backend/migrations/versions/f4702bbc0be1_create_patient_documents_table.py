"""create patient_documents table

Revision ID: f4702bbc0be1
Revises: c6674a696c08
Create Date: 2026-07-27 16:30:53.595850

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4702bbc0be1"
down_revision: str | Sequence[str] | None = "c6674a696c08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "patient_documents",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "document_type",
            sa.Enum(
                "identity",
                "insurance",
                "referral",
                "consent",
                "other",
                name="document_type",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "available",
                "failed",
                "deleted",
                name="document_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column(
            "media_type",
            sa.Enum(
                "application/pdf",
                "image/jpeg",
                "image/png",
                name="document_media_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status <> 'available') OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name=op.f("ck_patient_documents_available_has_size_and_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_patient_documents_org_patient_patients",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "uploaded_by_user_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_patient_documents_org_uploader_memberships",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_patient_documents_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_patient_documents_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_documents")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_patient_documents_storage_key")),
    )
    op.create_index(
        op.f("ix_patient_documents_organization_id"), "patient_documents", ["organization_id"]
    )
    op.create_index(
        "ix_patient_documents_patient_created_at",
        "patient_documents",
        ["patient_id", "created_at"],
    )
    op.create_index(op.f("ix_patient_documents_patient_id"), "patient_documents", ["patient_id"])
    op.create_index(op.f("ix_patient_documents_status"), "patient_documents", ["status"])
    op.create_index(
        op.f("ix_patient_documents_uploaded_by_user_id"),
        "patient_documents",
        ["uploaded_by_user_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_patient_documents_uploaded_by_user_id"), table_name="patient_documents"
    )
    op.drop_index(op.f("ix_patient_documents_status"), table_name="patient_documents")
    op.drop_index(op.f("ix_patient_documents_patient_id"), table_name="patient_documents")
    op.drop_index("ix_patient_documents_patient_created_at", table_name="patient_documents")
    op.drop_index(op.f("ix_patient_documents_organization_id"), table_name="patient_documents")
    op.drop_table("patient_documents")
