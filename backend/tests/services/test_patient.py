"""app.services.patient.PatientService tests against real PostgreSQL.

Exercises the service directly (as in tests/auth/test_dependencies.py for
app.auth.dependencies) — no route is required to reach these behaviors.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.services.patient import (
    InvalidPatientLinkError,
    PatientNotFoundError,
    PatientNumberConflictError,
    PatientService,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]


async def test_create_patient_succeeds_with_no_user_link(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-create-basic")
    service = PatientService(db_session)

    patient = await service.create_patient(
        organization_id=org.id,
        patient_number="PN-SVC-CREATE",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )

    assert patient.id is not None
    assert patient.organization_id == org.id
    assert patient.user_id is None


async def test_create_patient_rejects_duplicate_patient_number(
    db_session: AsyncSession, make_organization: MakeOrganization, make_patient: MakePatient
) -> None:
    org = await make_organization("svc-dup-number")
    await make_patient(org, "PN-SVC-DUP")
    service = PatientService(db_session)

    with pytest.raises(PatientNumberConflictError):
        await service.create_patient(
            organization_id=org.id,
            patient_number="PN-SVC-DUP",
            first_name="Another",
            last_name="Patient",
            date_of_birth=date(1990, 1, 1),
        )


async def test_create_patient_rejects_future_date_of_birth(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-future-dob")
    service = PatientService(db_session)
    tomorrow = date.today() + timedelta(days=1)

    with pytest.raises(ValueError, match="date_of_birth"):
        await service.create_patient(
            organization_id=org.id,
            patient_number="PN-SVC-FUTURE-DOB",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=tomorrow,
        )


async def test_create_patient_succeeds_with_valid_patient_role_linkage(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("svc-valid-link")
    user = await make_user("svc-valid-link")
    await make_membership(org, user, role=Role.PATIENT)
    service = PatientService(db_session)

    patient = await service.create_patient(
        organization_id=org.id,
        patient_number="PN-SVC-VALID-LINK",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
        user_id=user.id,
    )

    assert patient.user_id == user.id


async def test_create_patient_rejects_link_with_no_membership(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
) -> None:
    org = await make_organization("svc-no-membership-link")
    user = await make_user("svc-no-membership-link")
    service = PatientService(db_session)

    with pytest.raises(InvalidPatientLinkError):
        await service.create_patient(
            organization_id=org.id,
            patient_number="PN-SVC-NO-MEMBERSHIP",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=date(1990, 1, 1),
            user_id=user.id,
        )


async def test_create_patient_rejects_link_with_wrong_role(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("svc-wrong-role-link")
    user = await make_user("svc-wrong-role-link")
    await make_membership(org, user, role=Role.STAFF)
    service = PatientService(db_session)

    with pytest.raises(InvalidPatientLinkError):
        await service.create_patient(
            organization_id=org.id,
            patient_number="PN-SVC-WRONG-ROLE",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=date(1990, 1, 1),
            user_id=user.id,
        )


async def test_create_patient_rejects_link_with_inactive_membership(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("svc-inactive-membership-link")
    user = await make_user("svc-inactive-membership-link")
    await make_membership(org, user, role=Role.PATIENT, is_active=False)
    service = PatientService(db_session)

    with pytest.raises(InvalidPatientLinkError):
        await service.create_patient(
            organization_id=org.id,
            patient_number="PN-SVC-INACTIVE-MEMBERSHIP",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=date(1990, 1, 1),
            user_id=user.id,
        )


async def test_create_patient_rejects_user_already_linked_in_same_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-user-already-linked")
    user = await make_user("svc-user-already-linked")
    await make_membership(org, user, role=Role.PATIENT)
    await make_patient(org, "PN-SVC-EXISTING-LINK", user=user)
    service = PatientService(db_session)

    with pytest.raises(InvalidPatientLinkError):
        await service.create_patient(
            organization_id=org.id,
            patient_number="PN-SVC-SECOND-LINK",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=date(1990, 1, 1),
            user_id=user.id,
        )


async def test_get_patient_returns_tenant_scoped_patient(
    db_session: AsyncSession, make_organization: MakeOrganization, make_patient: MakePatient
) -> None:
    org = await make_organization("svc-get-patient")
    patient = await make_patient(org, "PN-SVC-GET")
    service = PatientService(db_session)

    result = await service.get_patient(organization_id=org.id, patient_id=patient.id)

    assert result.id == patient.id


async def test_get_patient_raises_not_found_for_wrong_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("svc-get-wrong-org-a")
    org_b = await make_organization("svc-get-wrong-org-b")
    patient = await make_patient(org_a, "PN-SVC-WRONG-ORG")
    service = PatientService(db_session)

    with pytest.raises(PatientNotFoundError):
        await service.get_patient(organization_id=org_b.id, patient_id=patient.id)


async def test_get_patient_raises_not_found_for_unknown_id(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-get-unknown")
    service = PatientService(db_session)

    with pytest.raises(PatientNotFoundError):
        await service.get_patient(organization_id=org.id, patient_id=uuid.uuid4())


async def test_list_patients_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("svc-list-a")
    org_b = await make_organization("svc-list-b")
    patient_a = await make_patient(org_a, "PN-SVC-LIST-A")
    await make_patient(org_b, "PN-SVC-LIST-B")
    service = PatientService(db_session)

    results = await service.list_patients(organization_id=org_a.id)

    assert [p.id for p in results] == [patient_a.id]


async def test_get_own_patient_record_returns_linked_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-self-access")
    user = await make_user("svc-self-access")
    patient = await make_patient(org, "PN-SVC-SELF", user=user)
    service = PatientService(db_session)

    result = await service.get_own_patient_record(organization_id=org.id, user_id=user.id)

    assert result.id == patient.id


async def test_get_own_patient_record_raises_not_found_when_unlinked(
    db_session: AsyncSession, make_organization: MakeOrganization, make_user: MakeUser
) -> None:
    org = await make_organization("svc-self-access-none")
    user = await make_user("svc-self-access-none")
    service = PatientService(db_session)

    with pytest.raises(PatientNotFoundError):
        await service.get_own_patient_record(organization_id=org.id, user_id=user.id)


async def test_get_own_patient_record_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("svc-self-access-cross-a")
    org_b = await make_organization("svc-self-access-cross-b")
    user = await make_user("svc-self-access-cross")
    await make_patient(org_a, "PN-SVC-SELF-CROSS", user=user)
    service = PatientService(db_session)

    with pytest.raises(PatientNotFoundError):
        await service.get_own_patient_record(organization_id=org_b.id, user_id=user.id)
