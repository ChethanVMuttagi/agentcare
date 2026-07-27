"""Tenant-scoped persistence/query operations for `PractitionerAvailability`.

Every read here REQUIRES an `organization_id`. This module only adds,
flushes, and queries. It never commits — see `app.services.availability`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability


async def list_by_practitioner(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[PractitionerAvailability]:
    """Return all availability windows for `practitioner_id` within
    `organization_id` (every department, every day, active or not),
    newest first."""
    result = await session.execute(
        select(PractitionerAvailability)
        .where(
            PractitionerAvailability.organization_id == organization_id,
            PractitionerAvailability.practitioner_id == practitioner_id,
        )
        .order_by(PractitionerAvailability.created_at.desc(), PractitionerAvailability.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def list_active_by_practitioner_department_day(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    department_id: uuid.UUID,
    day_of_week: DayOfWeek,
) -> Sequence[PractitionerAvailability]:
    """Return the ACTIVE availability windows that a new window for the
    same organization/practitioner/department/day would need to be
    checked against for overlap — see `app.services.availability`."""
    result = await session.execute(
        select(PractitionerAvailability).where(
            PractitionerAvailability.organization_id == organization_id,
            PractitionerAvailability.practitioner_id == practitioner_id,
            PractitionerAvailability.department_id == department_id,
            PractitionerAvailability.day_of_week == day_of_week,
            PractitionerAvailability.is_active.is_(True),
        )
    )
    return result.scalars().all()


async def create(
    session: AsyncSession, availability: PractitionerAvailability
) -> PractitionerAvailability:
    """Add and flush a new `PractitionerAvailability`. Does NOT commit —
    see `app.services.availability.AvailabilityService.create_availability`."""
    session.add(availability)
    await session.flush()
    return availability
