"""Practitioner API schemas.

Never returns SQLAlchemy models directly. `PractitionerResponse`
exposes only scheduling-relevant administrative fields — no private
email, phone, home address, `User` identity, internal credentials, or
employment information, because none of those fields exist on the
`Practitioner` model in the first place (see
docs/SCHEDULING_RESOURCES.md "Safe Practitioner Response").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.practitioner import PractitionerType


class PractitionerCreate(BaseModel):
    """Request body for `POST /organizations/{organization_id}/practitioners`."""

    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    practitioner_type: PractitionerType


class PractitionerResponse(BaseModel):
    """A practitioner record, as returned by the API. Safe/scheduling
    fields only — see the module docstring."""

    id: uuid.UUID
    organization_id: uuid.UUID
    first_name: str
    last_name: str
    practitioner_type: PractitionerType
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PractitionerListResponse(BaseModel):
    """`GET /organizations/{organization_id}/practitioners` response envelope."""

    practitioners: list[PractitionerResponse]


class PractitionerDepartmentResponse(BaseModel):
    """A practitioner-department assignment, as returned by the API."""

    id: uuid.UUID
    organization_id: uuid.UUID
    practitioner_id: uuid.UUID
    department_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
