"""DepartmentService: administrative department business rules.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005 (see `app.services.patient`). Transaction
ownership: `create_department` commits only after every check passes;
repositories only ever add/flush.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.department import Department
from app.repositories import department as department_repository
from app.repositories import facility as facility_repository


class DepartmentNotFoundError(AppException):
    """404: no department matches, within the caller's own organization."""

    status_code = 404
    error_code = "department_not_found"


class FacilityNotFoundError(AppException):
    """404: the referenced facility does not exist within this organization.

    Deliberately raised identically whether the facility truly doesn't
    exist anywhere, or exists but belongs to a DIFFERENT organization —
    `app.repositories.facility` never returns a cross-tenant row, so a
    caller cannot use this to learn a facility exists elsewhere.
    """

    status_code = 404
    error_code = "facility_not_found"


class DepartmentCodeConflictError(AppException):
    """409: `code` is already in use within this facility."""

    status_code = 409
    error_code = "department_code_conflict"


class DepartmentService:
    """Administrative department business rules, scoped to one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_department(
        self,
        *,
        organization_id: uuid.UUID,
        facility_id: uuid.UUID,
        name: str,
        code: str,
    ) -> Department:
        """Create a department, committing only once every check passes.

        Validation order: facility ownership (the facility must exist
        AND belong to `organization_id`), then code conflict within that
        facility. The database's composite foreign key
        (`fk_departments_organization_id_facility_id_facilities`) is the
        actual, race-safe enforcement of facility ownership — this
        pre-check exists to give a clean 404 on the common,
        non-racing path rather than an opaque constraint violation.
        """
        facility = await facility_repository.get_by_id(
            self._session, organization_id=organization_id, facility_id=facility_id
        )
        if facility is None:
            raise FacilityNotFoundError("No facility found with this id in this organization.")

        existing = await department_repository.get_by_facility_and_code(
            self._session, organization_id=organization_id, facility_id=facility_id, code=code
        )
        if existing is not None:
            raise DepartmentCodeConflictError(
                "A department with this code already exists in this facility."
            )

        department = Department(
            organization_id=organization_id,
            facility_id=facility_id,
            name=name,
            code=code,
        )
        await department_repository.create(self._session, department)
        await self._session.commit()
        return department

    async def get_department(
        self, *, organization_id: uuid.UUID, department_id: uuid.UUID
    ) -> Department:
        """Tenant-scoped retrieval by id. Raises `DepartmentNotFoundError`
        if no such department exists within `organization_id`."""
        department = await department_repository.get_by_id(
            self._session, organization_id=organization_id, department_id=department_id
        )
        if department is None:
            raise DepartmentNotFoundError("No department found with this id in this organization.")
        return department

    async def list_departments(
        self, *, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[Department]:
        """Tenant-scoped listing."""
        return await department_repository.list_by_organization(
            self._session, organization_id=organization_id, limit=limit, offset=offset
        )
