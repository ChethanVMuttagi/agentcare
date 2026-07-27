"""PractitionerAvailability: a persisted, RECURRING weekly availability window.

This is NOT an appointment slot and NOT a materialized calendar of
concrete dates — it is a recurring rule such as "Practitioner X is
available in Cardiology every Monday, 09:00-12:00, Asia/Kolkata". Turning
this into concrete bookable slots (and the concept of an `Appointment`
itself) is explicitly future work — see docs/SCHEDULING_RESOURCES.md and
docs/adr/ADR-0006-scheduling-resources.md.
"""

from __future__ import annotations

import uuid
from datetime import time
from enum import StrEnum
from typing import TYPE_CHECKING
from zoneinfo import available_timezones

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Time,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.organization import Organization
    from app.models.practitioner import Practitioner


class DayOfWeek(StrEnum):
    """Day of a recurring weekly availability window.

    Deliberately a small, controlled enum (same persistence strategy as
    every other enum in this codebase) rather than a raw integer, so the
    persisted/API value is self-describing (`"monday"`, not `0`).
    """

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class PractitionerAvailability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A recurring weekly availability window for one Practitioner within
    one Department.

    The `(practitioner_id, department_id)` pairing MUST already be an
    existing assignment (`PractitionerDepartment`) — enforced at the
    DATABASE level via a composite foreign key into
    `practitioner_departments(organization_id, practitioner_id,
    department_id)`, not just application validation. This guarantees
    availability can never reference a practitioner/department pairing
    that was never assigned. It does NOT, by itself, guarantee the
    assignment is still ACTIVE (`PractitionerDepartment.is_active`) —
    that mutable-state check is enforced at the service level
    (`app.services.availability`), the same layered pattern used for
    `Patient` <-> `User` linkage in STORY-005. See
    docs/SCHEDULING_RESOURCES.md.

    `start_time`/`end_time` are wall-clock times (no timezone attached to
    the column itself); `timezone` gives them meaning, the same modeling
    choice as `Facility.timezone`. A database `CHECK` constraint enforces
    `start_time < end_time`.

    Overlap prevention (no two ACTIVE windows for the same organization +
    practitioner + department + day_of_week may overlap) is a
    SERVICE-level rule, not a database constraint — see
    `app.services.availability` and docs/SCHEDULING_RESOURCES.md
    "Overlapping Availability" for the documented concurrency limitation.
    """

    __tablename__ = "practitioner_availability"
    __table_args__ = (
        # `name` here is just the constraint_name component — the
        # naming convention (app/db/base.py) applies the
        # `ck_%(table_name)s_%(constraint_name)s` prefix automatically,
        # the same way it does for every enum-backed CHECK constraint in
        # this codebase (e.g. `Organization.organization_type`'s
        # `name="organization_type"` -> `ck_organizations_organization_type`).
        # Passing an already-prefixed name here would double-prefix it.
        CheckConstraint(
            "start_time < end_time",
            name="start_before_end",
        ),
        # Guarantees `(practitioner_id, department_id)` is an actual,
        # existing assignment — see the class docstring. Name shortened
        # from the mechanical naming-convention form (would exceed
        # PostgreSQL's 63-byte identifier limit) while staying
        # descriptive.
        ForeignKeyConstraint(
            ["organization_id", "practitioner_id", "department_id"],
            [
                "practitioner_departments.organization_id",
                "practitioner_departments.practitioner_id",
                "practitioner_departments.department_id",
            ],
            name="fk_practitioner_availability_assignment",
            ondelete="RESTRICT",
        ),
        # Supports the overlap-check query (same practitioner + department
        # + day_of_week, active windows only) run on every create — see
        # app.services.availability.
        Index(
            "ix_practitioner_availability_practitioner_department_day",
            "practitioner_id",
            "department_id",
            "day_of_week",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Plain single-column FKs, IN ADDITION to the composite assignment FK
    # above: the composite FK alone already guarantees both reference a
    # real, matching assignment, but SQLAlchemy's `relationship()` needs
    # an explicit FK path between two tables to infer a join condition —
    # there is no FK constraint directly from this table to
    # `practitioners`/`departments` otherwise (the composite FK points at
    # `practitioner_departments`, a third table). These are intentionally
    # redundant with the composite FK for constraint purposes, but not
    # redundant for ORM relationship inference.
    practitioner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("practitioners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    day_of_week: Mapped[DayOfWeek] = mapped_column(
        SqlEnum(
            DayOfWeek,
            name="day_of_week",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    # IANA timezone identifier — same modeling/validation choice as
    # Facility.timezone (application-validated, not a giant DB enum).
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship()
    practitioner: Mapped[Practitioner] = relationship(back_populates="availability_windows")
    department: Mapped[Department] = relationship(back_populates="availability_windows")

    @validates("timezone")
    def _validate_timezone(self, _key: str, value: str) -> str:
        if value not in available_timezones():
            raise ValueError(f"{value!r} is not a valid IANA timezone identifier.")
        return value

    def __repr__(self) -> str:
        return (
            f"PractitionerAvailability(id={self.id!r}, "
            f"practitioner_id={self.practitioner_id!r}, "
            f"department_id={self.department_id!r}, day_of_week={self.day_of_week!r})"
        )
