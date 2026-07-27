"""AvailabilityService: recurring practitioner availability business rules.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005. Transaction ownership: `create_availability`
commits only after every check passes; repositories only ever add/flush.

Concurrency limitation (see docs/SCHEDULING_RESOURCES.md "Overlapping
Availability" for the full discussion): overlap rejection here is a
SERVICE-LEVEL pre-check (query existing active windows, compare in
Python, then insert) — it is NOT race-proof. Two concurrent requests
creating overlapping windows for the same organization/practitioner/
department/day could both pass the pre-check before either commits, and
both succeed, producing an overlap the database itself does not reject
(there is no exclusion constraint here). This is a deliberate, documented
scope boundary for this story, not an oversight — see the ADR.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import time
from zoneinfo import available_timezones

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.repositories import availability as availability_repository
from app.repositories import practitioner_department as practitioner_department_repository


class PractitionerNotAssignedError(AppException):
    """422: the practitioner is not (or no longer) actively assigned to
    this department, so availability cannot be created for this pairing."""

    status_code = 422
    error_code = "practitioner_not_assigned"


class InvalidAvailabilityTimeRangeError(AppException):
    """422: `start_time` must be strictly before `end_time`."""

    status_code = 422
    error_code = "invalid_availability_time_range"


class InvalidAvailabilityTimezoneError(AppException):
    """422: `timezone` is not a valid IANA timezone identifier."""

    status_code = 422
    error_code = "invalid_availability_timezone"


class AvailabilityOverlapError(AppException):
    """409: this window overlaps an existing ACTIVE window for the same
    organization/practitioner/department/day. See the module docstring
    for the concurrency limitation of this check."""

    status_code = 409
    error_code = "availability_overlap"


def _windows_overlap(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    """True iff `[start_a, end_a)` and `[start_b, end_b)` overlap.

    Adjacent windows (one ends exactly when the other starts) do NOT
    overlap — see docs/SCHEDULING_RESOURCES.md "Overlapping Availability".
    """
    return start_a < end_b and start_b < end_a


class AvailabilityService:
    """Recurring availability business rules, scoped to one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_availability(
        self,
        *,
        organization_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
        day_of_week: DayOfWeek,
        start_time: time,
        end_time: time,
        timezone: str,
    ) -> PractitionerAvailability:
        """Create a recurring availability window, committing only once
        every check passes.

        Validation order: practitioner-department assignment (must exist
        and be active), time range, timezone, then overlap against
        existing active windows for the same organization/practitioner/
        department/day. See the module docstring for the overlap check's
        documented concurrency limitation.
        """
        assignment = await practitioner_department_repository.get_assignment(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        if assignment is None or not assignment.is_active:
            raise PractitionerNotAssignedError(
                "This practitioner is not actively assigned to this department."
            )

        if not start_time < end_time:
            raise InvalidAvailabilityTimeRangeError("start_time must be before end_time.")

        if timezone not in available_timezones():
            raise InvalidAvailabilityTimezoneError(
                f"{timezone!r} is not a valid IANA timezone identifier."
            )

        existing_windows = await availability_repository.list_active_by_practitioner_department_day(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
            day_of_week=day_of_week,
        )
        for window in existing_windows:
            if _windows_overlap(start_time, end_time, window.start_time, window.end_time):
                raise AvailabilityOverlapError(
                    "This availability window overlaps an existing active window."
                )

        availability = PractitionerAvailability(
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            timezone=timezone,
        )
        await availability_repository.create(self._session, availability)
        await self._session.commit()
        return availability

    async def list_availability(
        self,
        *,
        organization_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[PractitionerAvailability]:
        """Tenant-scoped listing of a practitioner's availability windows."""
        return await availability_repository.list_by_practitioner(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            limit=limit,
            offset=offset,
        )
