"""Practitioner endpoints: administrative scheduling-resource management.

Covers practitioner CRUD-lite (create/list/get), department assignment,
and recurring availability. See docs/RBAC.md and
docs/SCHEDULING_RESOURCES.md for the authorization matrix: `ADMIN` may
create practitioners and assign them to departments; `ADMIN`/`STAFF` may
list/get practitioners and create/list availability (STAFF may create
availability for day-to-day scheduling operations — see
docs/SCHEDULING_RESOURCES.md "RBAC"). `PATIENT` may not reach any route
in this file.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.schemas.appointment import AvailableTimeSlotResponse, AvailableTimesResponse
from app.schemas.availability import (
    AvailabilityCreate,
    AvailabilityListResponse,
    AvailabilityResponse,
)
from app.schemas.practitioner import (
    PractitionerCreate,
    PractitionerDepartmentResponse,
    PractitionerListResponse,
    PractitionerResponse,
)
from app.services.availability import AvailabilityService
from app.services.availability_query import (
    MAX_APPOINTMENT_DURATION_MINUTES,
    MIN_APPOINTMENT_DURATION_MINUTES,
    AvailabilityQueryService,
)
from app.services.department import DepartmentService
from app.services.practitioner import PractitionerService

router = APIRouter(prefix="/organizations/{organization_id}/practitioners")

_require_admin = require_roles(Role.ADMIN)
_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)
_require_any_access = require_roles(Role.ADMIN, Role.STAFF, Role.PATIENT)


@router.post("", response_model=PractitionerResponse, status_code=201)
async def create_practitioner(
    organization_id: uuid.UUID,
    payload: PractitionerCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_admin)],
) -> PractitionerResponse:
    """Create a practitioner. ADMIN only."""
    service = PractitionerService(session)
    practitioner = await service.create_practitioner(
        organization_id=organization_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        practitioner_type=payload.practitioner_type,
    )
    return PractitionerResponse.model_validate(practitioner)


@router.get("", response_model=PractitionerListResponse)
async def list_practitioners(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    limit: int = 50,
    offset: int = 0,
) -> PractitionerListResponse:
    """List practitioners in this organization. ADMIN/STAFF only."""
    service = PractitionerService(session)
    practitioners = await service.list_practitioners(
        organization_id=organization_id, limit=limit, offset=offset
    )
    return PractitionerListResponse(
        practitioners=[
            PractitionerResponse.model_validate(practitioner) for practitioner in practitioners
        ]
    )


@router.get("/{practitioner_id}", response_model=PractitionerResponse)
async def get_practitioner(
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
) -> PractitionerResponse:
    """Retrieve a practitioner by id. ADMIN/STAFF only."""
    service = PractitionerService(session)
    practitioner = await service.get_practitioner(
        organization_id=organization_id, practitioner_id=practitioner_id
    )
    return PractitionerResponse.model_validate(practitioner)


@router.post(
    "/{practitioner_id}/departments/{department_id}",
    response_model=PractitionerDepartmentResponse,
    status_code=201,
)
async def assign_practitioner_to_department(
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    department_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_admin)],
) -> PractitionerDepartmentResponse:
    """Assign a practitioner to a department. ADMIN only."""
    service = PractitionerService(session)
    assignment = await service.assign_to_department(
        organization_id=organization_id,
        practitioner_id=practitioner_id,
        department_id=department_id,
    )
    return PractitionerDepartmentResponse.model_validate(assignment)


@router.post(
    "/{practitioner_id}/availability", response_model=AvailabilityResponse, status_code=201
)
async def create_availability(
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    payload: AvailabilityCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
) -> AvailabilityResponse:
    """Create a recurring availability window for a practitioner.
    ADMIN/STAFF (STAFF may perform day-to-day scheduling operations —
    see docs/SCHEDULING_RESOURCES.md "RBAC")."""
    service = AvailabilityService(session)
    availability = await service.create_availability(
        organization_id=organization_id,
        practitioner_id=practitioner_id,
        department_id=payload.department_id,
        day_of_week=payload.day_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        timezone=payload.timezone,
    )
    return AvailabilityResponse.model_validate(availability)


@router.get("/{practitioner_id}/availability", response_model=AvailabilityListResponse)
async def list_availability(
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    limit: int = 50,
    offset: int = 0,
) -> AvailabilityListResponse:
    """List a practitioner's recurring availability windows.
    ADMIN/STAFF only. 404s (via `PractitionerService.get_practitioner`)
    if `practitioner_id` doesn't exist in this organization, before ever
    querying availability."""
    practitioner_service = PractitionerService(session)
    await practitioner_service.get_practitioner(
        organization_id=organization_id, practitioner_id=practitioner_id
    )

    availability_service = AvailabilityService(session)
    windows = await availability_service.list_availability(
        organization_id=organization_id,
        practitioner_id=practitioner_id,
        limit=limit,
        offset=offset,
    )
    return AvailabilityListResponse(
        availability=[AvailabilityResponse.model_validate(window) for window in windows]
    )


@router.get(
    "/{practitioner_id}/available-times",
    response_model=AvailableTimesResponse,
)
async def get_available_times(
    organization_id: uuid.UUID,
    practitioner_id: uuid.UUID,
    department_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    appointment_date: Annotated[date, Query(alias="date")],
    duration_minutes: Annotated[
        int, Query(ge=MIN_APPOINTMENT_DURATION_MINUTES, le=MAX_APPOINTMENT_DURATION_MINUTES)
    ],
) -> AvailableTimesResponse:
    """Concrete, bookable candidate start times for one practitioner,
    department, and calendar date. ADMIN/STAFF/PATIENT (patients may
    discover safe availability to book — see docs/APPOINTMENTS.md
    "RBAC"). 404s if the practitioner or department doesn't exist in this
    organization; otherwise returns only free times — never why any other
    time is unavailable, and never another patient's appointment details
    (docs/APPOINTMENTS.md "Availability Privacy")."""
    practitioner_service = PractitionerService(session)
    await practitioner_service.get_practitioner(
        organization_id=organization_id, practitioner_id=practitioner_id
    )
    department_service = DepartmentService(session)
    await department_service.get_department(
        organization_id=organization_id, department_id=department_id
    )

    availability_query_service = AvailabilityQueryService(session)
    slots = await availability_query_service.list_available_times(
        organization_id=organization_id,
        practitioner_id=practitioner_id,
        department_id=department_id,
        on_date=appointment_date,
        duration_minutes=duration_minutes,
    )
    return AvailableTimesResponse(
        available_times=[
            AvailableTimeSlotResponse(start_at=slot.start_at, end_at=slot.end_at)
            for slot in slots
        ]
    )
