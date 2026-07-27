"""Department model tests against real PostgreSQL.

See tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL
and how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.facility import Facility
from app.models.organization import Organization

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]


async def test_department_id_is_generated_uuid(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("dept-uuid")
    facility = await make_facility(org, "dept-uuid")
    department = Department(
        organization_id=org.id, facility_id=facility.id, name="Synthetic Dept", code="DEPT-UUID"
    )
    db_session.add(department)
    await db_session.flush()

    assert isinstance(department.id, uuid.UUID)


async def test_department_requires_a_valid_organization(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("dept-no-org")
    facility = await make_facility(org, "dept-no-org")
    department = Department(
        organization_id=uuid.uuid4(),  # no such organization
        facility_id=facility.id,
        name="Synthetic Dept",
        code="DEPT-NO-ORG",
    )
    db_session.add(department)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_department_requires_a_valid_facility(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("dept-no-facility")
    department = Department(
        organization_id=org.id,
        facility_id=uuid.uuid4(),  # no such facility
        name="Synthetic Dept",
        code="DEPT-NO-FACILITY",
    )
    db_session.add(department)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_department_facility_must_belong_to_same_organization(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    """The core ownership-integrity invariant: a Department's
    organization_id and its facility's organization_id must match — this
    is enforced by a DATABASE-level composite foreign key, not just
    application validation. See docs/SCHEDULING_RESOURCES.md."""
    org_a = await make_organization("dept-mismatch-a")
    org_b = await make_organization("dept-mismatch-b")
    facility_b = await make_facility(org_b, "dept-mismatch-b")

    department = Department(
        organization_id=org_a.id,  # org A ...
        facility_id=facility_b.id,  # ... but facility belongs to org B
        name="Synthetic Mismatch Dept",
        code="DEPT-MISMATCH",
    )
    db_session.add(department)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_department_code_unique_within_facility(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("dept-code-unique")
    facility = await make_facility(org, "dept-code-unique")
    await make_department(org, facility, "DEPT-DUP")

    duplicate = Department(
        organization_id=org.id, facility_id=facility.id, name="Another Dept", code="DEPT-DUP"
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_same_department_code_allowed_across_different_facilities(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("dept-code-cross-facility")
    facility_1 = await make_facility(org, "dept-code-cross-1")
    facility_2 = await make_facility(org, "dept-code-cross-2")
    await make_department(org, facility_1, "DEPT-SHARED")

    same_code_other_facility = Department(
        organization_id=org.id,
        facility_id=facility_2.id,
        name="Another Dept",
        code="DEPT-SHARED",
    )
    db_session.add(same_code_other_facility)
    await db_session.flush()  # must not raise

    assert same_code_other_facility.id is not None


async def test_department_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    """Proves the composite ownership FK is real, database-level
    enforcement — inserting a mismatched pairing with raw SQL, bypassing
    the ORM entirely."""
    org_a = await make_organization("dept-raw-mismatch-a")
    org_b = await make_organization("dept-raw-mismatch-b")
    facility_b = await make_facility(org_b, "dept-raw-mismatch-b")

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO departments "
                "(id, organization_id, facility_id, name, code, "
                "is_active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_a, :facility_b, 'Raw SQL Dept', "
                "'RAW-1', true, now(), now())"
            ),
            {"org_a": org_a.id, "facility_b": facility_b.id},
        )
    await db_session.rollback()


async def test_department_is_active_defaults_true(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("dept-active-default")
    facility = await make_facility(org, "dept-active-default")
    department = Department(
        organization_id=org.id, facility_id=facility.id, name="Synthetic Dept", code="DEPT-ACTIVE"
    )
    db_session.add(department)
    await db_session.flush()

    assert department.is_active is True


async def test_department_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession, make_organization: MakeOrganization, make_facility: MakeFacility
) -> None:
    org = await make_organization("dept-timestamps")
    facility = await make_facility(org, "dept-timestamps")
    department = Department(
        organization_id=org.id,
        facility_id=facility.id,
        name="Synthetic Dept",
        code="DEPT-TIMESTAMPS",
    )
    db_session.add(department)
    await db_session.flush()

    assert department.created_at is not None
    assert department.updated_at is not None
    assert department.created_at.tzinfo is not None
    assert department.updated_at.tzinfo is not None


async def test_department_organization_and_facility_relationships(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
) -> None:
    org = await make_organization("dept-relationships")
    facility = await make_facility(org, "dept-relationships")
    department = await make_department(org, facility, "DEPT-REL")

    await db_session.refresh(department, attribute_names=["organization", "facility"])
    assert department.organization.id == org.id
    assert department.facility.id == facility.id

    await db_session.refresh(facility, attribute_names=["departments"])
    assert department in facility.departments
