"""`app.ai.tools.routing_tools` tests against real PostgreSQL.

`resolve_department` calls the REAL `app.repositories.department.search_by_name`
— never a fake/hardcoded success path. Proves: an explicit department
name resolves correctly, an ambiguous name returns a bounded candidate
list rather than guessing, an unmatched name is a controlled failure,
inactive departments are never matched, and tenant isolation holds.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.base import ToolExecutionContext, ToolResultStatus
from app.ai.tools.routing_tools import (
    RESOLVE_DEPARTMENT_TOOL,
    ResolveDepartmentArguments,
    build_routing_tool_registry,
)
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import Role
from app.models.organization import Organization
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakeUser = Callable[..., Awaitable[User]]


def _context(*, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> ToolExecutionContext:
    return ToolExecutionContext(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
        patient_id=None,
        workflow_run_id=uuid.uuid4(),
        workflow_step_id=uuid.uuid4(),
    )


def test_registry_get_returns_the_routing_tool() -> None:
    registry = build_routing_tool_registry()
    assert registry.get("resolve_department") is RESOLVE_DEPARTMENT_TOOL


async def test_resolves_an_explicitly_named_department(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("route-tool-resolve")
    admin = await make_user("route-tool-resolve")
    facility = await make_facility(org, "route-tool-resolve")
    department = await make_department(
        org, facility, "CARD", name="Cardiology"
    )

    registry = build_routing_tool_registry()
    context = _context(organization_id=org.id, user_id=admin.id, role=Role.ADMIN)
    result = await registry.execute(
        "resolve_department", {"department_name": "Cardiology"}, context, db_session
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.code == "department_resolved"
    assert result.data == {
        "department_id": str(department.id),
        "department_name": "Cardiology",
        "department_code": "CARD",
    }


async def test_resolves_case_insensitively_and_by_partial_match(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("route-tool-partial")
    admin = await make_user("route-tool-partial")
    facility = await make_facility(org, "route-tool-partial")
    department = await make_department(
        org, facility, "ORTHO", name="Orthopedics"
    )

    registry = build_routing_tool_registry()
    context = _context(organization_id=org.id, user_id=admin.id, role=Role.ADMIN)
    result = await registry.execute(
        "resolve_department", {"department_name": "orthopedic"}, context, db_session
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["department_id"] == str(department.id)  # type: ignore[index]


async def test_no_match_is_a_controlled_failure_not_a_guess(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
) -> None:
    org = await make_organization("route-tool-no-match")
    admin = await make_user("route-tool-no-match")

    registry = build_routing_tool_registry()
    context = _context(organization_id=org.id, user_id=admin.id, role=Role.ADMIN)
    result = await registry.execute(
        "resolve_department", {"department_name": "Nonexistent Department"}, context, db_session
    )

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "department_not_found"


async def test_ambiguous_match_returns_candidates_never_guesses(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("route-tool-ambiguous")
    admin = await make_user("route-tool-ambiguous")
    facility = await make_facility(org, "route-tool-ambiguous")
    await make_department(org, facility, "CARD1", name="Cardiology North")
    await make_department(org, facility, "CARD2", name="Cardiology South")

    registry = build_routing_tool_registry()
    context = _context(organization_id=org.id, user_id=admin.id, role=Role.ADMIN)
    result = await registry.execute(
        "resolve_department", {"department_name": "Cardiology"}, context, db_session
    )

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "ambiguous_department"
    assert result.data is not None
    assert len(result.data["candidates"]) == 2
    names = {c["name"] for c in result.data["candidates"]}
    assert names == {"Cardiology North", "Cardiology South"}


async def test_inactive_department_is_never_matched(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("route-tool-inactive")
    admin = await make_user("route-tool-inactive")
    facility = await make_facility(org, "route-tool-inactive")
    await make_department(
        org, facility, "RETIRED", name="Retired Department", is_active=False
    )

    registry = build_routing_tool_registry()
    context = _context(organization_id=org.id, user_id=admin.id, role=Role.ADMIN)
    result = await registry.execute(
        "resolve_department", {"department_name": "Retired"}, context, db_session
    )

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "department_not_found"


async def test_cross_tenant_department_is_never_matched(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org_a = await make_organization("route-tool-cross-a")
    admin_a = await make_user("route-tool-cross-a")
    org_b = await make_organization("route-tool-cross-b")
    facility_b = await make_facility(org_b, "route-tool-cross-b")
    await make_department(org_b, facility_b, "ONCB", name="Oncology B")

    registry = build_routing_tool_registry()
    context = _context(organization_id=org_a.id, user_id=admin_a.id, role=Role.ADMIN)
    result = await registry.execute(
        "resolve_department", {"department_name": "Oncology"}, context, db_session
    )

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "department_not_found"


def test_arguments_schema_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ResolveDepartmentArguments.model_validate(
            {"department_name": "Cardiology", "patient_id": str(uuid.uuid4())}
        )


def test_arguments_schema_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        ResolveDepartmentArguments.model_validate({"department_name": ""})
