"""app.repositories.patient_document tests against real PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

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
from app.repositories import patient_document as patient_document_repository

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


async def test_get_by_id_returns_document_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-get"
    )
    document = await make_patient_document(org, patient, user.id)

    result = await patient_document_repository.get_by_id(
        db_session, organization_id=org.id, document_id=document.id
    )

    assert result is not None
    assert result.id == document.id


async def test_get_by_id_returns_none_for_cross_tenant_document(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org_a = await make_organization("repo-doc-cross-a")
    org_b, user_b, patient_b = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-cross-b"
    )
    document_b = await make_patient_document(org_b, patient_b, user_b.id)

    result = await patient_document_repository.get_by_id(
        db_session, organization_id=org_a.id, document_id=document_b.id
    )

    assert result is None


async def test_get_by_id_returns_none_for_unknown_id(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
) -> None:
    org = await make_organization("repo-doc-unknown")

    result = await patient_document_repository.get_by_id(
        db_session, organization_id=org.id, document_id=uuid.uuid4()
    )

    assert result is None


async def test_list_by_patient_returns_only_that_patients_documents(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient_a = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-list-patient"
    )
    patient_b = await make_patient(org, "PN-repo-doc-list-patient-b")
    document_a = await make_patient_document(org, patient_a, user.id)
    await make_patient_document(org, patient_b, user.id)

    results = await patient_document_repository.list_by_patient(
        db_session, organization_id=org.id, patient_id=patient_a.id
    )

    assert [d.id for d in results] == [document_a.id]


async def test_list_by_patient_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org_a, user_a, patient_a = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-tenant-a"
    )
    org_b, user_b, patient_b = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-tenant-b"
    )
    await make_patient_document(org_a, patient_a, user_a.id)
    await make_patient_document(org_b, patient_b, user_b.id)

    results = await patient_document_repository.list_by_patient(
        db_session, organization_id=org_b.id, patient_id=patient_a.id
    )

    # patient_a does not exist in org_b's scope — no results, not an error.
    assert results == []


async def test_list_by_patient_orders_newest_first(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-order"
    )
    first = await make_patient_document(org, patient, user.id)
    second = await make_patient_document(org, patient, user.id)

    results = await patient_document_repository.list_by_patient(
        db_session, organization_id=org.id, patient_id=patient.id
    )

    assert [d.id for d in results] == [second.id, first.id]


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "repo-doc-create"
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

    created = await patient_document_repository.create(db_session, document)
    assert created.id is not None

    await db_session.rollback()

    results = await patient_document_repository.list_by_patient(
        db_session, organization_id=org.id, patient_id=patient.id
    )
    assert results == []
