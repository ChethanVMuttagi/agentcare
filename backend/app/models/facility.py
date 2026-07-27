"""Facility: a physical/operational location belonging to one Organization.

See docs/DOMAIN_MODEL.md for the full tenant-hierarchy explanation and
docs/adr/ADR-0003-multi-tenancy.md for the tenancy decision record.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING
from zoneinfo import available_timezones

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.organization import Organization


class FacilityType(StrEnum):
    """The kind of physical/operational location this facility is.

    Deliberately small — see docs/DOMAIN_MODEL.md "Enum Strategy" for the
    same controlled-evolution rationale as `OrganizationType`.
    """

    HOSPITAL = "hospital"
    CLINIC = "clinic"
    DIAGNOSTIC_CENTER = "diagnostic_center"
    OTHER = "other"


class Facility(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A physical/operational location belonging to exactly one Organization.

    `code` is unique only *within* its organization (see the composite
    unique constraint below) — the same code is expected and allowed to
    appear under different organizations.
    """

    __tablename__ = "facilities"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_facilities_organization_id_code"),
        # Composite unique key (in addition to the plain PK on `id`) so
        # `Department` can hold a composite FK `(organization_id,
        # facility_id) -> facilities(organization_id, id)` — this is what
        # makes "a Department's facility must belong to the SAME
        # organization" a database-enforced invariant rather than an
        # API-only check. See app/models/department.py and
        # docs/SCHEDULING_RESOURCES.md.
        UniqueConstraint("organization_id", "id", name="uq_facilities_organization_id_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    facility_type: Mapped[FacilityType] = mapped_column(
        # native_enum=False + create_constraint=True for the same reason as
        # Organization.organization_type — see app/models/organization.py.
        SqlEnum(
            FacilityType,
            name="facility_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    # IANA timezone identifier (e.g. "America/New_York"), not a database
    # enum/lookup table — validated at the application layer below rather
    # than maintained as a giant DB-level enum of every timezone.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship(back_populates="facilities")

    # Same no-delete-cascade rationale as every other relationship in this
    # codebase (see app/models/organization.py) — the FK
    # (`departments.facility_id`, part of a composite `ON DELETE RESTRICT`
    # FK — see app/models/department.py) is the actual enforcement point.
    # `overlaps="organization"`: this relationship and `Department.organization`
    # both touch `departments.organization_id` — intentional and harmless,
    # since that column is always set directly at construction time, never
    # via relationship assignment (see app/models/department.py's
    # `Department.facility` for the identical rationale in more detail).
    departments: Mapped[list[Department]] = relationship(
        back_populates="facility",
        passive_deletes=True,
        overlaps="organization",
    )

    @validates("timezone")
    def _validate_timezone(self, _key: str, value: str) -> str:
        if value not in available_timezones():
            raise ValueError(f"{value!r} is not a valid IANA timezone identifier.")
        return value

    def __repr__(self) -> str:
        return (
            f"Facility(id={self.id!r}, "
            f"organization_id={self.organization_id!r}, code={self.code!r})"
        )
