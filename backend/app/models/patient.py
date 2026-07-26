"""Patient: an administrative patient record belonging to an Organization.

This is an ADMINISTRATIVE record only — see docs/PATIENTS.md and the
healthcare safety boundary in docs/ARCHITECTURE.md. It deliberately holds
no diagnosis, symptoms, medication, treatment, clinical notes, insurance,
or any other clinical/medical content.

`Patient` is not `User`. `User` is a global authentication identity;
`Patient` is a healthcare-organization's administrative record of a
person, scoped to exactly one `Organization`. A `Patient` may optionally
be linked to a `User` (a "portal identity") via `user_id` — see
docs/RBAC.md and docs/adr/ADR-0005-patient-identity-and-access.md for the
full identity/access model this supports.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


def normalize_person_name(value: str) -> str:
    """Modest whitespace normalization for a person's name.

    Trims surrounding whitespace and collapses internal runs of whitespace
    to a single space. Deliberately NOT an attempt at culturally universal
    name parsing (no splitting into given/family/middle beyond the
    first/last fields this model already has, no Unicode case-folding) —
    this is an initial, administrative representation, not a definitive
    identity-document parser.
    """
    return " ".join(value.strip().split())


class Patient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An administrative patient record belonging to one Organization.

    `patient_number` is the organization's own administrative identifier
    for this patient — unique only *within* the organization (see the
    composite unique constraint below), not globally. `user_id` is
    OPTIONAL: an organization may register a patient administratively
    before that person has (or ever gets) a portal `User` identity. See
    docs/PATIENTS.md.
    """

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "patient_number",
            name="uq_patients_organization_id_patient_number",
        ),
        # NULLs are distinct under PostgreSQL UNIQUE constraints, so this
        # allows any number of patients with no linked user (user_id IS
        # NULL) per organization, while still guaranteeing that a given
        # (organization, user) pair maps to AT MOST ONE patient record —
        # see docs/PATIENTS.md "User <-> Patient Linkage".
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_patients_organization_id_user_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Optional: see the module/class docstring. No FK ondelete cascade —
    # same no-silent-cascade rationale as every other tenant/identity FK in
    # this codebase (app/models/organization.py, app/models/membership.py).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    patient_number: Mapped[str] = mapped_column(String(64), nullable=False)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship(back_populates="patients")
    user: Mapped[User | None] = relationship(back_populates="patients")

    @validates("patient_number")
    def _normalize_patient_number(self, _key: str, value: str) -> str:
        # Deliberately NOT case-folded (unlike User.email): organizations
        # may use meaningfully-cased administrative identifiers (e.g. a
        # scheme mixing letters and digits). Only surrounding whitespace is
        # stripped — see docs/PATIENTS.md "Patient Number".
        return value.strip()

    @validates("first_name", "last_name")
    def _normalize_name(self, _key: str, value: str) -> str:
        return normalize_person_name(value)

    @validates("date_of_birth")
    def _validate_date_of_birth(self, _key: str, value: date) -> date:
        if value > date.today():
            raise ValueError("date_of_birth must not be in the future.")
        return value

    def __repr__(self) -> str:
        return (
            f"Patient(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"patient_number={self.patient_number!r})"
        )
