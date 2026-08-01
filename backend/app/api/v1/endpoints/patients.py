"""Patient endpoints: AgentCare's first tenant-scoped business API.

The first production routes that actually consume the authorization
primitives built in STORY-004 (`get_current_membership`, `require_roles`)
— see docs/RBAC.md and docs/PATIENTS.md.

Route ordering matters: `GET .../patients/me` is registered BEFORE
`GET .../patients/{patient_id}` so it is matched first — otherwise
FastAPI/Starlette would try to bind the literal path segment "me" to the
`{patient_id}` UUID parameter of the more general route (and fail with a
confusing 422, since "me" isn't a valid UUID), rather than reaching the
dedicated self-access route at all.

`organization_id` is never trusted from a JWT — every route below
resolves it as a path parameter and requires FastAPI to establish a real,
active, database-backed membership (`get_current_membership`) before any
patient data is touched. See docs/RBAC.md "The JWT Trust Boundary".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_membership, get_current_user, require_roles
from app.core.pagination import MAX_PAGE_SIZE
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.schemas.patient import PatientCreate, PatientListResponse, PatientResponse
from app.services.patient import PatientService

router = APIRouter(prefix="/organizations/{organization_id}/patients")

_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)


@router.post("", response_model=PatientResponse, status_code=201)
async def create_patient(
    organization_id: uuid.UUID,
    payload: PatientCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
) -> PatientResponse:
    """Create a patient record. ADMIN/STAFF only."""
    service = PatientService(session)
    patient = await service.create_patient(
        organization_id=organization_id,
        patient_number=payload.patient_number,
        first_name=payload.first_name,
        last_name=payload.last_name,
        date_of_birth=payload.date_of_birth,
        user_id=payload.user_id,
    )
    return PatientResponse.model_validate(patient)


@router.get("", response_model=PatientListResponse)
async def list_patients(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PatientListResponse:
    """List patients in this organization. ADMIN/STAFF only."""
    service = PatientService(session)
    patients = await service.list_patients(
        organization_id=organization_id, limit=limit, offset=offset
    )
    return PatientListResponse(
        patients=[PatientResponse.model_validate(patient) for patient in patients]
    )


@router.get("/me", response_model=PatientResponse)
async def get_my_patient_record(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    # Any ACTIVE membership (any role) may call this route — it never
    # exposes more than "the patient record linked to YOUR OWN user_id",
    # so there is nothing for a non-patient role to gain from calling it.
    # In practice a Role.ADMIN/Role.STAFF membership will simply get 404,
    # since patient-user linkage is only ever established for a
    # Role.PATIENT membership (see app.services.patient._validate_user_link).
    # See docs/RBAC.md and docs/PATIENTS.md for this explicit decision.
    _membership: Annotated[OrganizationMembership, Depends(get_current_membership)],
) -> PatientResponse:
    """Return the caller's own linked patient record, if any."""
    service = PatientService(session)
    patient = await service.get_own_patient_record(
        organization_id=organization_id, user_id=current_user.id
    )
    return PatientResponse.model_validate(patient)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
) -> PatientResponse:
    """Retrieve a patient by id. ADMIN/STAFF only — a PATIENT-role
    membership must use `GET .../patients/me` instead; it cannot look up
    an arbitrary patient id even within its own organization (see
    docs/PATIENTS.md "Patient Self-Access")."""
    service = PatientService(session)
    patient = await service.get_patient(organization_id=organization_id, patient_id=patient_id)
    return PatientResponse.model_validate(patient)
