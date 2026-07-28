"""ReminderWorker: durable, restart-safe, batch reminder delivery.

Every unit of work is a `Reminder` row in PostgreSQL — never an
in-memory queue, never a Python list, never state that only exists
inside this process. `run_once()` is a single, complete poll cycle:
claim a batch (`app.repositories.reminder.acquire_pending`, `SELECT
... FOR UPDATE SKIP LOCKED`), then process each claimed reminder in its
OWN, separate database transaction — so one reminder's failure (or even
an unexpected crash mid-attempt) can never corrupt or block another's
processing, and a process restart between any two reminders loses
nothing: every claimed-but-not-yet-completed reminder is simply picked
up again, either immediately (if this exact worker restarts fast) or by
ANY worker once its lock goes stale (see
`app.repositories.reminder.acquire_pending`'s stale-lock recovery).

`run_once()` is what tests call directly (deterministic: process
exactly the reminders currently due, then return). `run_forever()` is a
thin polling loop around it, intended for `app.main`'s lifespan-managed
background task (see `docs/adr/ADR-0012-reminder-engine.md` "Worker
Deployment") — NOT exercised by the test suite, which never needs an
actual sleep-based loop to prove correctness.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.reminder import Reminder, ReminderAttempt, ReminderAttemptStatus
from app.notifications.base import NotificationMessage, NotificationProvider, NotificationResult
from app.repositories import appointment as appointment_repository
from app.repositories import reminder as reminder_repository
from app.repositories import reminder_attempt as reminder_attempt_repository
from app.services.reminder import (
    InvalidReminderTransitionError,
    ReminderNotFoundError,
    ReminderService,
)

logger = logging.getLogger("agentcare.workers.reminder")

_DEFAULT_BATCH_SIZE = 20
_DEFAULT_LOCK_TIMEOUT = timedelta(minutes=5)
_DEFAULT_POLL_INTERVAL_SECONDS = 30.0
_SAFE_ERROR_FALLBACK = "delivery failed"
_UNEXPECTED_ERROR_DETAIL = "unexpected provider error"


def _new_worker_id() -> str:
    return f"reminder-worker-{uuid.uuid4().hex[:16]}"


class ReminderWorker:
    """Scoped to a `async_sessionmaker` (NOT a single shared `AsyncSession`)
    — every claim and every per-reminder processing step opens its OWN
    session/transaction, exactly mirroring
    `tests/db/test_workflow_concurrency.py`'s "genuinely independent
    connections" proof pattern. This is what makes concurrent
    `ReminderWorker` instances (or repeated polls of the same one) safe
    against each other."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        notification_provider: NotificationProvider,
        *,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        lock_timeout: timedelta = _DEFAULT_LOCK_TIMEOUT,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        worker_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._notification_provider = notification_provider
        self._batch_size = batch_size
        self._lock_timeout = lock_timeout
        self._poll_interval_seconds = poll_interval_seconds
        self.worker_id = worker_id or _new_worker_id()

    async def run_once(self) -> int:
        """Claim and fully process one batch (up to `batch_size`
        reminders). Returns how many were claimed. Safe to call
        repeatedly/concurrently — see the class docstring."""
        claimed = await self._acquire_batch()
        for organization_id, reminder_id in claimed:
            await self._process_one(organization_id, reminder_id)
        return len(claimed)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Poll forever, `poll_interval_seconds` apart, until
        `stop_event` is set. A single poll cycle raising an unexpected
        exception is logged and swallowed — never lets one bad cycle
        kill the loop (mirrors `run_once`'s own per-reminder isolation,
        one level up). NOT exercised by the test suite (see the module
        docstring) — `app.main`'s lifespan wiring is the only real
        caller."""
        while stop_event is None or not stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("reminder worker poll cycle failed unexpectedly")
            if stop_event is None:
                await asyncio.sleep(self._poll_interval_seconds)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval_seconds)

    async def _acquire_batch(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self._session_factory() as session:
            now = datetime.now(UTC)
            stale_before = now - self._lock_timeout
            claimed = await reminder_repository.acquire_pending(
                session,
                now=now,
                stale_before=stale_before,
                worker_id=self.worker_id,
                batch_size=self._batch_size,
            )
            await session.commit()
            return [(reminder.organization_id, reminder.id) for reminder in claimed]

    async def _process_one(self, organization_id: uuid.UUID, reminder_id: uuid.UUID) -> None:
        """Process exactly one already-claimed reminder, in its own
        session. Never lets one reminder's failure propagate and abort
        the batch — every exception path here is caught, logged, and
        contained to this single reminder."""
        async with self._session_factory() as session:
            reminder_service = ReminderService(session)
            try:
                await reminder_service.mark_started(
                    organization_id=organization_id,
                    reminder_id=reminder_id,
                    actor_identifier=self.worker_id,
                )
            except (ReminderNotFoundError, InvalidReminderTransitionError):
                # Concurrently cancelled (or otherwise no longer in a
                # startable state) between acquisition and this call —
                # see `ReminderService.mark_started`'s own docstring.
                # Correctly abort WITHOUT ever calling the notification
                # provider for it.
                logger.info(
                    "reminder %s no longer startable at delivery time; skipping", reminder_id
                )
                return

            reminder = await reminder_service.get_reminder(
                organization_id=organization_id, reminder_id=reminder_id
            )
            appointment_start_at = await self._resolve_appointment_start_at(
                session, organization_id=organization_id, reminder=reminder
            )
            message = NotificationMessage(
                reminder_id=reminder.id,
                organization_id=reminder.organization_id,
                patient_id=reminder.patient_id,
                appointment_id=reminder.appointment_id,
                reminder_type=reminder.reminder_type,
                appointment_start_at=appointment_start_at,
            )
            attempt_number = reminder.attempt_count
            started_at = datetime.now(UTC)
            try:
                result = await self._notification_provider.send(message)
            except Exception:  # noqa: BLE001 — last-resort safety net, see NotificationProvider
                logger.exception(
                    "notification provider raised unexpectedly for reminder %s", reminder_id
                )
                result = NotificationResult(
                    success=False,
                    provider_name=self._notification_provider.provider_name,
                    safe_detail=_UNEXPECTED_ERROR_DETAIL,
                )
            completed_at = datetime.now(UTC)

            await reminder_attempt_repository.create(
                session,
                ReminderAttempt(
                    organization_id=organization_id,
                    reminder_id=reminder_id,
                    attempt_number=attempt_number,
                    status=ReminderAttemptStatus.SENT
                    if result.success
                    else ReminderAttemptStatus.FAILED,
                    provider_name=result.provider_name,
                    safe_error_message=None
                    if result.success
                    else (result.safe_detail or _SAFE_ERROR_FALLBACK)[:500],
                    started_at=started_at,
                    completed_at=completed_at,
                ),
            )
            await session.commit()

            if result.success:
                await reminder_service.mark_sent(
                    organization_id=organization_id,
                    reminder_id=reminder_id,
                    actor_identifier=self.worker_id,
                )
            else:
                await reminder_service.mark_failed(
                    organization_id=organization_id,
                    reminder_id=reminder_id,
                    safe_error=result.safe_detail or _SAFE_ERROR_FALLBACK,
                    actor_identifier=self.worker_id,
                )

    @staticmethod
    async def _resolve_appointment_start_at(
        session: AsyncSession, *, organization_id: uuid.UUID, reminder: Reminder
    ) -> datetime:
        """Prefer the timestamp captured in `reminder.payload` at
        schedule time (no extra query); fall back to a fresh
        `Appointment` lookup if it is missing or unparsable — defensive,
        not load-bearing, since `ReminderScheduler` always populates it
        for the one reminder type this story schedules automatically."""
        payload = reminder.payload
        if isinstance(payload, dict):
            raw = payload.get("appointment_start_at")
            if isinstance(raw, str):
                try:
                    return datetime.fromisoformat(raw)
                except ValueError:
                    pass

        appointment = await appointment_repository.get_by_id(
            session, organization_id=organization_id, appointment_id=reminder.appointment_id
        )
        if appointment is not None:
            return appointment.start_at
        # Should be unreachable (the composite FK guarantees the
        # appointment exists) — a safe, timezone-aware fallback rather
        # than raising out of a batch-processing loop for one reminder.
        return datetime.now(UTC)
