from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent,
    appointments,
    auth,
    departments,
    documents,
    health,
    patients,
    practitioners,
    workflows,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(patients.router, tags=["patients"])
api_router.include_router(departments.router, tags=["departments"])
api_router.include_router(practitioners.router, tags=["practitioners"])
api_router.include_router(appointments.router, tags=["appointments"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(workflows.router, tags=["workflows"])
api_router.include_router(agent.router, tags=["agent"])
