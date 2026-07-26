"""Patient API schemas.

Never returns SQLAlchemy models directly. `PatientResponse` exposes only
administrative fields — no relationship objects (`organization`, `user`),
and, per this story's healthcare safety boundary, no medical/clinical
fields exist to accidentally expose in the first place (see
docs/PATIENTS.md).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class PatientCreate(BaseModel):
    """Request body for `POST /organizations/{organization_id}/patients`."""

    patient_number: str = Field(min_length=1, max_length=64)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    date_of_birth: date
    user_id: uuid.UUID | None = None

    @field_validator("date_of_birth")
    @classmethod
    def _date_of_birth_not_in_future(cls, value: date) -> date:
        # Also re-validated at the model layer (app/models/patient.py) —
        # deliberate defense-in-depth, not redundancy: this gives a clean
        # 422 for the common case (a real API caller), while the model-level
        # check protects any other code path that might construct a
        # `Patient` directly.
        if value > date.today():
            raise ValueError("date_of_birth must not be in the future.")
        return value


class PatientResponse(BaseModel):
    """A patient record, as returned by the API. Administrative fields only."""

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    patient_number: str
    first_name: str
    last_name: str
    date_of_birth: date
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PatientListResponse(BaseModel):
    """`GET /organizations/{organization_id}/patients` response envelope."""

    patients: list[PatientResponse]
