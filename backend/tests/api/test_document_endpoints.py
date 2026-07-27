"""Patient document endpoint tests: upload, listing, retrieval, download,
deletion — end-to-end over real HTTP (via `client_with_storage`), against
real PostgreSQL and a temporary-directory-backed `LocalDocumentStorage`.
See docs/DOCUMENTS.md for the full RBAC matrix and privacy guarantees
this covers.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.patient_document import DocumentStatus, PatientDocument
from app.models.user import User

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]
MakePatientDocument = Callable[..., Awaitable[PatientDocument]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"
_VALID_PDF = b"%PDF-1.4\n" + (b"synthetic administrative document content " * 20)


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _documents_url(
    organization: Organization, patient: Patient, suffix: str = ""
) -> str:
    return f"/api/v1/organizations/{organization.id}/patients/{patient.id}/documents{suffix}"


def _upload_files(filename: str = "id-card.pdf", content: bytes = _VALID_PDF) -> dict:
    return {"file": (filename, content, "application/pdf")}


# --- POST .../documents (upload) -------------------------------------------


async def test_admin_can_upload_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-upload-admin")
    admin = await make_user("api-doc-upload-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-upload-admin")

    response = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(admin),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "available"
    assert body["document_type"] == "identity"
    assert body["original_filename"] == "id-card.pdf"
    assert body["patient_id"] == str(patient.id)
    assert "storage_key" not in body


async def test_staff_can_upload_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-upload-staff")
    staff = await make_user("api-doc-upload-staff")
    await make_membership(org, staff, role=Role.STAFF)
    patient = await make_patient(org, "PN-api-doc-upload-staff")

    response = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "insurance"},
        files=_upload_files(),
        headers=_auth_header(staff),
    )

    assert response.status_code == 201


async def test_patient_can_upload_own_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-upload-patient")
    patient_user = await make_user("api-doc-upload-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-doc-upload-patient", user=patient_user)

    response = await client_with_storage.post(
        _documents_url(org, own_patient),
        data={"document_type": "consent"},
        files=_upload_files(),
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 201
    assert response.json()["patient_id"] == str(own_patient.id)


async def test_patient_cannot_upload_for_another_patient(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-upload-spoof")
    other_patient = await make_patient(org, "PN-api-doc-upload-spoof-other")
    patient_user = await make_user("api-doc-upload-spoof")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-doc-upload-spoof-own", user=patient_user)

    # Attempts to upload directly into another patient's URL.
    response = await client_with_storage.post(
        _documents_url(org, other_patient),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 404


async def test_upload_rejects_unsupported_file_type(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-bad-type")
    admin = await make_user("api-doc-bad-type")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-bad-type")

    response = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "identity"},
        files=_upload_files(
            filename="fake.pdf", content=b"just plain text, not a real document"
        ),
        headers=_auth_header(admin),
    )

    assert response.status_code == 422


async def test_upload_rejects_svg(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-svg")
    admin = await make_user("api-doc-svg")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-svg")

    response = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "other"},
        files=_upload_files(
            filename="image.svg",
            content=b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>',
        ),
        headers=_auth_header(admin),
    )

    assert response.status_code == 422


async def test_upload_rejects_oversized_file(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "32")
    get_settings.cache_clear()
    org = await make_organization("api-doc-oversized")
    admin = await make_user("api-doc-oversized")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-oversized")

    response = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(admin),
    )

    assert response.status_code == 413
    get_settings.cache_clear()


async def test_upload_rejects_cross_tenant_patient_url(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("api-doc-upload-cross-a")
    org_b = await make_organization("api-doc-upload-cross-b")
    admin_a = await make_user("api-doc-upload-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    patient_b = await make_patient(org_b, "PN-api-doc-upload-cross-b")

    response = await client_with_storage.post(
        _documents_url(org_a, patient_b),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(admin_a),
    )

    assert response.status_code == 404


# --- GET .../documents (list) -----------------------------------------------


async def test_admin_can_list_documents(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-list-admin")
    admin = await make_user("api-doc-list-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-list-admin")
    await make_patient_document(org, patient, admin.id)

    response = await client_with_storage.get(
        _documents_url(org, patient), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert len(response.json()["documents"]) == 1


async def test_patient_cannot_list_another_patients_documents(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-list-patient-other")
    admin = await make_user("api-doc-list-patient-other-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    other_patient = await make_patient(org, "PN-api-doc-list-patient-other")
    await make_patient_document(org, other_patient, admin.id)
    patient_user = await make_user("api-doc-list-patient-other")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-doc-list-patient-other-own", user=patient_user)

    response = await client_with_storage.get(
        _documents_url(org, other_patient), headers=_auth_header(patient_user)
    )

    assert response.status_code == 404


async def test_cross_tenant_list_rejected(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a = await make_organization("api-doc-list-cross-a")
    org_b = await make_organization("api-doc-list-cross-b")
    admin_a = await make_user("api-doc-list-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    patient_b = await make_patient(org_b, "PN-api-doc-list-cross-b")

    response = await client_with_storage.get(
        _documents_url(org_a, patient_b), headers=_auth_header(admin_a)
    )

    assert response.status_code == 404


# --- GET .../documents/{id} ---------------------------------------------------


async def test_admin_can_get_document_by_id(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-get-admin")
    admin = await make_user("api-doc-get-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-get-admin")
    document = await make_patient_document(org, patient, admin.id)

    response = await client_with_storage.get(
        _documents_url(org, patient, f"/{document.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(document.id)


async def test_cross_tenant_document_retrieval_rejected(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org_a = await make_organization("api-doc-get-cross-a")
    admin_a = await make_user("api-doc-get-cross-admin-a")
    await make_membership(org_a, admin_a, role=Role.ADMIN)
    patient_a = await make_patient(org_a, "PN-api-doc-get-cross-a")

    org_b = await make_organization("api-doc-get-cross-b")
    admin_b = await make_user("api-doc-get-cross-admin-b")
    await make_membership(org_b, admin_b, role=Role.ADMIN)
    patient_b = await make_patient(org_b, "PN-api-doc-get-cross-b")
    document_b = await make_patient_document(org_b, patient_b, admin_b.id)

    response = await client_with_storage.get(
        _documents_url(org_a, patient_a, f"/{document_b.id}"), headers=_auth_header(admin_a)
    )

    assert response.status_code == 404


async def test_patient_cannot_get_another_patients_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-get-patient-other")
    admin = await make_user("api-doc-get-patient-other-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    other_patient = await make_patient(org, "PN-api-doc-get-patient-other")
    document = await make_patient_document(org, other_patient, admin.id)
    patient_user = await make_user("api-doc-get-patient-other")
    await make_membership(org, patient_user, role=Role.PATIENT)
    await make_patient(org, "PN-api-doc-get-patient-other-own", user=patient_user)

    response = await client_with_storage.get(
        _documents_url(org, other_patient, f"/{document.id}"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 404


# --- GET .../documents/{id}/download -----------------------------------------


async def test_download_returns_matching_content_and_safe_headers(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-download")
    admin = await make_user("api-doc-download")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-download")
    upload = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "identity"},
        files=_upload_files(filename='weird "name".pdf'),
        headers=_auth_header(admin),
    )
    document_id = upload.json()["id"]

    response = await client_with_storage.get(
        _documents_url(org, patient, f"/{document_id}/download"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.content == _VALID_PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    content_disposition = response.headers["content-disposition"]
    assert content_disposition.startswith("attachment;")
    assert "weird" in content_disposition
    # The original filename contained embedded double-quotes — they must
    # never break out of the quoted filename parameter.
    filename_param = content_disposition.split("filename=", 1)[1]
    assert filename_param.count('"') == 2  # only the wrapping quotes remain


async def test_patient_can_download_own_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-download-patient")
    patient_user = await make_user("api-doc-download-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(org, "PN-api-doc-download-patient", user=patient_user)
    upload = await client_with_storage.post(
        _documents_url(org, own_patient),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(patient_user),
    )
    document_id = upload.json()["id"]

    response = await client_with_storage.get(
        _documents_url(org, own_patient, f"/{document_id}/download"),
        headers=_auth_header(patient_user),
    )

    assert response.status_code == 200
    assert response.content == _VALID_PDF


async def test_download_non_available_document_returns_409(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-download-pending")
    admin = await make_user("api-doc-download-pending")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-download-pending")
    document = await make_patient_document(
        org, patient, admin.id, status=DocumentStatus.PENDING
    )

    response = await client_with_storage.get(
        _documents_url(org, patient, f"/{document.id}/download"), headers=_auth_header(admin)
    )

    assert response.status_code == 409


# --- DELETE .../documents/{id} ------------------------------------------------


async def test_admin_can_delete_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-delete-admin")
    admin = await make_user("api-doc-delete-admin")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-delete-admin")
    upload = await client_with_storage.post(
        _documents_url(org, patient),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(admin),
    )
    document_id = upload.json()["id"]

    response = await client_with_storage.delete(
        _documents_url(org, patient, f"/{document_id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


async def test_staff_can_delete_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-delete-staff")
    staff = await make_user("api-doc-delete-staff")
    await make_membership(org, staff, role=Role.STAFF)
    patient = await make_patient(org, "PN-api-doc-delete-staff")
    document = await make_patient_document(org, patient, staff.id)

    response = await client_with_storage.delete(
        _documents_url(org, patient, f"/{document.id}"), headers=_auth_header(staff)
    )

    assert response.status_code == 200


async def test_patient_cannot_delete_document(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-delete-patient-forbidden")
    patient_user = await make_user("api-doc-delete-patient-forbidden")
    await make_membership(org, patient_user, role=Role.PATIENT)
    own_patient = await make_patient(
        org, "PN-api-doc-delete-patient-forbidden", user=patient_user
    )
    upload = await client_with_storage.post(
        _documents_url(org, own_patient),
        data={"document_type": "identity"},
        files=_upload_files(),
        headers=_auth_header(patient_user),
    )
    document_id = upload.json()["id"]

    response = await client_with_storage.delete(
        _documents_url(org, own_patient, f"/{document_id}"), headers=_auth_header(patient_user)
    )

    assert response.status_code == 403


async def test_delete_already_deleted_document_returns_422(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: MakePatientDocument,
) -> None:
    org = await make_organization("api-doc-delete-twice")
    admin = await make_user("api-doc-delete-twice")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-delete-twice")
    document = await make_patient_document(
        org, patient, admin.id, status=DocumentStatus.DELETED
    )

    response = await client_with_storage.delete(
        _documents_url(org, patient, f"/{document.id}"), headers=_auth_header(admin)
    )

    assert response.status_code == 422


async def test_unknown_document_id_returns_404(
    client_with_storage: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-doc-unknown")
    admin = await make_user("api-doc-unknown")
    await make_membership(org, admin, role=Role.ADMIN)
    patient = await make_patient(org, "PN-api-doc-unknown")

    response = await client_with_storage.get(
        _documents_url(org, patient, f"/{uuid.uuid4()}"), headers=_auth_header(admin)
    )

    assert response.status_code == 404
