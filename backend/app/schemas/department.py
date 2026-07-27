"""Department API schemas.

Never returns SQLAlchemy models directly — see docs/SCHEDULING_RESOURCES.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DepartmentCreate(BaseModel):
    """Request body for `POST /organizations/{organization_id}/departments`."""

    facility_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=50)


class DepartmentResponse(BaseModel):
    """A department record, as returned by the API."""

    id: uuid.UUID
    organization_id: uuid.UUID
    facility_id: uuid.UUID
    name: str
    code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DepartmentListResponse(BaseModel):
    """`GET /organizations/{organization_id}/departments` response envelope."""

    departments: list[DepartmentResponse]
