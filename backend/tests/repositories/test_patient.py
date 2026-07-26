"""app.repositories.patient tests against real PostgreSQL.

Every query function here requires an `organization_id` — see
docs/PATIENTS.md "Tenant Ownership". These tests exercise the module's
functions directly, the same pattern tests/auth/test_dependencies.py uses
for app.auth.dependencies.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.repositories import patient as patient_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakePatient = Callable[..., Awaitable[Patient]]


async def test_get_by_id_returns_patient_within_organization(
    db_session: AsyncSession, make_organization: MakeOrganization, make_patient: MakePatient
) -> None:
    org = await make_organization("repo-get-by-id")
    patient = await make_patient(org, "PN-REPO-GET")

    result = await patient_repository.get_by_id(
        db_session, organization_id=org.id, patient_id=patient.id
    )

    assert result is not None
    assert result.id == patient.id


async def test_get_by_id_returns_none_for_wrong_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("repo-get-wrong-org-a")
    org_b = await make_organization("repo-get-wrong-org-b")
    patient = await make_patient(org_a, "PN-REPO-WRONG-ORG")

    result = await patient_repository.get_by_id(
        db_session, organization_id=org_b.id, patient_id=patient.id
    )

    assert result is None


async def test_get_by_id_returns_none_for_unknown_patient(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-get-unknown")

    result = await patient_repository.get_by_id(
        db_session, organization_id=org.id, patient_id=uuid.uuid4()
    )

    assert result is None


async def test_get_by_user_id_returns_linked_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("repo-get-by-user")
    user = await make_user("repo-get-by-user")
    patient = await make_patient(org, "PN-REPO-USER", user=user)

    result = await patient_repository.get_by_user_id(
        db_session, organization_id=org.id, user_id=user.id
    )

    assert result is not None
    assert result.id == patient.id


async def test_get_by_user_id_isolated_across_organizations(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("repo-user-cross-a")
    org_b = await make_organization("repo-user-cross-b")
    user = await make_user("repo-user-cross")
    await make_patient(org_a, "PN-REPO-USER-CROSS-A", user=user)

    result = await patient_repository.get_by_user_id(
        db_session, organization_id=org_b.id, user_id=user.id
    )

    assert result is None


async def test_get_by_patient_number_returns_match(
    db_session: AsyncSession, make_organization: MakeOrganization, make_patient: MakePatient
) -> None:
    org = await make_organization("repo-get-by-number")
    patient = await make_patient(org, "PN-REPO-NUMBER")

    result = await patient_repository.get_by_patient_number(
        db_session, organization_id=org.id, patient_number="PN-REPO-NUMBER"
    )

    assert result is not None
    assert result.id == patient.id


async def test_list_by_organization_returns_only_that_organizations_patients(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("repo-list-a")
    org_b = await make_organization("repo-list-b")
    patient_a1 = await make_patient(org_a, "PN-REPO-LIST-A1")
    patient_a2 = await make_patient(org_a, "PN-REPO-LIST-A2")
    await make_patient(org_b, "PN-REPO-LIST-B1")

    results = await patient_repository.list_by_organization(db_session, organization_id=org_a.id)
    result_ids = {patient.id for patient in results}

    assert result_ids == {patient_a1.id, patient_a2.id}


async def test_list_by_organization_respects_limit_and_offset(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("repo-list-paging")
    for i in range(5):
        await make_patient(org, f"PN-REPO-PAGE-{i}")

    first_page = await patient_repository.list_by_organization(
        db_session, organization_id=org.id, limit=2, offset=0
    )
    second_page = await patient_repository.list_by_organization(
        db_session, organization_id=org.id, limit=2, offset=2
    )

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {p.id for p in first_page}.isdisjoint({p.id for p in second_page})


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-create-no-commit")
    patient = Patient(
        organization_id=org.id,
        patient_number="PN-REPO-CREATE",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )

    created = await patient_repository.create(db_session, patient)
    assert created.id is not None  # flush assigned/confirmed the row exists

    # Rolling back the session must discard it -- proving `create()` only
    # flushed, and never committed, the row.
    await db_session.rollback()

    result = await patient_repository.get_by_id(
        db_session, organization_id=org.id, patient_id=patient.id
    )
    assert result is None
