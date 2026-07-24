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
    """Status of a single dependency this application actually integrates with.

    - ``ok``: the dependency is configured and was successfully reached.
    - ``unavailable``: the dependency is configured but could not be
      reached — this always makes overall readiness ``not_ready``, in
      every environment.
    - ``not_configured``: no configuration was provided for this
      dependency (e.g. no ``DATABASE_URL``). Whether this makes readiness
      ``not_ready`` is environment-dependent: acceptable in
      `development`/`test` (the app must run without a database there),
      but **not** acceptable in `staging`/`production` — see
      `app.api.v1.endpoints.health._is_check_acceptable` and
      docs/DATABASE.md.
    """

    name: str
    status: Literal["ok", "unavailable", "not_configured"]


class ReadinessResponse(BaseModel):
    """Readiness: the application and its currently wired-up dependencies.

    ``checks`` only lists dependencies that are actually implemented.
    Whether ``status`` is ``ready`` or ``not_ready`` depends on each
    check's status *and* the current environment — see `ReadinessCheck`
    and docs/DATABASE.md for the full rationale.
    """

    status: Literal["ready", "not_ready"]
    service: str
    environment: str
    version: str
    checks: list[ReadinessCheck] = []
