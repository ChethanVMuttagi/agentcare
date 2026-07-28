"""Tenant-scoped persistence/query operations for `ReminderAttempt`.

Append-only, mirroring `app.repositories.workflow_event` exactly: `create`
and read functions only — no `update`. See
`app.models.reminder.ReminderAttempt` for why no mutation function exists
or should ever be added.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder import ReminderAttempt


async def create(session: AsyncSession, attempt: ReminderAttempt) -> ReminderAttempt:
    """Add and flush a new `ReminderAttempt`. Does NOT commit — see
    `app.services.reminder.ReminderService`."""
    session.add(attempt)
    await session.flush()
    return attempt


async def list_by_reminder(
    session: AsyncSession, *, organization_id: uuid.UUID, reminder_id: uuid.UUID
) -> Sequence[ReminderAttempt]:
    """Every attempt for one reminder, in attempt order (oldest first) —
    `attempt_number` is the authoritative, tie-free ordering key (see the
    model docstring), not `created_at`."""
    result = await session.execute(
        select(ReminderAttempt)
        .where(
            ReminderAttempt.organization_id == organization_id,
            ReminderAttempt.reminder_id == reminder_id,
        )
        .order_by(ReminderAttempt.attempt_number)
    )
    return result.scalars().all()
