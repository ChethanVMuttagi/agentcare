"""Authentication API schemas.

Never returns SQLAlchemy models directly — `CurrentUserResponse` is the
only representation of `User` ever sent over the API, and it deliberately
excludes `password_hash` and any other internal DB state. Password
fields are write-only by construction: `TokenRequest.password` is
accepted as input but no response schema in this module ever echoes a
password or password hash back.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class TokenRequest(BaseModel):
    """Login request body for `POST /api/v1/auth/token`."""

    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """A newly issued access token."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"


class CurrentUserResponse(BaseModel):
    """The authenticated user's own profile — `GET /api/v1/auth/me`.

    Never includes `password_hash`.
    """

    id: uuid.UUID
    email: str
    is_active: bool
    created_at: datetime
