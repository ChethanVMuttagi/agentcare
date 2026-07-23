from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def get_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Liveness probe: the application process is up and serving requests."""
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env.value,
        version=settings.app_version,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def get_readiness(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    """Readiness probe: reflects only dependencies actually wired up so far.

    No database or external service integration exists yet, so ``checks``
    is truthfully empty rather than reporting a faked dependency status.
    """
    return ReadinessResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env.value,
        version=settings.app_version,
        checks=[],
    )
