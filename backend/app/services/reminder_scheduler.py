"""ReminderScheduler: the "when should a reminder fire" POLICY layer,
scoped to one `AsyncSession`.

Deliberately separate from `app.services.reminder.ReminderService`:
`ReminderService` owns the reminder STATE MACHINE and persistence
(schedule/cancel/reschedule/retry/mark_*, all reusable regardless of WHY
a reminder exists); `ReminderScheduler` owns the ADMINISTRATIVE POLICY
of "given this appointment event, what reminder(s) should exist, and
when" — e.g. the lead time before an appointment's start. This mirrors
the same separation `app.ai.agents` (WHICH specialist should handle a
request) already keeps distinct from `app.ai.tools` (WHAT a specialist
is allowed to actually do).

`app.services.appointment.AppointmentService` is the only caller —
constructs one internally exactly like it already constructs
`AvailabilityQueryService`, and calls it after a booking/reschedule/
cancellation genuinely commits. See docs/adr/ADR-0012-reminder-engine.md
"Automatic Scheduling".
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.reminder import Reminder, ReminderType
from app.models.workflow import ActorType
from app.services.reminder import ReminderService

# 24 hours before the appointment's own start time — a fixed, documented
# administrative policy. Not user-configurable in this story (see
# docs/adr/ADR-0012-reminder-engine.md "Current vs. Planned"): a future
# story could make this a per-organization or per-reminder-type setting
# without changing `ReminderService`'s contract at all.
DEFAULT_LEAD_TIME = timedelta(hours=24)


class ReminderScheduler:
    """Appointment-lifecycle-driven reminder scheduling policy."""

    def __init__(self, session: AsyncSession, *, lead_time: timedelta = DEFAULT_LEAD_TIME) -> None:
        self._reminder_service = ReminderService(session)
        self._lead_time = lead_time

    async def schedule_appointment_reminder(
        self, appointment: Appointment, *, initiated_by_user_id: uuid.UUID
    ) -> Reminder:
        """Schedule one `APPOINTMENT_REMINDER` for a just-booked (or
        just-rescheduled-to-a-new-time) appointment, `lead_time` before
        its `start_at`. If that computed time has already passed (e.g.
        the appointment starts sooner than `lead_time` from now), the
        reminder is still created — scheduled in the near past — so
        `app.workers.reminder_worker.ReminderWorker` picks it up and
        delivers it on its very next poll, rather than silently never
        creating a reminder at all for a soon-starting appointment.
        `payload` carries ONLY the appointment's `start_at` (a
        timestamp, not PHI by itself — see
        `app.notifications.base.NotificationMessage`'s identical
        discipline)."""
        scheduled_at = appointment.start_at - self._lead_time
        return await self._reminder_service.schedule_reminder(
            organization_id=appointment.organization_id,
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            reminder_type=ReminderType.APPOINTMENT_REMINDER,
            scheduled_at=scheduled_at,
            initiated_by_user_id=initiated_by_user_id,
            payload={"appointment_start_at": appointment.start_at.isoformat()},
        )

    async def cancel_appointment_reminders(
        self, appointment: Appointment, *, initiated_by_user_id: uuid.UUID
    ) -> list[Reminder]:
        """Cancel every still-cancellable reminder for `appointment` —
        called on both appointment cancellation and (as the first half
        of "cancel old, schedule new") appointment rescheduling. The
        acting user's identity is attributed on the resulting
        `WORKFLOW_CANCELLED`/`REMINDER_CANCELLED` audit trail, exactly
        like a human-initiated cancellation would be."""
        return await self._reminder_service.cancel_reminders_for_appointment(
            organization_id=appointment.organization_id,
            appointment_id=appointment.id,
            actor_type=ActorType.USER,
            actor_identifier=str(initiated_by_user_id),
        )
