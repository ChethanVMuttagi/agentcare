"""Appointment endpoints: booking, rescheduling, cancellation.

RBAC (see docs/APPOINTMENTS.md "RBAC" and docs/RBAC.md): `ADMIN`/`STAFF`
may book, view, list (organization-wide), reschedule, and cancel any
appointment in their organization. `PATIENT` may book, view, reschedule,
and cancel ONLY their own appointments — never another patient's, and
never by supplying another patient's id (see "Patient Identity" below).

## Patient Identity: Never Trusted From The Client

For every route a `PATIENT`-role caller can reach, this module resolves
`patient_id` from the caller's OWN linked `Patient` record
(`PatientService.get_own_patient_record`, keyed off the authenticated
`User.id` — see docs/RBAC.md Section 10), and passes that resolved id
into the service layer. `AppointmentCreate.patient_id` (client-supplied)
is used ONLY for `ADMIN`/`STAFF` callers booking on behalf of a patient;
for a `PATIENT` caller it is read but never trusted or used — see
`_resolve_booking_patient_id`.

## Listing Privacy

`GET .../appointments` returns an organization-wide list for `ADMIN`/
`STAFF`, but a `PATIENT` caller reaching the SAME route receives their
OWN appointments only — see `list_appointments` below and
docs/APPOINTMENTS.md "Listing Privacy".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.core.exceptions import AppException
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.user import User
from app.schemas.appointment import (
    AppointmentCancelRequest,
    AppointmentCreate,
    AppointmentListResponse,
    AppointmentRescheduleRequest,
    AppointmentResponse,
)
from app.services.appointment import AppointmentService
from app.services.patient import PatientService

router = APIRouter(prefix="/organizations/{organization_id}/appointments")

_require_any_access = require_roles(Role.ADMIN, Role.STAFF, Role.PATIENT)


class PatientIdRequiredError(AppException):
    """422: an ADMIN/STAFF caller must supply `patient_id` when booking."""

    status_code = 422
    error_code = "patient_id_required"


async def _own_patient_id(
    session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    patient_service = PatientService(session)
    own_patient = await patient_service.get_own_patient_record(
        organization_id=organization_id, user_id=user_id
    )
    return own_patient.id


async def _resolve_booking_patient_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    current_user: User,
    requested_patient_id: uuid.UUID | None,
) -> uuid.UUID:
    """Resolve the `patient_id` a booking should use.

    `PATIENT` role: ALWAYS the caller's own linked patient id —
    `requested_patient_id` (whatever the client sent) is deliberately
    ignored, not merely validated, so there is no code path where a
    patient could act on another patient's behalf by supplying a
    different id. `ADMIN`/`STAFF`: `requested_patient_id` is required.
    """
    if membership.role is Role.PATIENT:
        return await _own_patient_id(
            session, organization_id=organization_id, user_id=current_user.id
        )
    if requested_patient_id is None:
        raise PatientIdRequiredError("patient_id is required.")
    return requested_patient_id


async def _restriction_patient_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    current_user: User,
) -> uuid.UUID | None:
    """`None` for ADMIN/STAFF (no ownership restriction); the caller's own
    patient id for PATIENT — passed to `AppointmentService` so a lookup,
    reschedule, or cancel targeting another patient's appointment 404s,
    the same non-disclosing shape as any other not-found case."""
    if membership.role is Role.PATIENT:
        return await _own_patient_id(
            session, organization_id=organization_id, user_id=current_user.id
        )
    return None


@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(
    organization_id: uuid.UUID,
    payload: AppointmentCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AppointmentResponse:
    """Book an appointment. ADMIN/STAFF may book for any patient in the
    organization (`patient_id` required in the body). PATIENT may book
    only for themselves (`patient_id`, if supplied, is ignored — see the
    module docstring)."""
    patient_id = await _resolve_booking_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        requested_patient_id=payload.patient_id,
    )
    service = AppointmentService(session)
    appointment = await service.book_appointment(
        organization_id=organization_id,
        patient_id=patient_id,
        practitioner_id=payload.practitioner_id,
        department_id=payload.department_id,
        start_at=payload.start_at,
        duration_minutes=payload.duration_minutes,
    )
    return AppointmentResponse.model_validate(appointment)


@router.get("/{appointment_id}", response_model=AppointmentResponse)
async def get_appointment(
    organization_id: uuid.UUID,
    appointment_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AppointmentResponse:
    """Retrieve an appointment by id. ADMIN/STAFF: any appointment in the
    organization. PATIENT: only their own — any other appointment id
    404s, identically to a truly nonexistent one."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = AppointmentService(session)
    appointment = await service.get_appointment(
        organization_id=organization_id, appointment_id=appointment_id, patient_id=restriction
    )
    return AppointmentResponse.model_validate(appointment)


@router.get("", response_model=AppointmentListResponse)
async def list_appointments(
    organization_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = 50,
    offset: int = 0,
) -> AppointmentListResponse:
    """List appointments. ADMIN/STAFF: organization-wide. PATIENT: their
    own appointments only — never the organization-wide list (see the
    module docstring "Listing Privacy")."""
    service = AppointmentService(session)
    if membership.role is Role.PATIENT:
        patient_id = await _own_patient_id(
            session, organization_id=organization_id, user_id=current_user.id
        )
        appointments = await service.list_appointments_for_patient(
            organization_id=organization_id, patient_id=patient_id, limit=limit, offset=offset
        )
    else:
        appointments = await service.list_appointments(
            organization_id=organization_id, limit=limit, offset=offset
        )
    return AppointmentListResponse(
        appointments=[AppointmentResponse.model_validate(appt) for appt in appointments]
    )


@router.patch("/{appointment_id}/reschedule", response_model=AppointmentResponse)
async def reschedule_appointment(
    organization_id: uuid.UUID,
    appointment_id: uuid.UUID,
    payload: AppointmentRescheduleRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AppointmentResponse:
    """Reschedule a `booked` appointment to a new time. ADMIN/STAFF: any
    appointment in the organization. PATIENT: only their own."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = AppointmentService(session)
    appointment = await service.reschedule_appointment(
        organization_id=organization_id,
        appointment_id=appointment_id,
        start_at=payload.start_at,
        duration_minutes=payload.duration_minutes,
        patient_id=restriction,
    )
    return AppointmentResponse.model_validate(appointment)


@router.post("/{appointment_id}/cancel", response_model=AppointmentResponse)
async def cancel_appointment(
    organization_id: uuid.UUID,
    appointment_id: uuid.UUID,
    payload: AppointmentCancelRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AppointmentResponse:
    """Cancel a `booked` appointment. ADMIN/STAFF: any appointment in the
    organization. PATIENT: only their own."""
    restriction = await _restriction_patient_id(
        session, organization_id=organization_id, membership=membership, current_user=current_user
    )
    service = AppointmentService(session)
    appointment = await service.cancel_appointment(
        organization_id=organization_id,
        appointment_id=appointment_id,
        cancellation_reason=payload.cancellation_reason,
        patient_id=restriction,
    )
    return AppointmentResponse.model_validate(appointment)
