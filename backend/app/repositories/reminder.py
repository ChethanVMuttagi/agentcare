"""Tenant-scoped persistence/query operations for `Reminder`.

Every read here REQUIRES an `organization_id` — same discipline as
every other repository in this codebase (see
`app.repositories.appointment`). This module only adds, flushes, locks,
and queries — it never commits, and it makes no decision about whether a
transition is currently ALLOWED (e.g. "can a `SENT` reminder be
cancelled?") — see `app.services.reminder.ReminderService`, which owns
that decision, the same layering `app.services.workflow.WorkflowService`
already established for `WorkflowRun`/`WorkflowStep`.

`acquire_pending` is the concurrency primitive the whole reminder engine
is built on: a single `SELECT ... FOR UPDATE SKIP LOCKED` (the standard
PostgreSQL job-queue pattern) that ALSO recovers abandoned locks (a
`PROCESSING` reminder whose worker crashed before completing it) in the
SAME query — see docs/adr/ADR-0012-reminder-engine.md "Concurrency
Strategy" and `app.workers.reminder_worker.ReminderWorker`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder import Reminder, ReminderStatus


async def create(session: AsyncSession, reminder: Reminder) -> Reminder:
    """Add and flush a new `Reminder`. Does NOT commit — see
    `app.services.reminder.ReminderService.schedule_reminder`."""
    session.add(reminder)
    await session.flush()
    return reminder


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, reminder_id: uuid.UUID
) -> Reminder | None:
    """Return the reminder with `reminder_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(Reminder).where(
            Reminder.organization_id == organization_id,
            Reminder.id == reminder_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_id_for_update(
    session: AsyncSession, *, organization_id: uuid.UUID, reminder_id: uuid.UUID
) -> Reminder | None:
    """Same as `get_by_id`, but locks the row (`SELECT ... FOR UPDATE`) —
    the primitive `ReminderService` builds single-reminder, race-safe
    transitions (cancel, reschedule, explicit retry) on, mirroring
    `app.repositories.workflow_run.get_by_id_for_update` exactly,
    including its `populate_existing=True` (see that function's
    docstring for why a "for update" read must never return a
    session-cached, stale-attribute copy of an already-loaded row)."""
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.organization_id == organization_id,
            Reminder.id == reminder_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_by_appointment(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    appointment_id: uuid.UUID,
    statuses: Sequence[ReminderStatus] | None = None,
) -> Sequence[Reminder]:
    """Every reminder for one appointment, newest first. `statuses`, when
    given, restricts to those statuses only — used by
    `ReminderService.cancel_reminders_for_appointment` to find only the
    still-cancellable (`PENDING`/`PROCESSING`) reminders when an
    appointment is rescheduled or cancelled, never touching an already
    `SENT`/`CANCELLED` one."""
    stmt = select(Reminder).where(
        Reminder.organization_id == organization_id,
        Reminder.appointment_id == appointment_id,
    )
    if statuses is not None:
        stmt = stmt.where(Reminder.status.in_(statuses))
    result = await session.execute(stmt.order_by(Reminder.created_at.desc(), Reminder.id))
    return result.scalars().all()


async def list_by_patient(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Reminder]:
    """Self-scoped listing for exactly one patient, newest first."""
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.organization_id == organization_id,
            Reminder.patient_id == patient_id,
        )
        .order_by(Reminder.scheduled_at.desc(), Reminder.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def find_expired_locks(
    session: AsyncSession, *, stale_before: datetime, limit: int = 100
) -> Sequence[Reminder]:
    """Read-only diagnostic/observability query: `PROCESSING` reminders
    whose lock is older than `stale_before` — i.e. abandoned by a worker
    that crashed or was killed mid-attempt. Does NOT recover them (no
    mutation, no lock taken) — `acquire_pending` is what actually
    recovers an abandoned lock, as part of its own normal poll. This
    exists as a SEPARATE, side-effect-free way to observe/audit stuck
    work without racing or interfering with a real worker's row lock."""
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.status == ReminderStatus.PROCESSING,
            Reminder.locked_at.is_not(None),
            Reminder.locked_at < stale_before,
        )
        .order_by(Reminder.locked_at)
        .limit(limit)
    )
    return result.scalars().all()


async def acquire_pending(
    session: AsyncSession,
    *,
    now: datetime,
    stale_before: datetime,
    worker_id: str,
    batch_size: int,
) -> Sequence[Reminder]:
    """Atomically claim up to `batch_size` reminders for `worker_id`:
    either a genuinely due `PENDING` reminder (`scheduled_at <= now`), or
    a `PROCESSING` reminder whose lock has expired (`locked_at <
    stale_before` — an abandoned lock, recovered here). Uses
    `SELECT ... FOR UPDATE SKIP LOCKED`: a row already locked by another
    concurrently-running worker's transaction is silently SKIPPED, never
    blocked on — this is what makes it safe to run many worker instances
    (or many concurrent polls) against the SAME table without any of
    them ever claiming the same reminder twice. Sets `status=PROCESSING`,
    `locked_at=now`, `locked_by=worker_id`, and increments
    `attempt_count` for every claimed row, in the SAME transaction as the
    claim. Does NOT commit — the caller (`ReminderService`/
    `ReminderWorker`) must commit immediately after this returns, so the
    claim is durably visible (and the row lock released) before any
    external side effect (e.g. `NotificationProvider.send`) is
    attempted.
    """
    stmt = (
        select(Reminder)
        .where(
            or_(
                and_(
                    Reminder.status == ReminderStatus.PENDING,
                    Reminder.scheduled_at <= now,
                ),
                and_(
                    Reminder.status == ReminderStatus.PROCESSING,
                    Reminder.locked_at.is_not(None),
                    Reminder.locked_at < stale_before,
                ),
            )
        )
        .order_by(Reminder.scheduled_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    claimed = result.scalars().all()
    for reminder in claimed:
        reminder.status = ReminderStatus.PROCESSING
        reminder.locked_at = now
        reminder.locked_by = worker_id
        reminder.attempt_count += 1
    await session.flush()
    return claimed


async def mark_processing(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reminder_id: uuid.UUID,
    worker_id: str,
    locked_at: datetime,
) -> Reminder | None:
    """Single-reminder variant of the claim `acquire_pending` performs in
    batch — used for an explicit, caller-initiated retry
    (`ReminderService.retry_failed`) outside the worker's own poll loop.
    Locks the row, sets `status=PROCESSING`/`locked_at`/`locked_by`, and
    increments `attempt_count`. Returns `None` if no such reminder
    exists in this organization. Does NOT commit."""
    reminder = await get_by_id_for_update(
        session, organization_id=organization_id, reminder_id=reminder_id
    )
    if reminder is None:
        return None
    reminder.status = ReminderStatus.PROCESSING
    reminder.locked_at = locked_at
    reminder.locked_by = worker_id
    reminder.attempt_count += 1
    await session.flush()
    return reminder


async def mark_sent(
    session: AsyncSession, *, organization_id: uuid.UUID, reminder_id: uuid.UUID, sent_at: datetime
) -> Reminder | None:
    """`PROCESSING` -> `SENT`, clearing the lock. Returns `None` if no
    such reminder exists in this organization. Does NOT commit."""
    reminder = await get_by_id_for_update(
        session, organization_id=organization_id, reminder_id=reminder_id
    )
    if reminder is None:
        return None
    reminder.status = ReminderStatus.SENT
    reminder.sent_at = sent_at
    reminder.last_error = None
    reminder.locked_at = None
    reminder.locked_by = None
    await session.flush()
    return reminder


async def mark_failed(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reminder_id: uuid.UUID,
    safe_error: str,
    next_status: ReminderStatus,
) -> Reminder | None:
    """`PROCESSING` -> `next_status` (either `PENDING`, to retry on a
    future poll, or `FAILED`, once attempts are exhausted — the caller,
    `ReminderService`, decides which by comparing `attempt_count` against
    `max_attempts`; this function only applies whichever decision was
    made). Always clears the lock and records `safe_error` as
    `last_error`. Returns `None` if no such reminder exists in this
    organization. Does NOT commit."""
    reminder = await get_by_id_for_update(
        session, organization_id=organization_id, reminder_id=reminder_id
    )
    if reminder is None:
        return None
    reminder.status = next_status
    reminder.last_error = safe_error[:500]
    reminder.locked_at = None
    reminder.locked_by = None
    await session.flush()
    return reminder


async def cancel(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    reminder_id: uuid.UUID,
    cancelled_at: datetime,
) -> Reminder | None:
    """`PENDING`/`PROCESSING` -> `CANCELLED`, clearing the lock. Returns
    `None` if no such reminder exists in this organization. Does NOT
    validate that the CURRENT status is actually cancellable — see
    `ReminderService.cancel_reminder`, which checks that before ever
    calling this. Does NOT commit."""
    reminder = await get_by_id_for_update(
        session, organization_id=organization_id, reminder_id=reminder_id
    )
    if reminder is None:
        return None
    reminder.status = ReminderStatus.CANCELLED
    reminder.cancelled_at = cancelled_at
    reminder.locked_at = None
    reminder.locked_by = None
    await session.flush()
    return reminder


async def update(session: AsyncSession, reminder: Reminder) -> Reminder:
    """Flush an already-fetched-and-mutated `Reminder` (e.g.
    `ReminderService.reschedule_reminder` changing `scheduled_at`). The
    object must already be attached to `session` (typically via
    `get_by_id_for_update`) — this function performs no lookup of its
    own. Does NOT commit."""
    await session.flush()
    return reminder
