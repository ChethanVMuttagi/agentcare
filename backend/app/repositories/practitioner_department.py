"""Tenant-scoped persistence/query operations for `PractitionerDepartment`.

Every read here REQUIRES an `organization_id`. This module only adds,
flushes, and queries. It never commits — see `app.services.practitioner`
(assignment) and `app.services.availability` (assignment-validity check).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.practitioner_department import PractitionerDepartment


async def get_assignment(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    department_id: uuid.UUID,
) -> PractitionerDepartment | None:
    """Return the assignment of `practitioner_id` to `department_id` within
    `organization_id`, if any (regardless of `is_active`) — callers that
    care about active-only assignment (e.g. availability creation) check
    `.is_active` themselves; see `app.services.availability`."""
    result = await session.execute(
        select(PractitionerDepartment).where(
            PractitionerDepartment.organization_id == organization_id,
            PractitionerDepartment.practitioner_id == practitioner_id,
            PractitionerDepartment.department_id == department_id,
        )
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession, assignment: PractitionerDepartment
) -> PractitionerDepartment:
    """Add and flush a new `PractitionerDepartment`. Does NOT commit — see
    `app.services.practitioner.PractitionerService.assign_to_department`."""
    session.add(assignment)
    await session.flush()
    return assignment
