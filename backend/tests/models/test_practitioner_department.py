"""PractitionerDepartment model tests against real PostgreSQL.

See tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL
and how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]


async def test_assignment_id_is_generated_uuid(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("assign-uuid")
    facility = await make_facility(org, "assign-uuid")
    department = await make_department(org, facility, "ASSIGN-UUID")
    practitioner = await make_practitioner(org)

    assignment = PractitionerDepartment(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
    )
    db_session.add(assignment)
    await db_session.flush()

    assert isinstance(assignment.id, uuid.UUID)


async def test_assignment_cross_organization_practitioner_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    """The practitioner must belong to the SAME organization as the
    assignment row — enforced by a database-level composite FK."""
    org_a = await make_organization("assign-cross-prac-a")
    org_b = await make_organization("assign-cross-prac-b")
    facility_a = await make_facility(org_a, "assign-cross-prac-a")
    department_a = await make_department(org_a, facility_a, "ASSIGN-CROSS-PRAC")
    practitioner_b = await make_practitioner(org_b)  # belongs to org_b

    assignment = PractitionerDepartment(
        organization_id=org_a.id,
        practitioner_id=practitioner_b.id,  # cross-org practitioner
        department_id=department_a.id,
    )
    db_session.add(assignment)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_assignment_cross_organization_department_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    """The department must belong to the SAME organization as the
    assignment row — enforced by a database-level composite FK."""
    org_a = await make_organization("assign-cross-dept-a")
    org_b = await make_organization("assign-cross-dept-b")
    facility_b = await make_facility(org_b, "assign-cross-dept-b")
    department_b = await make_department(org_b, facility_b, "ASSIGN-CROSS-DEPT")
    practitioner_a = await make_practitioner(org_a)  # belongs to org_a

    assignment = PractitionerDepartment(
        organization_id=org_a.id,
        practitioner_id=practitioner_a.id,
        department_id=department_b.id,  # cross-org department
    )
    db_session.add(assignment)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_duplicate_assignment_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("assign-duplicate")
    facility = await make_facility(org, "assign-duplicate")
    department = await make_department(org, facility, "ASSIGN-DUP")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    duplicate = PractitionerDepartment(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_practitioner_may_be_assigned_to_multiple_departments(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("assign-multi-dept")
    facility = await make_facility(org, "assign-multi-dept")
    department_1 = await make_department(org, facility, "ASSIGN-MULTI-1")
    department_2 = await make_department(org, facility, "ASSIGN-MULTI-2")
    practitioner = await make_practitioner(org)

    assignment_1 = await make_practitioner_department(org, practitioner, department_1)
    assignment_2 = await make_practitioner_department(org, practitioner, department_2)

    assert assignment_1.department_id != assignment_2.department_id
    assert assignment_1.practitioner_id == assignment_2.practitioner_id == practitioner.id


async def test_department_may_have_multiple_practitioners(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("assign-multi-prac")
    facility = await make_facility(org, "assign-multi-prac")
    department = await make_department(org, facility, "ASSIGN-MULTI-PRAC")
    practitioner_1 = await make_practitioner(org)
    practitioner_2 = await make_practitioner(org)

    assignment_1 = await make_practitioner_department(org, practitioner_1, department)
    assignment_2 = await make_practitioner_department(org, practitioner_2, department)

    assert assignment_1.practitioner_id != assignment_2.practitioner_id
    assert assignment_1.department_id == assignment_2.department_id == department.id


async def test_assignment_is_active_defaults_true(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("assign-active-default")
    facility = await make_facility(org, "assign-active-default")
    department = await make_department(org, facility, "ASSIGN-ACTIVE")
    practitioner = await make_practitioner(org)

    assignment = PractitionerDepartment(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
    )
    db_session.add(assignment)
    await db_session.flush()

    assert assignment.is_active is True


async def test_assignment_relationships(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
) -> None:
    org = await make_organization("assign-relationships")
    facility = await make_facility(org, "assign-relationships")
    department = await make_department(org, facility, "ASSIGN-REL")
    practitioner = await make_practitioner(org)
    assignment = await make_practitioner_department(org, practitioner, department)

    await db_session.refresh(assignment, attribute_names=["practitioner", "department"])
    assert assignment.practitioner.id == practitioner.id
    assert assignment.department.id == department.id

    await db_session.refresh(practitioner, attribute_names=["department_assignments"])
    assert assignment in practitioner.department_assignments

    await db_session.refresh(department, attribute_names=["practitioner_assignments"])
    assert assignment in department.practitioner_assignments
