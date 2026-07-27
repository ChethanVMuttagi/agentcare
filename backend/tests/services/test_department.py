"""app.services.department.DepartmentService tests against real PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.services.department import (
    DepartmentCodeConflictError,
    DepartmentNotFoundError,
    DepartmentService,
    FacilityNotFoundError,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]


async def test_create_department_succeeds(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("svc-dept-create")
    facility = await make_facility(org, "svc-dept-create")
    service = DepartmentService(db_session)

    department = await service.create_department(
        organization_id=org.id, facility_id=facility.id, name="Cardiology", code="CARD"
    )

    assert department.id is not None
    assert department.organization_id == org.id
    assert department.facility_id == facility.id


async def test_create_department_rejects_facility_from_another_organization(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org_a = await make_organization("svc-dept-cross-a")
    org_b = await make_organization("svc-dept-cross-b")
    facility_b = await make_facility(org_b, "svc-dept-cross-b")
    service = DepartmentService(db_session)

    with pytest.raises(FacilityNotFoundError):
        await service.create_department(
            organization_id=org_a.id, facility_id=facility_b.id, name="Cardiology", code="CARD"
        )


async def test_create_department_rejects_unknown_facility(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-dept-unknown-facility")
    service = DepartmentService(db_session)

    with pytest.raises(FacilityNotFoundError):
        await service.create_department(
            organization_id=org.id, facility_id=uuid.uuid4(), name="Cardiology", code="CARD"
        )


async def test_create_department_rejects_duplicate_code(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("svc-dept-dup-code")
    facility = await make_facility(org, "svc-dept-dup-code")
    await make_department(org, facility, "DUP")
    service = DepartmentService(db_session)

    with pytest.raises(DepartmentCodeConflictError):
        await service.create_department(
            organization_id=org.id, facility_id=facility.id, name="Another Dept", code="DUP"
        )


async def test_get_department_returns_tenant_scoped_department(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("svc-dept-get")
    facility = await make_facility(org, "svc-dept-get")
    department = await make_department(org, facility, "SVC-DEPT-GET")
    service = DepartmentService(db_session)

    result = await service.get_department(organization_id=org.id, department_id=department.id)

    assert result.id == department.id


async def test_get_department_raises_not_found_for_wrong_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("svc-dept-get-wrong-a")
    org_b = await make_organization("svc-dept-get-wrong-b")
    facility_a = await make_facility(org_a, "svc-dept-get-wrong-a")
    department = await make_department(org_a, facility_a, "SVC-DEPT-WRONG")
    service = DepartmentService(db_session)

    with pytest.raises(DepartmentNotFoundError):
        await service.get_department(organization_id=org_b.id, department_id=department.id)


async def test_get_department_raises_not_found_for_unknown_id(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-dept-get-unknown")
    service = DepartmentService(db_session)

    with pytest.raises(DepartmentNotFoundError):
        await service.get_department(organization_id=org.id, department_id=uuid.uuid4())


async def test_list_departments_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("svc-dept-list-a")
    org_b = await make_organization("svc-dept-list-b")
    facility_a = await make_facility(org_a, "svc-dept-list-a")
    facility_b = await make_facility(org_b, "svc-dept-list-b")
    dept_a = await make_department(org_a, facility_a, "SVC-DEPT-LIST-A")
    await make_department(org_b, facility_b, "SVC-DEPT-LIST-B")
    service = DepartmentService(db_session)

    results = await service.list_departments(organization_id=org_a.id)

    assert [d.id for d in results] == [dept_a.id]
