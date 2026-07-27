"""app.services.practitioner.PractitionerService tests against real PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.models.practitioner import Practitioner, PractitionerType
from app.services.department import DepartmentNotFoundError
from app.services.practitioner import (
    PractitionerAlreadyAssignedError,
    PractitionerNotFoundError,
    PractitionerService,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]


async def test_create_practitioner_succeeds(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-prac-create")
    service = PractitionerService(db_session)

    practitioner = await service.create_practitioner(
        organization_id=org.id,
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.PHYSICIAN,
    )

    assert practitioner.id is not None
    assert practitioner.organization_id == org.id


async def test_get_practitioner_returns_tenant_scoped_practitioner(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("svc-prac-get")
    practitioner = await make_practitioner(org)
    service = PractitionerService(db_session)

    result = await service.get_practitioner(organization_id=org.id, practitioner_id=practitioner.id)

    assert result.id == practitioner.id


async def test_get_practitioner_raises_not_found_for_wrong_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("svc-prac-get-wrong-a")
    org_b = await make_organization("svc-prac-get-wrong-b")
    practitioner = await make_practitioner(org_a)
    service = PractitionerService(db_session)

    with pytest.raises(PractitionerNotFoundError):
        await service.get_practitioner(organization_id=org_b.id, practitioner_id=practitioner.id)


async def test_list_practitioners_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("svc-prac-list-a")
    org_b = await make_organization("svc-prac-list-b")
    practitioner_a = await make_practitioner(org_a)
    await make_practitioner(org_b)
    service = PractitionerService(db_session)

    results = await service.list_practitioners(organization_id=org_a.id)

    assert [p.id for p in results] == [practitioner_a.id]


async def test_assign_to_department_succeeds(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("svc-assign-success")
    facility = await make_facility(org, "svc-assign-success")
    department = await make_department(org, facility, "SVC-ASSIGN-SUCCESS")
    practitioner = await make_practitioner(org)
    service = PractitionerService(db_session)

    assignment = await service.assign_to_department(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
    )

    assert assignment.practitioner_id == practitioner.id
    assert assignment.department_id == department.id


async def test_assign_to_department_rejects_cross_tenant_practitioner(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("svc-assign-cross-prac-a")
    org_b = await make_organization("svc-assign-cross-prac-b")
    facility_a = await make_facility(org_a, "svc-assign-cross-prac-a")
    department_a = await make_department(org_a, facility_a, "SVC-ASSIGN-CROSS-PRAC")
    practitioner_b = await make_practitioner(org_b)
    service = PractitionerService(db_session)

    with pytest.raises(PractitionerNotFoundError):
        await service.assign_to_department(
            organization_id=org_a.id,
            practitioner_id=practitioner_b.id,
            department_id=department_a.id,
        )


async def test_assign_to_department_rejects_cross_tenant_department(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("svc-assign-cross-dept-a")
    org_b = await make_organization("svc-assign-cross-dept-b")
    facility_b = await make_facility(org_b, "svc-assign-cross-dept-b")
    department_b = await make_department(org_b, facility_b, "SVC-ASSIGN-CROSS-DEPT")
    practitioner_a = await make_practitioner(org_a)
    service = PractitionerService(db_session)

    with pytest.raises(DepartmentNotFoundError):
        await service.assign_to_department(
            organization_id=org_a.id,
            practitioner_id=practitioner_a.id,
            department_id=department_b.id,
        )


async def test_assign_to_department_rejects_duplicate_assignment(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("svc-assign-dup")
    facility = await make_facility(org, "svc-assign-dup")
    department = await make_department(org, facility, "SVC-ASSIGN-DUP")
    practitioner = await make_practitioner(org)
    service = PractitionerService(db_session)
    await service.assign_to_department(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
    )

    with pytest.raises(PractitionerAlreadyAssignedError):
        await service.assign_to_department(
            organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
        )


async def test_practitioner_may_be_assigned_to_multiple_departments(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("svc-assign-multi")
    facility = await make_facility(org, "svc-assign-multi")
    department_1 = await make_department(org, facility, "SVC-ASSIGN-MULTI-1")
    department_2 = await make_department(org, facility, "SVC-ASSIGN-MULTI-2")
    practitioner = await make_practitioner(org)
    service = PractitionerService(db_session)

    assignment_1 = await service.assign_to_department(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department_1.id
    )
    assignment_2 = await service.assign_to_department(
        organization_id=org.id, practitioner_id=practitioner.id, department_id=department_2.id
    )

    assert assignment_1.department_id != assignment_2.department_id


async def test_assign_to_department_rejects_unknown_practitioner(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("svc-assign-unknown-prac")
    facility = await make_facility(org, "svc-assign-unknown-prac")
    department = await make_department(org, facility, "SVC-ASSIGN-UNKNOWN-PRAC")
    service = PractitionerService(db_session)

    with pytest.raises(PractitionerNotFoundError):
        await service.assign_to_department(
            organization_id=org.id, practitioner_id=uuid.uuid4(), department_id=department.id
        )
