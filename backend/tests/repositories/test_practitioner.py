"""app.repositories.practitioner tests against real PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.practitioner import Practitioner, PractitionerType
from app.repositories import practitioner as practitioner_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]


async def test_get_by_id_returns_practitioner_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("repo-prac-get")
    practitioner = await make_practitioner(org)

    result = await practitioner_repository.get_by_id(
        db_session, organization_id=org.id, practitioner_id=practitioner.id
    )

    assert result is not None
    assert result.id == practitioner.id


async def test_get_by_id_returns_none_for_wrong_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("repo-prac-wrong-org-a")
    org_b = await make_organization("repo-prac-wrong-org-b")
    practitioner = await make_practitioner(org_a)

    result = await practitioner_repository.get_by_id(
        db_session, organization_id=org_b.id, practitioner_id=practitioner.id
    )

    assert result is None


async def test_get_by_id_returns_none_for_unknown_practitioner(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-prac-unknown")

    result = await practitioner_repository.get_by_id(
        db_session, organization_id=org.id, practitioner_id=uuid.uuid4()
    )

    assert result is None


async def test_list_by_organization_returns_only_that_organizations_practitioners(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org_a = await make_organization("repo-prac-list-a")
    org_b = await make_organization("repo-prac-list-b")
    practitioner_a = await make_practitioner(org_a)
    await make_practitioner(org_b)

    results = await practitioner_repository.list_by_organization(
        db_session, organization_id=org_a.id
    )

    assert [p.id for p in results] == [practitioner_a.id]


async def test_list_by_organization_respects_limit_and_offset(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_practitioner: MakePractitioner,
) -> None:
    org = await make_organization("repo-prac-paging")
    for _ in range(5):
        await make_practitioner(org)

    first_page = await practitioner_repository.list_by_organization(
        db_session, organization_id=org.id, limit=2, offset=0
    )
    second_page = await practitioner_repository.list_by_organization(
        db_session, organization_id=org.id, limit=2, offset=2
    )

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {p.id for p in first_page}.isdisjoint({p.id for p in second_page})


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-prac-create-no-commit")
    practitioner = Practitioner(
        organization_id=org.id,
        first_name="Synthetic",
        last_name="Practitioner",
        practitioner_type=PractitionerType.PHYSICIAN,
    )

    created = await practitioner_repository.create(db_session, practitioner)
    assert created.id is not None

    await db_session.rollback()

    result = await practitioner_repository.get_by_id(
        db_session, organization_id=org.id, practitioner_id=practitioner.id
    )
    assert result is None
