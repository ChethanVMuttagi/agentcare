"""Patient model tests against real PostgreSQL.

This is an ADMINISTRATIVE patient record — see docs/PATIENTS.md. See
tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL and
how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakePatient = Callable[..., Awaitable[Patient]]


async def test_patient_id_is_generated_uuid(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-uuid")
    patient = Patient(
        organization_id=org.id,
        patient_number="PN-UUID-1",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)
    await db_session.flush()

    assert isinstance(patient.id, uuid.UUID)


async def test_patient_requires_a_valid_organization(db_session: AsyncSession) -> None:
    patient = Patient(
        organization_id=uuid.uuid4(),
        patient_number="PN-NO-ORG",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_patient_user_id_is_optional(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-no-user")
    patient = Patient(
        organization_id=org.id,
        patient_number="PN-NO-USER",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)
    await db_session.flush()

    assert patient.user_id is None


async def test_patient_requires_a_valid_user_when_linked(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-bad-user")
    patient = Patient(
        organization_id=org.id,
        user_id=uuid.uuid4(),
        patient_number="PN-BAD-USER",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_patient_number_unique_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("pat-num-unique")
    await make_patient(org, "PN-DUP")

    duplicate = Patient(
        organization_id=org.id,
        patient_number="PN-DUP",
        first_name="Another",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_same_patient_number_allowed_across_different_organizations(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("pat-num-cross-a")
    org_b = await make_organization("pat-num-cross-b")
    await make_patient(org_a, "PN-SHARED")

    same_number_other_org = Patient(
        organization_id=org_b.id,
        patient_number="PN-SHARED",
        first_name="Another",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(same_number_other_org)
    await db_session.flush()  # must not raise

    assert same_number_other_org.id is not None


async def test_user_linkage_unique_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("pat-user-link-unique")
    user = await make_user("pat-user-link-unique")
    await make_patient(org, "PN-LINK-1", user=user)

    duplicate_link = Patient(
        organization_id=org.id,
        user_id=user.id,
        patient_number="PN-LINK-2",
        first_name="Another",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(duplicate_link)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_same_user_may_be_linked_in_different_organizations(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("pat-user-cross-a")
    org_b = await make_organization("pat-user-cross-b")
    user = await make_user("pat-user-cross")
    await make_patient(org_a, "PN-CROSS-A", user=user)

    linked_in_org_b = Patient(
        organization_id=org_b.id,
        user_id=user.id,
        patient_number="PN-CROSS-B",
        first_name="Another",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(linked_in_org_b)
    await db_session.flush()  # must not raise

    assert linked_in_org_b.id is not None


async def test_multiple_unlinked_patients_allowed_in_same_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("pat-multi-unlinked")
    first = await make_patient(org, "PN-UNLINKED-1")
    second = await make_patient(org, "PN-UNLINKED-2")

    assert first.user_id is None
    assert second.user_id is None
    assert first.id != second.id


async def test_patient_rejects_future_date_of_birth_at_application_layer(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-future-dob")
    tomorrow = date.today() + timedelta(days=1)

    with pytest.raises(ValueError, match="date_of_birth"):
        Patient(
            organization_id=org.id,
            patient_number="PN-FUTURE-DOB",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=tomorrow,
        )


async def test_patient_name_whitespace_is_normalized(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-name-normalize")
    patient = Patient(
        organization_id=org.id,
        patient_number="PN-NAME-NORM",
        first_name="  Synthetic   Middle  ",
        last_name="  Patient  ",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)
    await db_session.flush()

    assert patient.first_name == "Synthetic Middle"
    assert patient.last_name == "Patient"


async def test_patient_number_whitespace_is_stripped_but_case_preserved(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-number-normalize")
    patient = Patient(
        organization_id=org.id,
        patient_number="  MRN-AbC-001  ",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)
    await db_session.flush()

    assert patient.patient_number == "MRN-AbC-001"


async def test_patient_is_active_defaults_true(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-active-default")
    patient = Patient(
        organization_id=org.id,
        patient_number="PN-ACTIVE-DEFAULT",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)
    await db_session.flush()

    assert patient.is_active is True


async def test_patient_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("pat-timestamps")
    patient = Patient(
        organization_id=org.id,
        patient_number="PN-TIMESTAMPS",
        first_name="Synthetic",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(patient)
    await db_session.flush()

    assert patient.created_at is not None
    assert patient.updated_at is not None
    assert patient.created_at.tzinfo is not None
    assert patient.updated_at.tzinfo is not None


async def test_patient_organization_relationship(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("pat-org-relationship")
    patient = await make_patient(org, "PN-ORG-REL")

    await db_session.refresh(patient, attribute_names=["organization"])
    assert patient.organization.id == org.id

    await db_session.refresh(org, attribute_names=["patients"])
    assert patient in org.patients


async def test_patient_user_relationship(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("pat-user-relationship")
    user = await make_user("pat-user-relationship")
    patient = await make_patient(org, "PN-USER-REL", user=user)

    await db_session.refresh(patient, attribute_names=["user"])
    assert patient.user is not None
    assert patient.user.id == user.id

    await db_session.refresh(user, attribute_names=["patients"])
    assert patient in user.patients
