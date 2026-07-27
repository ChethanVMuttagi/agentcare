"""app.services.document.PatientDocumentService tests against real
PostgreSQL, using a temporary-directory-backed `LocalDocumentStorage`
(never the real configured `DOCUMENT_STORAGE_PATH`)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.patient_document import DocumentStatus, DocumentType
from app.models.user import User
from app.services.document import (
    DocumentDeletionFailedError,
    DocumentNotAvailableError,
    DocumentNotFoundError,
    DocumentPatientInactiveError,
    DocumentPatientNotFoundError,
    DocumentTooLargeError,
    InvalidDocumentTransitionError,
    PatientDocumentService,
    UnsupportedFileTypeError,
    UploaderNotActiveMemberError,
)
from app.storage.base import DocumentStorage
from app.storage.local import LocalDocumentStorage

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]

_VALID_PDF = b"%PDF-1.4\n" + (b"synthetic administrative document content " * 20)
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024


class _BytesStream:
    """A minimal `AsyncReadable` (see `app.services.document.AsyncReadable`)
    over an in-memory `bytes` buffer — the test-only stand-in for
    `fastapi.UploadFile`."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk


class _StorageThatFailsOnPut:
    """A `DocumentStorage` double whose `put()` always raises — used to
    prove the upload state machine's failure path (mark FAILED, never
    leave an orphaned AVAILABLE row)."""

    async def put(self, storage_key: str, chunks: AsyncIterator[bytes]) -> int:
        raise RuntimeError("synthetic storage failure")

    async def open_read_stream(self, storage_key: str) -> AsyncIterator[bytes]:
        raise AssertionError("not expected to be called in this test")

    async def delete(self, storage_key: str) -> None:
        return None

    async def exists(self, storage_key: str) -> bool:
        return False


class _StorageThatFailsOnDelete:
    """Wraps a real `DocumentStorage`, but `delete()` always raises —
    used to prove `delete_document` leaves status UNCHANGED on failure."""

    def __init__(self, wrapped: DocumentStorage) -> None:
        self._wrapped = wrapped

    async def put(self, storage_key: str, chunks: AsyncIterator[bytes]) -> int:
        return await self._wrapped.put(storage_key, chunks)

    async def open_read_stream(self, storage_key: str) -> AsyncIterator[bytes]:
        return await self._wrapped.open_read_stream(storage_key)

    async def delete(self, storage_key: str) -> None:
        raise RuntimeError("synthetic delete failure")

    async def exists(self, storage_key: str) -> bool:
        return await self._wrapped.exists(storage_key)


async def _scenario(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    suffix: str,
    *,
    role: Role = Role.ADMIN,
) -> tuple[Organization, User, Patient]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=role)
    patient = await make_patient(org, f"PN-{suffix}")
    return org, user, patient


def _service(
    db_session: AsyncSession,
    storage: DocumentStorage,
    *,
    max_upload_bytes: int = _DEFAULT_MAX_BYTES,
) -> PatientDocumentService:
    return PatientDocumentService(db_session, storage, max_upload_bytes=max_upload_bytes)


# --- upload_document: success ----------------------------------------------


async def test_upload_document_succeeds(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-upload-ok"
    )
    service = _service(db_session, local_storage)

    document = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.IDENTITY,
        original_filename="id-card.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    assert document.status is DocumentStatus.AVAILABLE
    assert document.size_bytes == len(_VALID_PDF)
    assert document.sha256 == hashlib.sha256(_VALID_PDF).hexdigest()
    assert document.original_filename == "id-card.pdf"
    assert await local_storage.exists(document.storage_key) is True


async def test_upload_document_storage_key_is_opaque(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-opaque-key"
    )
    service = _service(db_session, local_storage)

    document = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.IDENTITY,
        original_filename="patient-name-jane-doe.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    # Never derived from filename/patient identity.
    assert "patient-name-jane-doe" not in document.storage_key
    assert "jane" not in document.storage_key.lower()
    # Matches the documented shape: "<org-hex>/<uuid-hex>".
    prefix, _, suffix = document.storage_key.partition("/")
    assert prefix == org.id.hex
    assert uuid.UUID(suffix)  # parses cleanly as a UUID hex string


