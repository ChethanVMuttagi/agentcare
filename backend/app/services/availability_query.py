"""AvailabilityQueryService: turns recurring availability into concrete times.

`PractitionerAvailability` (STORY-006) represents a recurring WEEKLY rule
("Monday 09:00-12:00 in Cardiology, Asia/Kolkata"), not a materialized,
bookable calendar slot. This service is the one place that converts that
recurring rule, for one specific calendar date, into concrete UTC instants
— and is also the one place `AppointmentService` (booking/rescheduling)
asks "does this specific proposed time actually fall inside an active
availability window?" before ever attempting to persist an `Appointment`.

Deliberately does NOT materialize appointment-slot rows anywhere — slots
are computed on demand, on every call. See docs/APPOINTMENTS.md
"Availability Calculation" and docs/adr/ADR-0007-appointment-concurrency.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import AppointmentStatus
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.repositories import appointment as appointment_repository
from app.repositories import availability as availability_repository
from app.repositories import department as department_repository
from app.repositories import practitioner as practitioner_repository
from app.repositories import practitioner_department as practitioner_department_repository

# A candidate appointment start time is offered every N minutes within an
# active availability window. Not client-configurable in this story — a
# single, deliberate default keeps the available-times response shape
# simple and predictable. See docs/APPOINTMENTS.md "Slot Interval".
DEFAULT_SLOT_INTERVAL_MINUTES = 15

# Bounds on a bookable appointment's administrative duration. Not tied to
# any one medical duration — the caller supplies `duration_minutes`,
# bounded here to a sane administrative range. See
# docs/APPOINTMENTS.md "Duration".
MIN_APPOINTMENT_DURATION_MINUTES = 15
MAX_APPOINTMENT_DURATION_MINUTES = 240


@dataclass(frozen=True)
class AvailableSlot:
    """One concrete, bookable candidate time — not a persisted row."""

    start_at: datetime
    end_at: datetime


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Standard half-open interval overlap test — same rule used by
    `app.services.availability._windows_overlap` and enforced at the
    database level for `Appointment` via the EXCLUDE constraints."""
    return a_start < b_end and b_start < a_end


def _window_local_bounds(
    window: PractitionerAvailability, on_date: date
) -> tuple[datetime, datetime]:
    """Return this window's `[start, end)` as UTC-aware instants, for the
    local calendar date `on_date` in the window's OWN timezone.

    Uses `zoneinfo` so DST transitions on `on_date` are resolved
    correctly for that specific date/timezone, rather than assuming a
    fixed UTC offset — see docs/APPOINTMENTS.md "Timezone Semantics".
    """
    local_tz = ZoneInfo(window.timezone)
    start_local = datetime.combine(on_date, window.start_time, tzinfo=local_tz)
    end_local = datetime.combine(on_date, window.end_time, tzinfo=local_tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


class AvailabilityQueryService:
    """Read-only availability calculation, scoped to one `AsyncSession`.

    Performs no mutation and owns no transaction — it only reads
    `PractitionerAvailability` and `Appointment` rows already committed
    (or flushed) in the current session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _resources_bookable(
        self,
        *,
        organization_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
    ) -> bool:
        """`True` only if the practitioner exists and is active, the
        department exists and is active, AND an ACTIVE assignment links
        them. Callers (see `app.api.v1.endpoints.appointments`) are
        expected to have already resolved/404'd the practitioner and
        department for EXISTENCE; this additionally checks the mutable
        `is_active` state every one of these requires — none of which a
        composite foreign key can express (see
        `app/models/practitioner_availability.py` for the identical
        existence-vs-activity split)."""
        practitioner = await practitioner_repository.get_by_id(
            self._session, organization_id=organization_id, practitioner_id=practitioner_id
        )
        if practitioner is None or not practitioner.is_active:
            return False

        department = await department_repository.get_by_id(
            self._session, organization_id=organization_id, department_id=department_id
        )
        if department is None or not department.is_active:
            return False

        assignment = await practitioner_department_repository.get_assignment(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        return assignment is not None and assignment.is_active

    async def list_available_times(
        self,
        *,
        organization_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
        on_date: date,
        duration_minutes: int,
        slot_interval_minutes: int = DEFAULT_SLOT_INTERVAL_MINUTES,
    ) -> list[AvailableSlot]:
        """Candidate start times on `on_date` that could accommodate a
        `duration_minutes`-long appointment.

        Requires an ACTIVE practitioner, ACTIVE department, and ACTIVE
        assignment between them (see `_resources_bookable`) — any one of
        these being inactive yields an empty list, not an error: "no
        bookable time" and "resource temporarily unschedulable" are the
        same observable outcome to a caller, by design (see
        docs/APPOINTMENTS.md "Availability Privacy"). Existence checks
        (404 for a truly unknown practitioner/department) remain the
        caller's job (see `app.api.v1.endpoints.appointments`), the same
        pattern `PractitionerService`/`AvailabilityService` already
        establish.
        """
        if not await self._resources_bookable(
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        ):
            return []

        windows = await availability_repository.list_active_by_practitioner_department(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        duration = timedelta(minutes=duration_minutes)
        step = timedelta(minutes=slot_interval_minutes)
        now = datetime.now(UTC)

        slots: list[AvailableSlot] = []
        for window in windows:
            if list(DayOfWeek).index(window.day_of_week) != on_date.weekday():
                continue
            window_start_utc, window_end_utc = _window_local_bounds(window, on_date)

            existing = await appointment_repository.list_practitioner_appointments_in_range(
                self._session,
                organization_id=organization_id,
                practitioner_id=practitioner_id,
                range_start=window_start_utc,
                range_end=window_end_utc,
                statuses=(AppointmentStatus.BOOKED,),
            )

            cursor = window_start_utc
            while cursor + duration <= window_end_utc:
                slot_end = cursor + duration
                if cursor >= now and not any(
                    _overlaps(cursor, slot_end, appt.start_at, appt.end_at) for appt in existing
                ):
                    slots.append(AvailableSlot(start_at=cursor, end_at=slot_end))
                cursor += step

        slots.sort(key=lambda slot: slot.start_at)
        return slots

    async def is_within_availability(
        self,
        *,
        organization_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> bool:
        """Does `[start_at, end_at)` fall ENTIRELY inside one active
        availability window for this practitioner/department?

        Each active window is checked in ITS OWN timezone (not the
        caller's, not UTC) — a window's `day_of_week`/`start_time`/
        `end_time` are local, wall-clock concepts (see
        `app/models/practitioner_availability.py`). `start_at`/`end_at`
        are converted into each window's local time independently, so
        windows with different timezones for the same practitioner are
        each evaluated correctly. Does not consider existing
        appointments — that is `Appointment`'s exclusion constraint's
        job (see `app.services.appointment.AppointmentService`), not
        this method's.
        """
        windows = await availability_repository.list_active_by_practitioner_department(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        for window in windows:
            local_tz = ZoneInfo(window.timezone)
            start_local = start_at.astimezone(local_tz)
            end_local = end_at.astimezone(local_tz)
            if start_local.date() != end_local.date():
                # An appointment that crosses midnight in this window's
                # local timezone can never fit inside one single-day
                # recurring window — see docs/APPOINTMENTS.md "Known
                # Limitations".
                continue
            if list(DayOfWeek).index(window.day_of_week) != start_local.weekday():
                continue
            if window.start_time <= start_local.time() and end_local.time() <= window.end_time:
                return True
        return False
