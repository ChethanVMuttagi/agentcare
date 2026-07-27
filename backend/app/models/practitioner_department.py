"""PractitionerDepartment: the many-to-many assignment of a Practitioner to a Department.

A practitioner may work in multiple departments; a department may contain
multiple practitioners. See docs/SCHEDULING_RESOURCES.md and
docs/adr/ADR-0006-scheduling-resources.md.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.organization import Organization
    from app.models.practitioner import Practitioner


class PractitionerDepartment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One Practitioner's assignment to one Department.

    A real model (not a bare association table) so the assignment has its
    own lifecycle: `is_active` lets an assignment be revoked (e.g. a
    practitioner stops working in a department) without deleting the
    historical record, and `created_at`/`updated_at` give a basic audit
    trail of when the assignment was made/changed.

    Both `practitioner_id` and `department_id` MUST belong to the SAME
    `Organization` as this row's own `organization_id` — enforced at the
    DATABASE level via two composite foreign keys (not just application
    validation): `(organization_id, practitioner_id) ->
    practitioners(organization_id, id)` and `(organization_id,
    department_id) -> departments(organization_id, id)`. See
    docs/SCHEDULING_RESOURCES.md "Practitioner <-> Department Association".
    """

    __tablename__ = "practitioner_departments"
    __table_args__ = (
        # A practitioner may be assigned to a given department at most
        # once. Deliberately includes `organization_id` (rather than just
        # `UNIQUE(practitioner_id, department_id)`) so
        # `PractitionerAvailability` can hold a composite FK
        # `(organization_id, practitioner_id, department_id) ->
        # practitioner_departments(organization_id, practitioner_id,
        # department_id)` — the DB-level guarantee that availability can
        # only reference an actual assignment pairing. See
        # app/models/practitioner_availability.py.
        # Constraint names below are deliberately shortened from the
        # naming convention's full mechanical form (which would exceed
        # PostgreSQL's 63-byte identifier limit for this table/column
        # combination) while staying descriptive.
        UniqueConstraint(
            "organization_id",
            "practitioner_id",
            "department_id",
            name="uq_practitioner_departments_org_practitioner_department",
        ),
        ForeignKeyConstraint(
            ["organization_id", "practitioner_id"],
            ["practitioners.organization_id", "practitioners.id"],
            name="fk_practitioner_departments_org_practitioner_practitioners",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            name="fk_practitioner_departments_org_department_departments",
            ondelete="RESTRICT",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # No plain single-column FKs here: the two composite
    # `ForeignKeyConstraint`s above already guarantee both ids reference
    # real rows whose `organization_id` matches this row's.
    practitioner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # `overlaps=`: all three relationships below write
    # `practitioner_departments.organization_id` in some sense (via
    # `organization`'s plain FK, or as part of the composite FKs
    # `practitioner`/`department` participate in) — intentional and
    # harmless, since `organization_id` is always set directly at
    # construction time, never via relationship assignment. See
    # `Facility.departments` in app/models/facility.py for the detailed
    # rationale behind this pattern, applied consistently here.
    organization: Mapped[Organization] = relationship(
        overlaps="department_assignments,practitioner_assignments"
    )
    practitioner: Mapped[Practitioner] = relationship(
        back_populates="department_assignments",
        overlaps="organization,practitioner_assignments",
    )
    department: Mapped[Department] = relationship(
        back_populates="practitioner_assignments",
        overlaps="department_assignments,organization,practitioner",
    )

    def __repr__(self) -> str:
        return (
            f"PractitionerDepartment(id={self.id!r}, "
            f"practitioner_id={self.practitioner_id!r}, "
            f"department_id={self.department_id!r})"
        )
