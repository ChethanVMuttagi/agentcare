"""Department endpoints: administrative scheduling-resource management.

See docs/RBAC.md and docs/SCHEDULING_RESOURCES.md for the authorization
matrix this enforces: `ADMIN` may create; `ADMIN`/`STAFF` may list/get.
`PATIENT` may not reach any route in this file (see
docs/SCHEDULING_RESOURCES.md "RBAC" for why patient-readable discovery
endpoints are deliberately deferred, not exposed here).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.core.pagination import MAX_PAGE_SIZE
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.schemas.department import DepartmentCreate, DepartmentListResponse, DepartmentResponse
from app.services.department import DepartmentService

router = APIRouter(prefix="/organizations/{organization_id}/departments")

_require_admin = require_roles(Role.ADMIN)
_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)


@router.post("", response_model=DepartmentResponse, status_code=201)
async def create_department(
    organization_id: uuid.UUID,
    payload: DepartmentCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_admin)],
) -> DepartmentResponse:
    """Create a department. ADMIN only."""
    service = DepartmentService(session)
    department = await service.create_department(
        organization_id=organization_id,
        facility_id=payload.facility_id,
        name=payload.name,
        code=payload.code,
    )
    return DepartmentResponse.model_validate(department)


@router.get("", response_model=DepartmentListResponse)
async def list_departments(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DepartmentListResponse:
    """List departments in this organization. ADMIN/STAFF only."""
    service = DepartmentService(session)
    departments = await service.list_departments(
        organization_id=organization_id, limit=limit, offset=offset
    )
    return DepartmentListResponse(
        departments=[DepartmentResponse.model_validate(department) for department in departments]
    )


@router.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    organization_id: uuid.UUID,
    department_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
) -> DepartmentResponse:
    """Retrieve a department by id. ADMIN/STAFF only."""
    service = DepartmentService(session)
    department = await service.get_department(
        organization_id=organization_id, department_id=department_id
    )
    return DepartmentResponse.model_validate(department)
