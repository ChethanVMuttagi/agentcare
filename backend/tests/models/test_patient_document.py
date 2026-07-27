"""PatientDocument model tests against real PostgreSQL.

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

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.patient_document import (
    DocumentMediaType,
    DocumentStatus,
    DocumentType,
    PatientDocument,
)
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]
MakePatientDocument = Callable[..., Awaitable[PatientDocument]]


async def _scenario(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    suffix: str,
) -> tuple[Organization, User, Patient]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    patient = await make_patient(org, f"PN-{suffix}")
    return org, user, patient


async def test_document_id_is_generated_uuid(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-uuid"
    )

    document = PatientDocument(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        status=DocumentStatus.PENDING,
        original_filename="synthetic.pdf",
        storage_key=f"{org.id.hex}/{uuid.uuid4().hex}",
        media_type=DocumentMediaType.PDF,
    )
    db_session.add(document)
    await db_session.flush()

    assert isinstance(document.id, uuid.UUID)


async def test_status_defaults_to_pending(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-default-status"
    )

    document = PatientDocument(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="synthetic.pdf",
        storage_key=f"{org.id.hex}/{uuid.uuid4().hex}",
        media_type=DocumentMediaType.PDF,
    )
    db_session.add(document)
    await db_session.flush()

    assert document.status is DocumentStatus.PENDING
    assert document.size_bytes is None
    assert document.sha256 is None


async def test_available_requires_size_and_hash(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-avail-check"
    )

    document = PatientDocument(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        status=DocumentStatus.AVAILABLE,
        original_filename="synthetic.pdf",
        storage_key=f"{org.id.hex}/{uuid.uuid4().hex}",
        media_type=DocumentMediaType.PDF,
    )
    db_session.add(document)

    with pytest.raises(IntegrityError, match="available_has_size_and_hash"):
        await db_session.flush()
    await db_session.rollback()


async def test_available_with_size_and_hash_succeeds(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-avail-ok"
    )

    document = PatientDocument(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        status=DocumentStatus.AVAILABLE,
        original_filename="synthetic.pdf",
        storage_key=f"{org.id.hex}/{uuid.uuid4().hex}",
        media_type=DocumentMediaType.PDF,
        size_bytes=1234,
        sha256="a" * 64,
    )
    db_session.add(document)
    await db_session.flush()  # must NOT raise

    assert document.id is not None


async def test_rejects_cross_tenant_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a, user_a, _patient_a = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-cross-a"
    )
    org_b = await make_organization("doc-cross-b")
    patient_b = await make_patient(org_b, "PN-doc-cross-b")

    document = PatientDocument(
        organization_id=org_a.id,
        patient_id=patient_b.id,
        uploaded_by_user_id=user_a.id,
        document_type=DocumentType.OTHER,
        original_filename="synthetic.pdf",
        storage_key=f"{org_a.id.hex}/{uuid.uuid4().hex}",
        media_type=DocumentMediaType.PDF,
    )
    db_session.add(document)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_rejects_uploader_with_no_membership_in_this_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("doc-no-member")
    patient = await make_patient(org, "PN-doc-no-member")
    outsider = await make_user("doc-no-member-outsider")  # never a member of `org`

    document = PatientDocument(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=outsider.id,
        document_type=DocumentType.OTHER,
        original_filename="synthetic.pdf",
        storage_key=f"{org.id.hex}/{uuid.uuid4().hex}",
        media_type=DocumentMediaType.PDF,
    )
    db_session.add(document)

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_storage_key_must_be_unique(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-dup-key"
    )
    shared_key = f"{org.id.hex}/{uuid.uuid4().hex}"
    await make_patient_document(org, patient, user.id, storage_key=shared_key)

    duplicate = PatientDocument(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        status=DocumentStatus.PENDING,
        original_filename="synthetic-2.pdf",
        storage_key=shared_key,
        media_type=DocumentMediaType.PDF,
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError, match="uq_patient_documents_storage_key"):
        await db_session.flush()
    await db_session.rollback()


async def test_document_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-raw-type"
    )

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO patient_documents "
                "(id, organization_id, patient_id, uploaded_by_user_id, document_type, "
                "status, original_filename, storage_key, media_type, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :patient_id, :user_id, 'bogus_type', "
                "'pending', 'f.pdf', :storage_key, 'application/pdf', now(), now())"
            ),
            {
                "org_id": org.id,
                "patient_id": patient.id,
                "user_id": user.id,
                "storage_key": f"{org.id.hex}/{uuid.uuid4().hex}",
            },
        )
    await db_session.rollback()


async def test_document_status_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-raw-status"
    )

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO patient_documents "
                "(id, organization_id, patient_id, uploaded_by_user_id, document_type, "
                "status, original_filename, storage_key, media_type, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :patient_id, :user_id, 'other', "
                "'bogus_status', 'f.pdf', :storage_key, 'application/pdf', now(), now())"
            ),
            {
                "org_id": org.id,
                "patient_id": patient.id,
                "user_id": user.id,
                "storage_key": f"{org.id.hex}/{uuid.uuid4().hex}",
            },
        )
    await db_session.rollback()


async def test_media_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-raw-media"
    )

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO patient_documents "
                "(id, organization_id, patient_id, uploaded_by_user_id, document_type, "
                "status, original_filename, storage_key, media_type, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :patient_id, :user_id, 'other', "
                "'pending', 'f.pdf', :storage_key, 'application/x-msdownload', now(), now())"
            ),
            {
                "org_id": org.id,
                "patient_id": patient.id,
                "user_id": user.id,
                "storage_key": f"{org.id.hex}/{uuid.uuid4().hex}",
            },
        )
    await db_session.rollback()


async def test_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-timestamps"
    )
    document = await make_patient_document(org, patient, user.id)

    assert document.created_at is not None
    assert document.updated_at is not None
    assert document.created_at.tzinfo is not None


async def test_relationships(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-relationships"
    )
    document = await make_patient_document(org, patient, user.id)

    await db_session.refresh(document, attribute_names=["patient", "organization"])
    assert document.patient.id == patient.id
    assert document.organization.id == org.id


async def test_repr_excludes_original_filename(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "doc-repr"
    )
    document = await make_patient_document(
        org, patient, user.id, original_filename="sensitive-name.pdf"
    )

    assert "sensitive-name.pdf" not in repr(document)
