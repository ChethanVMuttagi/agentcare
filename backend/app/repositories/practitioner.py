"""Tenant-scoped persistence/query operations for `Practitioner`.

Every read here REQUIRES an `organization_id` — same discipline as
`app.repositories.patient`/`app.repositories.department`. This module
only adds, flushes, and queries. It never commits — see
`app.services.practitioner`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.practitioner import Practitioner


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, practitioner_id: uuid.UUID
) -> Practitioner | None:
    """Return the practitioner with `practitioner_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(Practitioner).where(
            Practitioner.organization_id == organization_id,
            Practitioner.id == practitioner_id,
        )
    )
    return result.scalar_one_or_none()


async def list_by_organization(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Practitioner]:
    """Return practitioners belonging to `organization_id`, newest first."""
    result = await session.execute(
        select(Practitioner)
        .where(Practitioner.organization_id == organization_id)
        .order_by(Practitioner.created_at.desc(), Practitioner.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def create(session: AsyncSession, practitioner: Practitioner) -> Practitioner:
    """Add and flush a new `Practitioner`. Does NOT commit — see
    `app.services.practitioner.PractitionerService.create_practitioner`."""
    session.add(practitioner)
    await session.flush()
    return practitioner