async def test_upload_document_duplicate_filenames_produce_distinct_documents(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-dup-filename"
    )
    service = _service(db_session, local_storage)

    first = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="scan.pdf",
        stream=_BytesStream(_VALID_PDF),
    )
    second = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="scan.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    assert first.id != second.id
    assert first.storage_key != second.storage_key
    assert first.original_filename == second.original_filename == "scan.pdf"


# --- upload_document: validation rejections (no row created) --------------


async def test_upload_document_rejects_unknown_patient(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("svc-doc-unknown-patient")
    user = await make_user("svc-doc-unknown-patient")
    await make_membership(org, user, role=Role.ADMIN)
    service = _service(db_session, local_storage)

    with pytest.raises(DocumentPatientNotFoundError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=uuid.uuid4(),
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="f.pdf",
            stream=_BytesStream(_VALID_PDF),
        )


async def test_upload_document_rejects_inactive_patient(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-doc-inactive-patient")
    user = await make_user("svc-doc-inactive-patient")
    await make_membership(org, user, role=Role.ADMIN)
    patient = await make_patient(org, "PN-svc-doc-inactive-patient", is_active=False)
    service = _service(db_session, local_storage)

    with pytest.raises(DocumentPatientInactiveError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="f.pdf",
            stream=_BytesStream(_VALID_PDF),
        )


async def test_upload_document_rejects_uploader_with_no_membership(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-doc-no-member")
    patient = await make_patient(org, "PN-svc-doc-no-member")
    outsider = await make_user("svc-doc-no-member-outsider")
    service = _service(db_session, local_storage)

    with pytest.raises(UploaderNotActiveMemberError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=outsider.id,
            document_type=DocumentType.OTHER,
            original_filename="f.pdf",
            stream=_BytesStream(_VALID_PDF),
        )


async def test_upload_document_rejects_uploader_with_inactive_membership(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("svc-doc-inactive-member")
    user = await make_user("svc-doc-inactive-member")
    await make_membership(org, user, role=Role.ADMIN, is_active=False)
    patient = await make_patient(org, "PN-svc-doc-inactive-member")
    service = _service(db_session, local_storage)

    with pytest.raises(UploaderNotActiveMemberError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="f.pdf",
            stream=_BytesStream(_VALID_PDF),
        )


async def test_upload_document_rejects_unsupported_file_type_and_creates_no_row(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-bad-type"
    )
    service = _service(db_session, local_storage)

    with pytest.raises(UnsupportedFileTypeError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="not-really-a-pdf.pdf",
            stream=_BytesStream(b"just plain text, not a real document"),
        )

    documents = await service.list_documents_for_patient(
        organization_id=org.id, patient_id=patient.id
    )
    assert documents == []


async def test_upload_document_rejects_empty_file(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-empty"
    )
    service = _service(db_session, local_storage)

    with pytest.raises(UnsupportedFileTypeError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="empty.pdf",
            stream=_BytesStream(b""),
        )


# --- upload_document: oversized (accepted signature, then fails mid-stream) --


async def test_upload_document_rejects_oversized_upload_and_marks_failed(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-oversized"
    )
    # A tiny max so the synthetic PDF (already well over the signature
    # header size) is guaranteed to exceed it while streaming.
    service = _service(db_session, local_storage, max_upload_bytes=32)

    with pytest.raises(DocumentTooLargeError):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="big.pdf",
            stream=_BytesStream(_VALID_PDF),
        )

    documents = await service.list_documents_for_patient(
        organization_id=org.id, patient_id=patient.id
    )
    assert len(documents) == 1
    assert documents[0].status is DocumentStatus.FAILED
    assert documents[0].size_bytes is None
    assert documents[0].sha256 is None
    assert await local_storage.exists(documents[0].storage_key) is False


# --- upload_document: storage failure --------------------------------------


async def test_upload_document_storage_failure_marks_failed_not_available(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-storage-fail"
    )
    service = _service(db_session, _StorageThatFailsOnPut())

    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        await service.upload_document(
            organization_id=org.id,
            patient_id=patient.id,
            uploaded_by_user_id=user.id,
            document_type=DocumentType.OTHER,
            original_filename="f.pdf",
            stream=_BytesStream(_VALID_PDF),
        )

    documents = await service.list_documents_for_patient(
        organization_id=org.id, patient_id=patient.id
    )
    assert len(documents) == 1
    assert documents[0].status is DocumentStatus.FAILED
    assert documents[0].size_bytes is None
    assert documents[0].sha256 is None


# --- get_document / list_documents_for_patient -----------------------------


async def test_get_document_cross_tenant_returns_not_found(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a, user_a, patient_a = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-get-cross-a"
    )
    org_b = await make_organization("svc-doc-get-cross-b")
    service = _service(db_session, local_storage)
    document = await service.upload_document(
        organization_id=org_a.id,
        patient_id=patient_a.id,
        uploaded_by_user_id=user_a.id,
        document_type=DocumentType.OTHER,
        original_filename="f.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    with pytest.raises(DocumentNotFoundError):
        await service.get_document(organization_id=org_b.id, document_id=document.id)


async def test_get_document_scoped_to_wrong_patient_returns_not_found(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient_a = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-get-wrong-patient"
    )
    patient_b = await make_patient(org, "PN-svc-doc-get-wrong-patient-b")
    service = _service(db_session, local_storage)
    document = await service.upload_document(
        organization_id=org.id,
        patient_id=patient_a.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="f.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    with pytest.raises(DocumentNotFoundError):
        await service.get_document(
            organization_id=org.id, document_id=document.id, patient_id=patient_b.id
        )

    found = await service.get_document(
        organization_id=org.id, document_id=document.id, patient_id=patient_a.id
    )
    assert found.id == document.id


# --- download_document -------------------------------------------------------


async def test_download_document_returns_matching_bytes(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-download"
    )
    service = _service(db_session, local_storage)
    document = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="f.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    found, stream = await service.download_document(
        organization_id=org.id, document_id=document.id
    )
    downloaded = b""
    async for chunk in stream:
        downloaded += chunk

    assert found.id == document.id
    assert downloaded == _VALID_PDF
    assert hashlib.sha256(downloaded).hexdigest() == document.sha256


async def test_download_document_rejects_non_available_status(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_patient_document: Callable[..., Awaitable[object]],
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-download-pending"
    )
    document = await make_patient_document(
        org, patient, user.id, status=DocumentStatus.PENDING
    )
    service = _service(db_session, local_storage)

    with pytest.raises(DocumentNotAvailableError):
        await service.download_document(organization_id=org.id, document_id=document.id)


# --- delete_document ----------------------------------------------------------


async def test_delete_document_succeeds_and_removes_object(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-delete-ok"
    )
    service = _service(db_session, local_storage)
    document = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="f.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    deleted = await service.delete_document(organization_id=org.id, document_id=document.id)

    assert deleted.status is DocumentStatus.DELETED
    assert await local_storage.exists(document.storage_key) is False


async def test_delete_already_deleted_document_is_rejected(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-delete-twice"
    )
    service = _service(db_session, local_storage)
    document = await service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="f.pdf",
        stream=_BytesStream(_VALID_PDF),
    )
    await service.delete_document(organization_id=org.id, document_id=document.id)

    with pytest.raises(InvalidDocumentTransitionError):
        await service.delete_document(organization_id=org.id, document_id=document.id)


async def test_delete_document_storage_failure_leaves_status_unchanged(
    db_session: AsyncSession,
    local_storage: LocalDocumentStorage,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user, patient = await _scenario(
        make_organization, make_user, make_membership, make_patient, "svc-doc-delete-fail"
    )
    upload_service = _service(db_session, local_storage)
    document = await upload_service.upload_document(
        organization_id=org.id,
        patient_id=patient.id,
        uploaded_by_user_id=user.id,
        document_type=DocumentType.OTHER,
        original_filename="f.pdf",
        stream=_BytesStream(_VALID_PDF),
    )

    failing_service = _service(db_session, _StorageThatFailsOnDelete(local_storage))
    with pytest.raises(DocumentDeletionFailedError):
        await failing_service.delete_document(organization_id=org.id, document_id=document.id)

    reloaded = await upload_service.get_document(
        organization_id=org.id, document_id=document.id
    )
    assert reloaded.status is DocumentStatus.AVAILABLE
    assert await local_storage.exists(document.storage_key) is True
