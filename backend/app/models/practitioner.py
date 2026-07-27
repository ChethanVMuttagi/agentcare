"""Practitioner: a schedulable healthcare professional (administrative resource).

Deliberately named `Practitioner`, not `Doctor` — a schedulable
healthcare professional may be a physician, physiotherapist, counselor,
therapist, or other healthcare professional. See
docs/SCHEDULING_RESOURCES.md and
docs/adr/ADR-0006-scheduling-resources.md for the full rationale.

This is an administrative SCHEDULING resource, not a clinical one: it
holds no diagnosis capability, no treatment/prescription authority, and
no unnecessary personal information — see docs/ARCHITECTURE.md Section 7
"Healthcare Safety Boundary".
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values
from app.models.patient import normalize_person_name

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.practitioner_availability import PractitionerAvailability
    from app.models.practitioner_department import PractitionerDepartment


class PractitionerType(StrEnum):
    """The kind of schedulable healthcare professional this practitioner is.

    Deliberately small (see docs/DOMAIN_MODEL.md "Enum Strategy" for the
    same controlled-evolution rationale as `OrganizationType`/`Role`).
    This is NOT a medical specialty — specialty/department association is
    a separate concept (`PractitionerDepartment`), not encoded here.
    """

    PHYSICIAN = "physician"
    PHYSIOTHERAPIST = "physiotherapist"
    COUNSELOR = "counselor"
    THERAPIST = "therapist"
    OTHER = "other"


class Practitioner(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A schedulable healthcare professional, scoped to one Organization.

    No `facility_id`: a practitioner's facility association is derived
    through department assignment (`PractitionerDepartment` ->
    `Department.facility_id`), not stored directly here — see
    docs/SCHEDULING_RESOURCES.md "Facility Relationship" for why a direct
    `facility_id` would create a conflicting ownership model once a
    practitioner works across multiple facilities via multiple
    department assignments.
    """

    __tablename__ = "practitioners"
    __table_args__ = (
        # Composite unique key (in addition to the plain PK on `id`) so
        # `PractitionerDepartment`/`PractitionerAvailability` can hold
        # composite FKs `(organization_id, practitioner_id) ->
        # practitioners(organization_id, id)` — same technique as
        # `facilities`/`departments`.
        UniqueConstraint("organization_id", "id", name="uq_practitioners_organization_id_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    practitioner_type: Mapped[PractitionerType] = mapped_column(
        # native_enum=False + create_constraint=True for the same reason
        # as Organization.organization_type — see app/models/organization.py.
        SqlEnum(
            PractitionerType,
            name="practitioner_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship(back_populates="practitioners")

    # No delete cascade — same rationale as every other relationship in
    # this codebase. The FK (`practitioner_departments.practitioner_id`,
    # part of a composite `ON DELETE RESTRICT` FK — see
    # app/models/practitioner_department.py) is the actual enforcement
    # point. `overlaps="practitioner_assignments"`:
    # `practitioner_departments.organization_id` is also written via
    # `Department.practitioner_assignments` — intentional and harmless
    # (see `Facility.departments` in app/models/facility.py for the
    # detailed rationale, same pattern applied here).
    department_assignments: Mapped[list[PractitionerDepartment]] = relationship(
        back_populates="practitioner",
        passive_deletes=True,
        overlaps="practitioner_assignments",
    )

    # No delete cascade — same rationale as above. The FK
    # (`practitioner_availability`'s composite assignment FK, ON DELETE
    # RESTRICT — see app/models/practitioner_availability.py) is the
    # actual enforcement point.
    availability_windows: Mapped[list[PractitionerAvailability]] = relationship(
        back_populates="practitioner",
        passive_deletes=True,
    )

    @validates("first_name", "last_name")
    def _normalize_name(self, _key: str, value: str) -> str:
        return normalize_person_name(value)

    def __repr__(self) -> str:
        return (
            f"Practitioner(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"practitioner_type={self.practitioner_type!r})"
        )
