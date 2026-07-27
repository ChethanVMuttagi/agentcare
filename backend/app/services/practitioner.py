"""PractitionerService: administrative practitioner business rules.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005. Transaction ownership: `create_practitioner`
and `assign_to_department` commit only after every check passes;
repositories only ever add/flush.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_department import PractitionerDepartment
from app.repositories import department as department_repository
from app.repositories import practitioner as practitioner_repository
from app.repositories import practitioner_department as practitioner_department_repository
from app.services.department import DepartmentNotFoundError


class PractitionerNotFoundError(AppException):
    """404: no practitioner matches, within the caller's own organization."""

    status_code = 404
    error_code = "practitioner_not_found"


class PractitionerAlreadyAssignedError(AppException):
    """409: this practitioner is already assigned to this department."""

    status_code = 409
    error_code = "practitioner_already_assigned"


class PractitionerService:
    """Administrative practitioner business rules, scoped to one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_practitioner(
        self,
        *,
        organization_id: uuid.UUID,
        first_name: str,
        last_name: str,
        practitioner_type: PractitionerType,
    ) -> Practitioner:
        """Create a practitioner, then commit. No conflict check is
        needed here — unlike `Patient`/`Department`, nothing about a
        `Practitioner` is required to be unique (see docs/SCHEDULING_RESOURCES.md)."""
        practitioner = Practitioner(
            organization_id=organization_id,
            first_name=first_name,
            last_name=last_name,
            practitioner_type=practitioner_type,
        )
        await practitioner_repository.create(self._session, practitioner)
        await self._session.commit()
        return practitioner

    async def get_practitioner(
        self, *, organization_id: uuid.UUID, practitioner_id: uuid.UUID
    ) -> Practitioner:
        """Tenant-scoped retrieval by id. Raises `PractitionerNotFoundError`
        if no such practitioner exists within `organization_id`."""
        practitioner = await practitioner_repository.get_by_id(
            self._session, organization_id=organization_id, practitioner_id=practitioner_id
        )
        if practitioner is None:
            raise PractitionerNotFoundError(
                "No practitioner found with this id in this organization."
            )
        return practitioner

    async def list_practitioners(
        self, *, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[Practitioner]:
        """Tenant-scoped listing."""
        return await practitioner_repository.list_by_organization(
            self._session, organization_id=organization_id, limit=limit, offset=offset
        )

    async def assign_to_department(
        self,
        *,
        organization_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
    ) -> PractitionerDepartment:
        """Assign `practitioner_id` to `department_id`, committing only
        once every check passes.

        Validation order: the practitioner must exist in `organization_id`
        (`PractitionerNotFoundError`), the department must exist in
        `organization_id` (`DepartmentNotFoundError`), then no existing
        assignment for this pairing may already exist
        (`PractitionerAlreadyAssignedError`). Because both prior lookups
        are themselves tenant-scoped, a cross-organization `practitioner_id`
        or `department_id` is rejected as "not found" here — the request
        never reaches the database's composite foreign keys, which remain
        the race-safe, authoritative enforcement regardless.
        """
        practitioner = await practitioner_repository.get_by_id(
            self._session, organization_id=organization_id, practitioner_id=practitioner_id
        )
        if practitioner is None:
            raise PractitionerNotFoundError(
                "No practitioner found with this id in this organization."
            )

        department = await department_repository.get_by_id(
            self._session, organization_id=organization_id, department_id=department_id
        )
        if department is None:
            raise DepartmentNotFoundError("No department found with this id in this organization.")

        existing = await practitioner_department_repository.get_assignment(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        if existing is not None:
            raise PractitionerAlreadyAssignedError(
                "This practitioner is already assigned to this department."
            )

        assignment = PractitionerDepartment(
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        await practitioner_department_repository.create(self._session, assignment)
        await self._session.commit()
        return assignment
