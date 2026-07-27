"""PatientDocument: administrative document metadata + storage reference.

This is an ADMINISTRATIVE record only — see docs/DOCUMENTS.md and the
healthcare safety boundary in docs/ARCHITECTURE.md. It holds metadata
ABOUT an uploaded file (who, what kind, how big, where it lives in
object storage, whether it's currently retrievable) — never the file's
bytes themselves (see docs/adr/ADR-0008-document-storage-and-security.md
"SQL Metadata vs. Object Bytes") and never any extracted/interpreted
content from it. No OCR, no summarization, no medical/clinical
interpretation of a document's contents exists anywhere in this domain.

Uploaded files are UNTRUSTED INPUT — see `app.services.document_validation`
for signature-based type validation and docs/DOCUMENTS.md "Malware-Scanning
Boundary" for what this story does and does not guarantee about a
successfully-validated file's safety.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.patient import Patient


class DocumentType(StrEnum):
    """The kind of administrative document this is.

    Deliberately small and administrative — see docs/DOMAIN_MODEL.md
    "Enum Strategy" for the same controlled-evolution rationale as every
    other enum in this codebase. `REFERRAL` means a referral document is
    stored administratively (e.g. a PDF a referring provider sent) — it
    does NOT mean AgentCare interprets, routes, or acts on referral
    CONTENT clinically. See docs/DOCUMENTS.md "Healthcare Safety
    Boundary".
    """

    IDENTITY = "identity"
    INSURANCE = "insurance"
    REFERRAL = "referral"
    CONSENT = "consent"
    OTHER = "other"


class DocumentStatus(StrEnum):
    """Controlled document lifecycle status.

    Reflects whether the database record currently has a successfully
    persisted object behind it — see docs/DOCUMENTS.md "Upload State
    Machine":

    - `PENDING`: metadata row created; the object write is in progress or
      has not yet been confirmed. NEVER treated as retrievable.
    - `AVAILABLE`: the object was written successfully; `size_bytes` and
      `sha256` are populated (enforced by a database `CHECK` constraint
      — see `PatientDocument.__table_args__`); safe to download.
    - `FAILED`: the object write did not complete successfully. The
      metadata row is kept (auditability), but never treated as
      retrievable.
    - `DELETED`: the document was retired/deleted; the underlying object
      has been (or is being) removed from storage. Never treated as
      retrievable. No hard deletion of the metadata row — see
      docs/DOCUMENTS.md "Deletion Semantics".
    """

    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentMediaType(StrEnum):
    """The small, signature-validated allowlist of media types this story
    accepts — see `app.services.document_validation.sniff_media_type`.
    Values are the canonical MIME type strings, reused directly as the
    `Content-Type` on download (`app.api.v1.endpoints.documents`)."""

    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"


class PatientDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An administrative document uploaded for one `Patient`, scoped to
    one `Organization`.

    Tenant/patient ownership integrity is enforced at the DATABASE level,
    the same composite-FK technique used throughout this codebase (see
    `app/models/appointment.py`):
    `(organization_id, patient_id) -> patients(organization_id, id)`.

    `uploaded_by_user_id` is enforced at the DATABASE level to have been
    (at some point) a genuine member of `organization_id` — a composite
    FK against `organization_memberships(organization_id, user_id)`,
    which itself transitively guarantees a valid `users.id` (that table's
    own `user_id` FK is `ON DELETE RESTRICT`). This does NOT, and cannot,
    guarantee the membership was ACTIVE or held an upload-permitted role
    AT THE TIME of this specific upload — membership `is_active`/`role`
    are mutable independently of any document row, the same
    existence-vs-activity split already established for
    `PractitionerDepartment.is_active`
    (`app/models/practitioner_availability.py`) and `Patient` linkage
    validity (ADR-0005). That check is, and must remain, a service-level
    concern (`app.services.document.PatientDocumentService`), re-verified
    on every upload — see docs/DOCUMENTS.md "Tenant & Patient Ownership".
    No plain `ForeignKey("users.id")` exists on this column: the
    composite FK is the sole, sufficient enforcement, and no
    `relationship()` is built off it (mirrors
    `app/models/department.py`'s `facility_id`, which has no plain FK for
    the identical reason).
    """

    __tablename__ = "patient_documents"
    __table_args__ = (
        # `size_bytes`/`sha256` are only ever populated once the object
        # write succeeds — this constraint makes "reported AVAILABLE
        # without proof of a persisted object" a database-level
        # impossibility, not just a service-level convention. See
        # docs/DOCUMENTS.md "Upload State Machine".
        CheckConstraint(
            "(status <> 'available') OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="available_has_size_and_hash",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_patient_documents_org_patient_patients",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "uploaded_by_user_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_patient_documents_org_uploader_memberships",
            ondelete="RESTRICT",
        ),
        Index("ix_patient_documents_patient_created_at", "patient_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # No plain `ForeignKey("patients.id")` collision here: this IS the
    # plain FK (needed for `relationship()` join inference, same pattern
    # as `Appointment.patient_id` — see app/models/appointment.py), kept
    # alongside the composite ownership FK above.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # See the class docstring: enforced only via the composite FK above.
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    document_type: Mapped[DocumentType] = mapped_column(
        SqlEnum(
            DocumentType,
            name="document_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        SqlEnum(
            DocumentStatus,
            name="document_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        default=DocumentStatus.PENDING,
        index=True,
    )
    # DISPLAY metadata only — sanitized by
    # `app.services.document_validation.sanitize_original_filename` before
    # ever reaching this column. Never used as a filesystem path, and
    # never trusted as an authoritative media type. See
    # docs/DOCUMENTS.md "Original Filename".
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Server-generated, opaque (see
    # `app.services.document._generate_storage_key`) — never
    # client-supplied, never derived from patient/user identity. Globally
    # unique: two documents can never collide on the same storage
    # location. Never exposed to any API client — see
    # docs/DOCUMENTS.md "Download Safety".
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    media_type: Mapped[DocumentMediaType] = mapped_column(
        SqlEnum(
            DocumentMediaType,
            name="document_media_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    # NULL while PENDING/FAILED — populated exactly once, when the object
    # write succeeds (see the `available_has_size_and_hash` CHECK above).
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    organization: Mapped[Organization] = relationship()
    patient: Mapped[Patient] = relationship(foreign_keys=[patient_id])

    def __repr__(self) -> str:
        # Deliberately excludes `original_filename` — display metadata
        # about a specific patient's document, not something to casually
        # surface in logs/reprs (see SECURITY.md "Sensitive Logging
        # Restrictions").
        return (
            f"PatientDocument(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"patient_id={self.patient_id!r}, status={self.status!r})"
        )
