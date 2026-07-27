"""Practitioner model tests against real PostgreSQL.

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

from app.models.organization import Organization
from app.models.practitioner import Practitioner, PractitionerType

MakeOrganization = Callable[..., Awaitable[Organization]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]


async def test_practitioner_id_is_generated_uuid(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("prac-uuid")
    practitioner = Practitioner(
        organization_id=org.id,
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.PHYSICIAN,
    )
    db_session.add(practitioner)
    await db_session.flush()

    assert isinstance(practitioner.id, uuid.UUID)


async def test_practitioner_requires_a_valid_organization(db_session: AsyncSession) -> None:
    practitioner = Practitioner(
        organization_id=uuid.uuid4(),
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.PHYSICIAN,
    )
    db_session.add(practitioner)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_practitioner_type_persists_and_round_trips(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("prac-type-roundtrip")
    practitioner = Practitioner(
        organization_id=org.id,
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.PHYSIOTHERAPIST,
    )
    db_session.add(practitioner)
    await db_session.flush()
    practitioner_id = practitioner.id
    db_session.expire(practitioner)

    reloaded = await db_session.get(Practitioner, practitioner_id)
    assert reloaded is not None
    assert reloaded.practitioner_type is PractitionerType.PHYSIOTHERAPIST


async def test_practitioner_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    # Proves the CHECK constraint is real, database-level enforcement,
    # not just SQLAlchemy's application-side `validate_strings`.
    org = await make_organization("prac-check-constraint")
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO practitioners "
                "(id, organization_id, first_name, last_name, practitioner_type, "
                "is_active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, 'Raw', 'SQL', "
                "'not_a_real_type', true, now(), now())"
            ),
            {"org_id": org.id},
        )
    await db_session.rollback()


async def test_practitioner_name_whitespace_is_normalized(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("prac-name-normalize")
    practitioner = Practitioner(
        organization_id=org.id,
        first_name="  Synthetic   Middle  ",
        last_name="  Practitioner  ",
        practitioner_type=PractitionerType.THERAPIST,
    )
    db_session.add(practitioner)
    await db_session.flush()

    assert practitioner.first_name == "Synthetic Middle"
    assert practitioner.last_name == "Practitioner"


async def test_practitioner_is_active_defaults_true(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("prac-active-default")
    practitioner = Practitioner(
        organization_id=org.id,
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.COUNSELOR,
    )
    db_session.add(practitioner)
    await db_session.flush()

    assert practitioner.is_active is True


async def test_practitioner_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("prac-timestamps")
    practitioner = Practitioner(
        organization_id=org.id,
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.OTHER,
    )
    db_session.add(practitioner)
    await db_session.flush()

    assert practitioner.created_at is not None
    assert practitioner.updated_at is not None
    assert practitioner.created_at.tzinfo is not None
    assert practitioner.updated_at.tzinfo is not None


async def test_practitioner_organization_relationship(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("prac-org-relationship")
    practitioner = await make_practitioner(org)

    await db_session.refresh(practitioner, attribute_names=["organization"])
    assert practitioner.organization.id == org.id

    await db_session.refresh(org, attribute_names=["practitioners"])
    assert practitioner in org.practitioners
