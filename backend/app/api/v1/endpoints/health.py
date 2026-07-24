from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.config import Environment, Settings, get_settings
from app.db.health import check_database_connection
from app.db.session import get_engine
from app.schemas.common import HealthResponse, ReadinessCheck, ReadinessResponse

router = APIRouter()

# Environments where the database is allowed to be unconfigured without
# that counting as a readiness failure — see `_is_check_acceptable`.
_ENVIRONMENTS_WHERE_UNCONFIGURED_DB_IS_ACCEPTABLE = frozenset(
    {Environment.DEVELOPMENT, Environment.TEST}
)


@router.get("/health", response_model=HealthResponse)
async def get_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Liveness probe: the application process is up and serving requests.

    Independent of database readiness by design — see `get_readiness`.
    """
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env.value,
        version=settings.app_version,
    )


async def get_database_readiness_check(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessCheck:
    """Resolve the current, factual database connectivity state.

    Deliberately environment-agnostic: it reports what's true
    (`ok` / `unavailable` / `not_configured`), never what's acceptable —
    that policy decision belongs to `get_readiness` (see
    `_is_check_acceptable`). A separate FastAPI dependency (rather than
    inline logic in the route) so tests can override it via
    `app.dependency_overrides` to simulate any state without needing a
    real database connection.
    """
    if settings.database_url is None:
        return ReadinessCheck(name="database", status="not_configured")

    try:
        engine = get_engine()
    except RuntimeError:
        return ReadinessCheck(name="database", status="not_configured")

    is_connected = await check_database_connection(engine)
    return ReadinessCheck(name="database", status="ok" if is_connected else "unavailable")


def _is_check_acceptable(check: ReadinessCheck, environment: Environment) -> bool:
    """Whether `check`'s status counts as acceptable for readiness.

    - ``ok`` is always acceptable.
    - ``unavailable`` (configured but unreachable) is never acceptable —
      a real connectivity failure, regardless of environment.
    - ``not_configured`` is acceptable only in development/test, where
      the application must be able to run without a database at all. In
      staging/production, a required dependency that was never configured
      is not safe to report as ready — see docs/DATABASE.md.
    """
    if check.status == "unavailable":
        return False
    if check.status == "not_configured":
        return environment in _ENVIRONMENTS_WHERE_UNCONFIGURED_DB_IS_ACCEPTABLE
    return True  # "ok"


@router.get("/ready", response_model=ReadinessResponse)
async def get_readiness(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    database_check: Annotated[ReadinessCheck, Depends(get_database_readiness_check)],
) -> ReadinessResponse:
    """Readiness probe: reflects only dependencies actually wired up so far.

    Whether a given check's status counts as ready is environment-
    dependent (see `_is_check_acceptable`):

    - ``ok`` is always ready.
    - ``unavailable`` (configured but unreachable) is never ready, in any
      environment — a real failure.
    - ``not_configured`` is ready in `development`/`test` (the app must be
      importable/testable without a database), but **not ready** in
      `staging`/`production` — an unconfigured database is not safe to
      call ready outside development/test.

    When not ready, the response is still returned with the standard
    body, but with HTTP 503 so orchestrators/load balancers treat it as
    such. See docs/DATABASE.md for the full readiness-semantics rationale.
    """
    checks = [database_check]
    is_ready = all(_is_check_acceptable(check, settings.app_env) for check in checks)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        service=settings.app_name,
        environment=settings.app_env.value,
        version=settings.app_version,
        checks=checks,
    )
