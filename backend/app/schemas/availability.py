"""PractitionerAvailability API schemas.

Never returns SQLAlchemy models directly. See docs/SCHEDULING_RESOURCES.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import BaseModel, Field, model_validator

from app.models.practitioner_availability import DayOfWeek


class AvailabilityCreate(BaseModel):
    """Request body for
    `POST /organizations/{organization_id}/practitioners/{practitioner_id}/availability`."""

    department_id: uuid.UUID
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _start_before_end(self) -> AvailabilityCreate:
        # Also re-validated at the service layer
        # (app.services.availability) and enforced at the database layer
        # (a real CHECK constraint) — deliberate defense-in-depth, not
        # redundancy: this gives a clean 422 for the common case (a real
        # API caller), while the deeper layers protect any other code
        # path.
        if not self.start_time < self.end_time:
            raise ValueError("start_time must be before end_time.")
        return self


class AvailabilityResponse(BaseModel):
    """A recurring availability window, as returned by the API."""

    id: uuid.UUID
    organization_id: uuid.UUID
    practitioner_id: uuid.UUID
    department_id: uuid.UUID
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    timezone: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AvailabilityListResponse(BaseModel):
    """Availability-listing response envelope."""

    availability: list[AvailabilityResponse]
