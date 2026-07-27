"""app.repositories.practitioner_department tests against real PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment
from app.repositories import practitioner_department as practitioner_department_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]


async def test_get_assignment_returns_match(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("repo-assign-get")
    facility = await make_facility(org, "repo-assign-get")
    department = await make_department(org, facility, "REPO-ASSIGN-GET")
    practitioner = await make_practitioner(org)
    assignment = await make_practitioner_department(org, practitioner, department)

    result = await practitioner_department_repository.get_assignment(
        db_session,
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
    )

    assert result is not None
    assert result.id == assignment.id


async def test_get_assignment_returns_none_when_unassigned(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("repo-assign-none")
    facility = await make_facility(org, "repo-assign-none")
    department = await make_department(org, facility, "REPO-ASSIGN-NONE")
    practitioner = await make_practitioner(org)  # never assigned

    result = await practitioner_department_repository.get_assignment(
        db_session,
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
    )

    assert result is None


async def test_get_assignment_includes_inactive_rows(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    """`get_assignment` returns the row regardless of `is_active` —
    callers that care about active-only assignment check `.is_active`
    themselves (see app.services.availability)."""
    org = await make_organization("repo-assign-inactive")
    facility = await make_facility(org, "repo-assign-inactive")
    department = await make_department(org, facility, "REPO-ASSIGN-INACTIVE")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department, is_active=False)

    result = await practitioner_department_repository.get_assignment(
        db_session,
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
    )

    assert result is not None
    assert result.is_active is False


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("repo-assign-create-no-commit")
    facility = await make_facility(org, "repo-assign-create-no-commit")
    department = await make_department(org, facility, "REPO-ASSIGN-CREATE")
    practitioner = await make_practitioner(org)
    assignment = PractitionerDepartment(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
    )

    created = await practitioner_department_repository.create(db_session, assignment)
    assert created.id is not None

    await db_session.rollback()

    result = await practitioner_department_repository.get_assignment(
        db_session,
        organization_id=org.id,
        practitioner_id=practitioner.id,
        department_id=department.id,
    )
    assert result is None
