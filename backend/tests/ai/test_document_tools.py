"""`app.ai.tools.document_tools` tests against real PostgreSQL.

`list_patient_documents` calls the REAL `PatientDocumentService` — never
a fake/hardcoded success path. Proves: patient self-scope is enforced
using SERVER-DERIVED identity (never a model-supplied `patient_id` for
a PATIENT caller), the output never includes `storage_key`/`sha256`/
`size_bytes`/`uploaded_by_user_id`, and cross-tenant/cross-patient
isolation holds.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.base import ToolExecutionContext, ToolResultStatus
from app.ai.tools.document_tools import (
    LIST_PATIENT_DOCUMENTS_TOOL,
    ListPatientDocumentsArguments,
    build_document_tool_registry,
)
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.patient_document import DocumentStatus, DocumentType, PatientDocument
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatientDocument = Callable[..., Awaitable[PatientDocument]]


def _context(
    *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role, patient_id: uuid.UUID | None
) -> ToolExecutionContext:
    return ToolExecutionContext(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
        patient_id=patient_id,
        workflow_run_id=uuid.uuid4(),
        workflow_step_id=uuid.uuid4(),
    )


def test_registry_get_returns_the_document_tool() -> None:
    registry = build_document_tool_registry()
    assert registry.get("list_patient_documents") is LIST_PATIENT_DOCUMENTS_TOOL


async def test_admin_lists_documents_for_a_specified_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("doc-tool-admin")
    admin = await make_user("doc-tool-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-doc-tool-admin")
    await make_patient_document(
        org, patient, admin.id, document_type=DocumentType.INSURANCE
    )
    await make_patient_document(
        org, patient, admin.id, document_type=DocumentType.CONSENT, status=DocumentStatus.PENDING
    )

    registry = build_document_tool_registry()
    context = _context(
        organization_id=org.id, user_id=admin.id, role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute(
        "list_patient_documents", {"patient_id": str(patient.id)}, context, db_session
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.code == "documents_listed"
    assert result.data is not None
    assert len(result.data["documents"]) == 2
    for document in result.data["documents"]:
        assert set(document.keys()) == {
            "id",
            "document_type",
            "status",
            "original_filename",
            "created_at",
        }
        assert "storage_key" not in document
        assert "sha256" not in document
        assert "size_bytes" not in document
        assert "uploaded_by_user_id" not in document


async def test_admin_without_patient_id_is_a_controlled_failure(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("doc-tool-admin-no-patient")
    admin = await make_user("doc-tool-admin-no-patient")
    await make_membership(org, admin, role=Role.ADMIN)

    registry = build_document_tool_registry()
    context = _context(
        organization_id=org.id, user_id=admin.id, role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute("list_patient_documents", {}, context, db_session)

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "patient_id_required"


async def test_patient_lists_own_documents_using_server_derived_identity(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("doc-tool-patient-self")
    patient_user = await make_user("doc-tool-patient-self")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(
        org, "PN-doc-tool-patient-self-own", user=patient_user
    )
    await make_patient_document(org, own_patient, patient_user.id)

    registry = build_document_tool_registry()
    context = _context(
        organization_id=org.id,
        user_id=patient_user.id,
        role=Role.PATIENT,
        patient_id=own_patient.id,
    )
    result = await registry.execute("list_patient_documents", {}, context, db_session)

    assert result.status is ToolResultStatus.SUCCESS
    assert len(result.data["documents"]) == 1  # type: ignore[index]


async def test_patient_cannot_list_another_patients_documents_via_model_argument(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    """MANDATORY adversarial case: a PATIENT caller's request results in
    a model decision that supplies ANOTHER patient's UUID as
    `patient_id`. The tool must ignore it and use the caller's own
    server-derived patient id."""
    org = await make_organization("doc-tool-patient-spoof")
    patient_user = await make_user("doc-tool-patient-spoof")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(
        org, "PN-doc-tool-patient-spoof-own", user=patient_user
    )
    other_patient = await make_patient(org, "PN-doc-tool-patient-spoof-other")
    await make_patient_document(org, own_patient, patient_user.id)
    await make_patient_document(org, other_patient, patient_user.id)

    registry = build_document_tool_registry()
    context = _context(
        organization_id=org.id,
        user_id=patient_user.id,
        role=Role.PATIENT,
        patient_id=own_patient.id,
    )
    result = await registry.execute(
        "list_patient_documents", {"patient_id": str(other_patient.id)}, context, db_session
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert len(result.data["documents"]) == 1  # type: ignore[index]


async def test_patient_with_no_linked_record_is_a_controlled_failure(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("doc-tool-unlinked-patient")
    patient_user = await make_user("doc-tool-unlinked-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)

    registry = build_document_tool_registry()
    context = _context(
        organization_id=org.id, user_id=patient_user.id, role=Role.PATIENT, patient_id=None
    )
    result = await registry.execute("list_patient_documents", {}, context, db_session)

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "patient_not_linked"


async def test_cross_tenant_patient_id_returns_empty_not_disclosed(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org_a = await make_organization("doc-tool-cross-a")
    admin_a = await make_user("doc-tool-cross-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)

    org_b = await make_organization("doc-tool-cross-b")
    admin_b = await make_user("doc-tool-cross-b")
    await make_membership(org_b, admin_b, role=Role.ADMIN)
    patient_b = await make_patient(org_b, "PN-doc-tool-cross-b")
    await make_patient_document(org_b, patient_b, admin_b.id)

    registry = build_document_tool_registry()
    context = _context(
        organization_id=org_a.id, user_id=admin_a.id, role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute(
        "list_patient_documents", {"patient_id": str(patient_b.id)}, context, db_session
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["documents"] == []  # type: ignore[index]


def test_arguments_schema_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ListPatientDocumentsArguments.model_validate(
            {"patient_id": str(uuid.uuid4()), "storage_key": "should-be-rejected"}
        )
