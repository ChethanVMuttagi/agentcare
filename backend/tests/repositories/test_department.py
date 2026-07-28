"""app.repositories.department tests against real PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization
from app.repositories import department as department_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]


async def test_get_by_id_returns_department_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("repo-dept-get")
    facility = await make_facility(org, "repo-dept-get")
    department = await make_department(org, facility, "REPO-DEPT-GET")

    result = await department_repository.get_by_id(
        db_session, organization_id=org.id, department_id=department.id
    )

    assert result is not None
    assert result.id == department.id


async def test_get_by_id_returns_none_for_wrong_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("repo-dept-wrong-org-a")
    org_b = await make_organization("repo-dept-wrong-org-b")
    facility_a = await make_facility(org_a, "repo-dept-wrong-org-a")
    department = await make_department(org_a, facility_a, "REPO-DEPT-WRONG-ORG")

    result = await department_repository.get_by_id(
        db_session, organization_id=org_b.id, department_id=department.id
    )

    assert result is None


async def test_get_by_id_returns_none_for_unknown_department(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-dept-unknown")

    result = await department_repository.get_by_id(
        db_session, organization_id=org.id, department_id=uuid.uuid4()
    )

    assert result is None


async def test_get_by_facility_and_code_returns_match(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("repo-dept-by-code")
    facility = await make_facility(org, "repo-dept-by-code")
    department = await make_department(org, facility, "REPO-DEPT-CODE")

    result = await department_repository.get_by_facility_and_code(
        db_session, organization_id=org.id, facility_id=facility.id, code="REPO-DEPT-CODE"
    )

    assert result is not None
    assert result.id == department.id


async def test_list_by_organization_returns_only_that_organizations_departments(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("repo-dept-list-a")
    org_b = await make_organization("repo-dept-list-b")
    facility_a = await make_facility(org_a, "repo-dept-list-a")
    facility_b = await make_facility(org_b, "repo-dept-list-b")
    dept_a = await make_department(org_a, facility_a, "REPO-DEPT-LIST-A")
    await make_department(org_b, facility_b, "REPO-DEPT-LIST-B")

    results = await department_repository.list_by_organization(
        db_session, organization_id=org_a.id
    )

    assert [d.id for d in results] == [dept_a.id]


async def test_list_by_organization_respects_limit_and_offset(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("repo-dept-paging")
    facility = await make_facility(org, "repo-dept-paging")
    for i in range(5):
        await make_department(org, facility, f"REPO-DEPT-PAGE-{i}")

    first_page = await department_repository.list_by_organization(
        db_session, organization_id=org.id, limit=2, offset=0
    )
    second_page = await department_repository.list_by_organization(
        db_session, organization_id=org.id, limit=2, offset=2
    )

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {d.id for d in first_page}.isdisjoint({d.id for d in second_page})


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("repo-dept-create-no-commit")
    facility = await make_facility(org, "repo-dept-create-no-commit")
    department = Department(
        organization_id=org.id,
        facility_id=facility.id,
        name="Synthetic Dept",
        code="REPO-DEPT-CREATE",
    )

    created = await department_repository.create(db_session, department)
    assert created.id is not None

    await db_session.rollback()

    result = await department_repository.get_by_id(
        db_session, organization_id=org.id, department_id=department.id
    )
    assert result is None


# --- search_by_name (STORY-011, added for app.ai.tools.routing_tools) ---


async def test_search_by_name_matches_case_insensitively(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("repo-dept-search")
    facility = await make_facility(org, "repo-dept-search")
    department = await make_department(org, facility, "REPO-SEARCH-CARD", name="Cardiology")

    results = await department_repository.search_by_name(
        db_session, organization_id=org.id, name_query="cardio"
    )

    assert [d.id for d in results] == [department.id]


async def test_search_by_name_excludes_inactive_departments(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("repo-dept-search-inactive")
    facility = await make_facility(org, "repo-dept-search-inactive")
    await make_department(
        org, facility, "REPO-SEARCH-INACTIVE", name="Retired Ward", is_active=False
    )

    results = await department_repository.search_by_name(
        db_session, organization_id=org.id, name_query="Retired"
    )

    assert results == []


async def test_search_by_name_is_scoped_to_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("repo-dept-search-cross-a")
    org_b = await make_organization("repo-dept-search-cross-b")
    facility_b = await make_facility(org_b, "repo-dept-search-cross-b")
    await make_department(org_b, facility_b, "REPO-SEARCH-CROSS-B", name="Oncology")

    results = await department_repository.search_by_name(
        db_session, organization_id=org_a.id, name_query="Oncology"
    )

    assert results == []


async def test_search_by_name_respects_limit(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("repo-dept-search-limit")
    facility = await make_facility(org, "repo-dept-search-limit")
    for i in range(3):
        await make_department(org, facility, f"REPO-SEARCH-LIMIT-{i}", name=f"Radiology {i}")

    results = await department_repository.search_by_name(
        db_session, organization_id=org.id, name_query="Radiology", limit=2
    )

    assert len(results) == 2
