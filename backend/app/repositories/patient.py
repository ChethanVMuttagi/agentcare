"""Tenant-scoped persistence/query operations for `Patient`.

Every read here REQUIRES an `organization_id` — there is no
`get_by_id(patient_id)` shape that a business route could accidentally
call without tenant context (see docs/PATIENTS.md "Tenant Ownership").
A patient that exists but belongs to a different organization is
indistinguishable, from these functions' return values, from a patient
that doesn't exist at all — both simply return `None` — which is what
gives the service/API layers their non-disclosing cross-tenant behavior
"for free", without any special-casing.

This module only adds, flushes, and queries. It never commits — see
`app.services.patient` for transaction ownership.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, patient_id: uuid.UUID
) -> Patient | None:
    """Return the patient with `patient_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(Patient).where(
            Patient.organization_id == organization_id,
            Patient.id == patient_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_user_id(
    session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> Patient | None:
    """Return the patient linked to `user_id` within `organization_id`, if any.

    Used for both patient self-access (`GET /patients/me`) and linkage
    validation (rejecting a second patient linked to the same user in the
    same organization) — see `app.services.patient`.
    """
    result = await session.execute(
        select(Patient).where(
            Patient.organization_id == organization_id,
            Patient.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_patient_number(
    session: AsyncSession, *, organization_id: uuid.UUID, patient_number: str
) -> Patient | None:
    """Return the patient numbered `patient_number` within `organization_id`, if any."""
    result = await session.execute(
        select(Patient).where(
            Patient.organization_id == organization_id,
            Patient.patient_number == patient_number,
        )
    )
    return result.scalar_one_or_none()


async def list_by_organization(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Patient]:
    """Return patients belonging to `organization_id`, newest first."""
    result = await session.execute(
        select(Patient)
        .where(Patient.organization_id == organization_id)
        .order_by(Patient.created_at.desc(), Patient.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def create(session: AsyncSession, patient: Patient) -> Patient:
    """Add and flush a new `Patient`. Does NOT commit — see
    `app.services.patient.PatientService.create_patient` for transaction
    ownership."""
    session.add(patient)
    await session.flush()
    return patient
