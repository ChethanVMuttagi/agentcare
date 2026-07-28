"""Tenant-scoped persistence/query operations for `Department`.

Every read here REQUIRES an `organization_id` — same discipline as
`app.repositories.patient` (see docs/PATIENTS.md "Tenant Ownership" and
docs/SCHEDULING_RESOURCES.md). This module only adds, flushes, and
queries. It never commits — see `app.services.department`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, department_id: uuid.UUID
) -> Department | None:
    """Return the department with `department_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(Department).where(
            Department.organization_id == organization_id,
            Department.id == department_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_facility_and_code(
    session: AsyncSession, *, organization_id: uuid.UUID, facility_id: uuid.UUID, code: str
) -> Department | None:
    """Return the department numbered `code` within `facility_id`, if any.

    Used for the pre-create conflict check in `app.services.department`
    (`uq_departments_facility_id_code` is the real, race-safe
    enforcement; this gives a clean error on the common, non-racing path).
    """
    result = await session.execute(
        select(Department).where(
            Department.organization_id == organization_id,
            Department.facility_id == facility_id,
            Department.code == code,
        )
    )
    return result.scalar_one_or_none()


async def search_by_name(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name_query: str,
    limit: int = 10,
) -> Sequence[Department]:
    """Case-insensitive substring match against `Department.name`,
    restricted to `is_active` departments, scoped to `organization_id`.

    Added in STORY-011 for `app.ai.tools.routing_tools.resolve_department`
    — no existing repository function here supported name-based lookup
    (only `get_by_id`/`get_by_facility_and_code`/`list_by_organization`
    did). Returns every match up to `limit`; the caller decides what to
    do with more than one (see `resolve_department`'s "ambiguous match"
    handling — this function itself never guesses).
    """
    result = await session.execute(
        select(Department)
        .where(
            Department.organization_id == organization_id,
            Department.is_active.is_(True),
            Department.name.ilike(f"%{name_query}%"),
        )
        .order_by(Department.name)
        .limit(limit)
    )
    return result.scalars().all()


async def list_by_organization(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Department]:
    """Return departments belonging to `organization_id`, newest first."""
    result = await session.execute(
        select(Department)
        .where(Department.organization_id == organization_id)
        .order_by(Department.created_at.desc(), Department.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def create(session: AsyncSession, department: Department) -> Department:
    """Add and flush a new `Department`. Does NOT commit — see
    `app.services.department.DepartmentService.create_department`."""
    session.add(department)
    await session.flush()
    return department
