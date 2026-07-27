"""Department: an administrative scheduling unit belonging to a Facility.

See docs/SCHEDULING_RESOURCES.md for the full scheduling-resource model
and docs/adr/ADR-0006-scheduling-resources.md for the decision record.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.facility import Facility
    from app.models.organization import Organization
    from app.models.practitioner_availability import PractitionerAvailability
    from app.models.practitioner_department import PractitionerDepartment


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An administrative scheduling unit (e.g. "Cardiology") belonging to
    exactly one Facility.

    `code` is unique only *within* its facility (see the composite unique
    constraint below) — the same code is expected and allowed to appear
    under different facilities, including facilities in different
    organizations.

    The referenced `Facility` MUST belong to the SAME `Organization` as
    this `Department`. This is enforced at the DATABASE level, not just
    the application: `(organization_id, facility_id)` is a composite
    foreign key into `facilities(organization_id, id)` (see
    `app/models/facility.py`'s `uq_facilities_organization_id_id`) —
    PostgreSQL itself rejects any row where the referenced facility's
    `organization_id` doesn't match this row's `organization_id`. See
    docs/SCHEDULING_RESOURCES.md "Department Ownership Integrity".
    """

    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("facility_id", "code", name="uq_departments_facility_id_code"),
        # Composite unique key (in addition to the plain PK on `id`) so
        # `PractitionerDepartment`/`PractitionerAvailability` can hold
        # composite FKs `(organization_id, department_id) ->
        # departments(organization_id, id)` — the same technique used for
        # `facilities` above, applied one level down.
        UniqueConstraint("organization_id", "id", name="uq_departments_organization_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "facility_id"],
            ["facilities.organization_id", "facilities.id"],
            name="fk_departments_organization_id_facility_id_facilities",
            ondelete="RESTRICT",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # No plain `ForeignKey("facilities.id")` here: the composite
    # `ForeignKeyConstraint` above already guarantees `facility_id`
    # references a real facility — one whose `organization_id` also
    # matches this row's. A second, single-column FK would be redundant.
    facility_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship(back_populates="departments")
    # `overlaps="organization"`: both this relationship (via the composite
    # FK, which includes `organization_id`) and `Department.organization`
    # touch the `organization_id` column. This is intentional and safe —
    # `organization_id` is always set directly as a column value at
    # construction time (see app.services.department), never populated by
    # assigning to either relationship — so there is no actual risk of
    # the two relationships writing conflicting values; this only silences
    # SQLAlchemy's (accurate but, here, harmless) ambiguity warning.
    facility: Mapped[Facility] = relationship(
        back_populates="departments", overlaps="organization"
    )

    # No delete cascade — same rationale as every other relationship in
    # this codebase. The FK (`practitioner_departments.department_id`,
    # part of a composite `ON DELETE RESTRICT` FK — see
    # app/models/practitioner_department.py) is the actual enforcement
    # point.
    practitioner_assignments: Mapped[list[PractitionerDepartment]] = relationship(
        back_populates="department",
        passive_deletes=True,
    )

    # No delete cascade — same rationale as above. The FK
    # (`practitioner_availability`'s composite assignment FK, ON DELETE
    # RESTRICT — see app/models/practitioner_availability.py) is the
    # actual enforcement point.
    availability_windows: Mapped[list[PractitionerAvailability]] = relationship(
        back_populates="department",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"Department(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"facility_id={self.facility_id!r}, code={self.code!r})"
        )
