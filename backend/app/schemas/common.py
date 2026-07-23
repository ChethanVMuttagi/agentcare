"""Common, cross-cutting Pydantic schemas.

Successful responses are returned as-is from their endpoint (no blanket
envelope). Errors use the standardized shape defined here so API clients
can rely on a single error format across the whole API.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    """Liveness: the application process is running and can serve requests."""

    status: Literal["ok"]
    service: str
    environment: str
    version: str


class ReadinessCheck(BaseModel):
    """Status of a single dependency this application actually integrates with."""

    name: str
    status: Literal["ok", "unavailable"]


class ReadinessResponse(BaseModel):
    """Readiness: the application and its currently wired-up dependencies.

    ``checks`` only lists dependencies that are actually implemented. It is
    empty until a real dependency (e.g. the database) is integrated in a
    later story — an empty list is the truthful result, not a placeholder.
    """

    status: Literal["ok"]
    service: str
    environment: str
    version: str
    checks: list[ReadinessCheck] = []
