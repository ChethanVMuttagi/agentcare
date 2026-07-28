"""Tenant-scoped persistence/query operations for `Appointment`.

Every read here REQUIRES an `organization_id` — same discipline as every
other repository in this codebase (see `app.repositories.patient`). This
module only adds, flushes, and queries. It never commits, and it performs
no RBAC/authorization decisions and no business validation — see
`app.services.appointment`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment, AppointmentStatus


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, appointment_id: uuid.UUID
) -> Appointment | None:
    """Return the appointment with `appointment_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(Appointment).where(
            Appointment.organization_id == organization_id,
            Appointment.id == appointment_id,
        )
    )
    return result.scalar_one_or_none()


async def list_by_organization(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Appointment]:
    """Return appointments belonging to `organization_id`, newest first.

    Administrative, org-wide listing — callers must ensure the requester
    is authorized to see organization-wide appointment data (ADMIN/STAFF
    only; see `app.services.appointment` and docs/APPOINTMENTS.md
    "Listing Privacy"). A `PATIENT` caller must never reach this
    function — `list_by_patient` is the self-scoped equivalent.
    """
    result = await session.execute(
        select(Appointment)
        .where(Appointment.organization_id == organization_id)
        .order_by(Appointment.start_at.desc(), Appointment.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def list_by_patient(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Appointment]:
    """Return appointments for one patient within `organization_id`, newest first."""
    result = await session.execute(
        select(Appointment)
        .where(
            Appointment.organization_id == organization_id,
            Appointment.patient_id == patient_id,
        )
        .order_by(Appointment.start_at.desc(), Appointment.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def list_practitioner_appointments_in_range(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
    statuses: Sequence[AppointmentStatus] = (AppointmentStatus.BOOKED,),
) -> Sequence[Appointment]:
    """Return a practitioner's appointments overlapping `[range_start, range_end)`.

    Used by `app.services.availability.AvailabilityQueryService` to remove
    candidate times that already conflict with an existing appointment.
    Defaults to `BOOKED` only (the only status that occupies time — see
    `app.models.appointment.AppointmentStatus`); callers needing a
    different view (e.g. an audit report including cancelled/completed
    appointments) may pass a wider `statuses` sequence explicitly.
    """
    result = await session.execute(
        select(Appointment).where(
            Appointment.organization_id == organization_id,
            Appointment.practitioner_id == practitioner_id,
            Appointment.status.in_(statuses),
            Appointment.start_at < range_end,
            Appointment.end_at > range_start,
        )
    )
    return result.scalars().all()


async def count_by_status(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[AppointmentStatus, int]:
    """Organization-wide appointment counts grouped by `status` — the
    appointments breakdown for the Milestone B analytics summary
    (`app.api.v1.endpoints.analytics`)."""
    result = await session.execute(
        select(Appointment.status, func.count())
        .where(Appointment.organization_id == organization_id)
        .group_by(Appointment.status)
    )
    return {status: count for status, count in result.all()}


async def create(session: AsyncSession, appointment: Appointment) -> Appointment:
    """Add and flush a new `Appointment`. Does NOT commit — see
    `app.services.appointment.AppointmentService.book_appointment`.

    A conflicting booking raises `sqlalchemy.exc.IntegrityError` at flush
    time (the PostgreSQL exclusion constraint) — this function does not
    catch it; translating that specific, expected error into a domain
    `AppointmentConflictError` is `AppointmentService`'s job (see its
    module docstring), not this repository's.
    """
    session.add(appointment)
    await session.flush()
    return appointment
