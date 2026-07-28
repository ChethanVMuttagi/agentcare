"""Appointment: a booked administrative scheduling record.

An `Appointment` is a concrete, dated booking of a `Patient` against a
`Practitioner`'s recurring `PractitionerAvailability` (see
`app/models/practitioner_availability.py`), within one `Department`. It is
deliberately ADMINISTRATIVE only — no diagnosis, symptoms, treatment,
medication, or clinical notes anywhere on this model. See
docs/APPOINTMENTS.md and docs/adr/ADR-0007-appointment-concurrency.md for
the full design and rationale, in particular the PostgreSQL-native
concurrency-safety mechanism this model relies on (Section "Concurrency
Safety" below).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.organization import Organization
    from app.models.patient import Patient
    from app.models.practitioner import Practitioner


class AppointmentStatus(StrEnum):
    """Controlled appointment lifecycle status.

    Deliberately small — see docs/APPOINTMENTS.md "Status Model" for which
    statuses occupy practitioner/patient time for collision purposes:
    `BOOKED` occupies time (participates in the exclusion constraints
    below); `CANCELLED` and `COMPLETED` do not (both are excluded from the
    exclusion-constraint predicate, so a cancelled or historically
    completed appointment never blocks a new booking over the same time,
    and never conflicts with itself if a later appointment happens to
    reuse the same slot).
    """

    BOOKED = "booked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A booked administrative appointment, scoped to one Organization.

    Tenant/ownership integrity is enforced at the DATABASE level, the same
    composite-FK technique used throughout this codebase
    (`app/models/department.py`, `app/models/practitioner_availability.py`):

    - `(organization_id, patient_id) -> patients(organization_id, id)`
      guarantees the patient belongs to this SAME organization.
    - `(organization_id, practitioner_id, department_id) ->
      practitioner_departments(organization_id, practitioner_id,
      department_id)` guarantees the practitioner/department pairing is a
      real, existing assignment within this SAME organization — and
      transitively, since `practitioner_departments` itself carries
      composite FKs into `practitioners`/`departments`, that the
      practitioner and department also both belong to this organization.
      This does NOT, by itself, guarantee the assignment is currently
      ACTIVE (mutable state) — that is a service-level check, the same
      layering `PractitionerAvailability` already established.

    ## Concurrency Safety

    Double-booking is prevented at the DATABASE level via two PostgreSQL
    `EXCLUDE` constraints (require the `btree_gist` extension — see the
    migration), NOT a SELECT-then-INSERT service-level check:

    - `ex_appointments_practitioner_no_overlap`: no two `BOOKED`
      appointments for the same `practitioner_id` may have overlapping
      `[start_at, end_at)` ranges.
    - `ex_appointments_patient_no_overlap`: same guarantee, for
      `patient_id` — see docs/APPOINTMENTS.md "Patient Double-Booking"
      for the policy decision.

    Both constraints apply `WHERE (status = 'booked')` — cancelled and
    completed appointments never participate, so freeing a cancelled slot
    or a status change on a historical appointment can never spuriously
    conflict with a new booking. This is genuinely race-safe under
    concurrent transactions (verified against real PostgreSQL in
    `tests/db/test_appointment_concurrency.py`), unlike
    `PractitionerAvailability`'s overlap check (STORY-006), which is a
    service-level pre-check only — see
    docs/adr/ADR-0007-appointment-concurrency.md for why this story's
    concurrency requirement justifies the added infrastructure that
    ADR-0006 explicitly deferred.
    """

    __tablename__ = "appointments"
    __table_args__ = (
        # Added in STORY-013 — `app.models.reminder.Reminder` is the
        # first model to reference `appointments` via a composite
        # `(organization_id, appointment_id)` FK (the same tenant-safety
        # technique `patients`/`workflow_runs`/`workflow_steps` already
        # use), which requires PostgreSQL to have a unique constraint
        # covering EXACTLY those two columns to reference. Purely
        # additive and never fails on existing data: `id` alone is
        # already the primary key (globally unique), so every
        # `(organization_id, id)` pair is trivially already unique too.
        UniqueConstraint("organization_id", "id", name="uq_appointments_organization_id_id"),
        CheckConstraint("start_at < end_at", name="start_before_end"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_appointments_org_patient_patients",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "practitioner_id", "department_id"],
            [
                "practitioner_departments.organization_id",
                "practitioner_departments.practitioner_id",
                "practitioner_departments.department_id",
            ],
            name="fk_appointments_assignment",
            ondelete="RESTRICT",
        ),
        ExcludeConstraint(
            ("practitioner_id", "="),
            (text("tstzrange(start_at, end_at, '[)')"), "&&"),
            where=text("status = 'booked'"),
            using="gist",
            name="ex_appointments_practitioner_no_overlap",
        ),
        ExcludeConstraint(
            ("patient_id", "="),
            (text("tstzrange(start_at, end_at, '[)')"), "&&"),
            where=text("status = 'booked'"),
            using="gist",
            name="ex_appointments_patient_no_overlap",
        ),
        Index("ix_appointments_practitioner_start_at", "practitioner_id", "start_at"),
        Index("ix_appointments_patient_start_at", "patient_id", "start_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # No plain single-column `ForeignKey("patients.id")` decorator here:
    # `patient_id` below carries an explicit `ForeignKeyConstraint` (see
    # `__table_args__` for the "no ambiguous relationship" note on
    # `patient`) purely so SQLAlchemy's `relationship()` has a direct,
    # unambiguous join path to `patients` distinct from the composite
    # ownership FK above — mirrors the `practitioner_id`/`department_id`
    # pattern in `app/models/practitioner_availability.py`.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    practitioner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("practitioners.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        # native_enum=False + create_constraint=True for the same reason as
        # every other enum in this codebase — see app/models/organization.py.
        SqlEnum(
            AppointmentStatus,
            name="appointment_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
        default=AppointmentStatus.BOOKED,
        index=True,
    )
    # Short ADMINISTRATIVE cancellation reason only (e.g. "patient
    # requested", "practitioner unavailable") — never clinical content.
    # See docs/APPOINTMENTS.md "Cancellation".
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    organization: Mapped[Organization] = relationship()
    # `foreign_keys=[patient_id]`: `appointments` carries TWO separate FK
    # constraints that both reference `patients` — the plain `patient_id
    # -> patients.id` FK above, and the composite ownership FK
    # `fk_appointments_org_patient_patients` in `__table_args__`. Without
    # this, SQLAlchemy cannot determine which FK path to use for this
    # relationship's join condition and raises an ambiguity error at
    # mapper-configuration time. The composite FK still fully enforces
    # tenant ownership at the database level regardless of which FK the
    # ORM relationship itself joins through.
    patient: Mapped[Patient] = relationship(foreign_keys=[patient_id])
    practitioner: Mapped[Practitioner] = relationship()
    department: Mapped[Department] = relationship()

    def __repr__(self) -> str:
        return (
            f"Appointment(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"practitioner_id={self.practitioner_id!r}, status={self.status!r})"
        )
