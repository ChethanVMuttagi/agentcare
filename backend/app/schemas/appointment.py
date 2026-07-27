"""Appointment API schemas.

Never returns SQLAlchemy models directly. `AppointmentResponse` exposes
only administrative scheduling fields — see docs/APPOINTMENTS.md. No
diagnosis, symptoms, treatment, medication, or clinical-note field exists
anywhere on `Appointment` in the first place (see
`app/models/appointment.py`), so there is nothing clinical to
accidentally expose here either.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.appointment import AppointmentStatus
from app.services.availability_query import (
    MAX_APPOINTMENT_DURATION_MINUTES,
    MIN_APPOINTMENT_DURATION_MINUTES,
)

CANCELLATION_REASON_MAX_LENGTH = 500


def _require_timezone_aware(value: datetime) -> datetime:
    # Also re-validated at the service layer (a naive datetime is never
    # accepted for comparison against `datetime.now(UTC)` or persistence)
    # — deliberate defense-in-depth, matching this codebase's established
    # pattern (see app/schemas/availability.py).
    if value.tzinfo is None:
        raise ValueError("must include timezone information (e.g. a UTC offset).")
    return value


class AppointmentCreate(BaseModel):
    """Request body for `POST /organizations/{organization_id}/appointments`.

    `patient_id` is used ONLY for an `ADMIN`/`STAFF` caller booking on
    behalf of a patient. A `PATIENT`-role caller's `patient_id` is always
    derived server-side from their own linked `Patient` record and this
    field is ignored for them entirely — see
    `app.api.v1.endpoints.appointments` and docs/APPOINTMENTS.md "RBAC":
    a patient can never supply another patient's id to act on their
    behalf.
    """

    patient_id: uuid.UUID | None = None
    practitioner_id: uuid.UUID
    department_id: uuid.UUID
    start_at: datetime
    duration_minutes: int = Field(
        ge=MIN_APPOINTMENT_DURATION_MINUTES, le=MAX_APPOINTMENT_DURATION_MINUTES
    )

    @field_validator("start_at")
    @classmethod
    def _start_at_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class AppointmentRescheduleRequest(BaseModel):
    """Request body for
    `PATCH /organizations/{organization_id}/appointments/{appointment_id}/reschedule`."""

    start_at: datetime
    duration_minutes: int = Field(
        ge=MIN_APPOINTMENT_DURATION_MINUTES, le=MAX_APPOINTMENT_DURATION_MINUTES
    )

    @field_validator("start_at")
    @classmethod
    def _start_at_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class AppointmentCancelRequest(BaseModel):
    """Request body for
    `POST /organizations/{organization_id}/appointments/{appointment_id}/cancel`.

    `cancellation_reason` is a short ADMINISTRATIVE reason only (e.g.
    "patient requested", "practitioner unavailable") — never clinical
    content. See docs/APPOINTMENTS.md "Cancellation"."""

    cancellation_reason: str | None = Field(default=None, max_length=CANCELLATION_REASON_MAX_LENGTH)


class AppointmentResponse(BaseModel):
    """An appointment, as returned by the API. Administrative fields only."""

    id: uuid.UUID
    organization_id: uuid.UUID
    patient_id: uuid.UUID
    practitioner_id: uuid.UUID
    department_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AppointmentListResponse(BaseModel):
    """`GET /organizations/{organization_id}/appointments` response envelope."""

    appointments: list[AppointmentResponse]


class AvailableTimeSlotResponse(BaseModel):
    """One concrete, bookable candidate time. Never reveals WHY any other
    time is unavailable — see docs/APPOINTMENTS.md "Availability
    Privacy": only free times are returned, never details of any
    appointment (another patient's or otherwise) that blocks a time."""

    start_at: datetime
    end_at: datetime


class AvailableTimesResponse(BaseModel):
    """`GET .../practitioners/{practitioner_id}/available-times` response envelope."""

    available_times: list[AvailableTimeSlotResponse]
