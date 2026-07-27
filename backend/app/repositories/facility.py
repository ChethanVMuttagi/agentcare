"""Tenant-scoped read access to `Facility`.

Minimal by design: STORY-006 only needs to look up a facility to validate
`Department` ownership (`app.services.department`) — there is still no
CRUD API or full repository for `Facility` itself (see
docs/DOMAIN_MODEL.md).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.facility import Facility


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, facility_id: uuid.UUID
) -> Facility | None:
    """Return the facility with `facility_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(Facility).where(
            Facility.organization_id == organization_id,
            Facility.id == facility_id,
        )
    )
    return result.scalar_one_or_none()
