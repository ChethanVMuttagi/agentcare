"""PatientService: administrative patient business rules.

Establishes this codebase's first real mutating-transaction pattern:

    Route/dependency -> Service -> Repository -> Session

`app.repositories.patient` only adds/flushes/queries — it never commits.
`PatientService` decides transaction completion: `create_patient` commits
after a successful create, and commits nothing at all if validation fails
first (see `create_patient`'s docstring for why that's sufficient to avoid
partial state, with no explicit rollback needed).

Authorization (who is allowed to call these methods at all) is a FastAPI
dependency concern (`app.auth.dependencies`), enforced before a route
handler ever calls into this service — see docs/RBAC.md and
docs/PATIENTS.md. This module is concerned with a narrower, different
question: given a request that's already been authorized, is the DATA it
describes valid (does this patient number already exist in this
organization? is this user linkage allowed?).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.membership import OrganizationMembership, Role
from app.models.patient import Patient
from app.repositories import patient as patient_repository


class PatientNotFoundError(AppException):
    """404: no patient matches, within the caller's own organization.

    Deliberately raised identically whether the patient truly doesn't
    exist anywhere, or exists but belongs to a DIFFERENT organization —
    `app.repositories.patient` never returns a cross-tenant row in the
    first place, so this class has no way to distinguish the two cases
    even if it wanted to. See docs/PATIENTS.md "Cross-Tenant Protection".
    """

    status_code = 404
    error_code = "patient_not_found"


class PatientNumberConflictError(AppException):
    """409: `patient_number` is already in use within this organization."""

    status_code = 409
    error_code = "patient_number_conflict"


class InvalidPatientLinkError(AppException):
    """422: the requested `user_id` cannot be linked to this patient record.

    Covers, uniformly (no distinguishing detail returned to the caller):
    the user has no membership in this organization at all; the
    membership is inactive; the membership's role is not `Role.PATIENT`
    (see the module docstring on why that's a service-level rule, not a
    database constraint); or the user is already linked to a different
    patient record within this same organization.
    """

    status_code = 422
    error_code = "invalid_patient_link"


async def _validate_user_link(
    session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Raise `InvalidPatientLinkError` unless `user_id` may be linked as a
    patient within `organization_id` right now.

    Requires an ACTIVE membership with `Role.PATIENT` in this specific
    organization, and that no other patient in this organization is
    already linked to this user (the database's
    `uq_patients_organization_id_user_id` constraint enforces the latter
    too, at flush time, as defense-in-depth against a race between this
    check and the insert — see `PatientService.create_patient`).

    Enforced HERE, at the service level, rather than as a database
    constraint/trigger: a membership's role can change independently of
    any patient row (e.g. an admin promotes/demotes a member), so a
    `Role.PATIENT`-only constraint that referenced `organization_memberships`
    would need to be continuously re-validated on every membership update,
    not just on patient creation — a database trigger could do this, but
    that's meaningfully more schema machinery than this story's scope
    justifies. A service-level check, run at patient-creation time, is
    simpler, keeps the invariant in one readable place, and matches this
    project's established preference for a minimal schema plus explicit
    application-level business rules (see docs/adr/ADR-0005-patient-identity-and-access.md).
    """
    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if (
        membership is None
        or not membership.is_active
        or membership.role is not Role.PATIENT
    ):
        raise InvalidPatientLinkError(
            "The specified user cannot be linked as a patient in this organization."
        )

    existing = await patient_repository.get_by_user_id(
        session, organization_id=organization_id, user_id=user_id
    )
    if existing is not None:
        raise InvalidPatientLinkError(
            "The specified user is already linked to a patient record in this organization."
        )


class PatientService:
    """Administrative patient business rules, scoped to one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_patient(
        self,
        *,
        organization_id: uuid.UUID,
        patient_number: str,
        first_name: str,
        last_name: str,
        date_of_birth: date,
        user_id: uuid.UUID | None = None,
    ) -> Patient:
        """Create a patient record, committing only once every check passes.

        Validation order: patient-number conflict, then (if `user_id` is
        given) user-linkage validity. If either check raises, this method
        returns before ever calling `session.add()` — nothing is staged,
        so there is no partial state to roll back. If the create itself
        fails at flush time (e.g. a race against a concurrent request
        using the same patient number), the underlying `IntegrityError`
        propagates uncaught: it is never translated into API-visible
        detail (the global exception handler in `app.core.exceptions`
        maps any unhandled exception to a generic 500), so no raw
        database/constraint text ever reaches a client either way.
        """
        existing = await patient_repository.get_by_patient_number(
            self._session, organization_id=organization_id, patient_number=patient_number
        )
        if existing is not None:
            raise PatientNumberConflictError(
                "A patient with this patient number already exists in this organization."
            )

        if user_id is not None:
            await _validate_user_link(
                self._session, organization_id=organization_id, user_id=user_id
            )

        patient = Patient(
            organization_id=organization_id,
            user_id=user_id,
            patient_number=patient_number,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=date_of_birth,
        )
        await patient_repository.create(self._session, patient)
        await self._session.commit()
        return patient

    async def get_patient(
        self, *, organization_id: uuid.UUID, patient_id: uuid.UUID
    ) -> Patient:
        """Tenant-scoped retrieval by id. Raises `PatientNotFoundError`
        (never anything more specific) if no such patient exists within
        `organization_id` — see `PatientNotFoundError`."""
        patient = await patient_repository.get_by_id(
            self._session, organization_id=organization_id, patient_id=patient_id
        )
        if patient is None:
            raise PatientNotFoundError("No patient found with this id in this organization.")
        return patient

    async def list_patients(
        self, *, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[Patient]:
        """Tenant-scoped listing."""
        return await patient_repository.list_by_organization(
            self._session, organization_id=organization_id, limit=limit, offset=offset
        )

    async def get_own_patient_record(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> Patient:
        """Return the patient record linked to `user_id` within
        `organization_id`. Raises `PatientNotFoundError` if none is linked
        — the same error a `GET /patients/{id}` miss would raise, so a
        caller cannot distinguish "you have no linked record" from any
        other not-found case."""
        patient = await patient_repository.get_by_user_id(
            self._session, organization_id=organization_id, user_id=user_id
        )
        if patient is None:
            raise PatientNotFoundError("No patient record is linked to your account.")
        return patient
