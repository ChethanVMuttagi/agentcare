"""PatientDocumentService: upload orchestration, lifecycle, and safe retrieval.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005/006/007. Uploaded files are UNTRUSTED INPUT —
see `app.services.document_validation` for signature-based type
validation. This module owns the upload STATE MACHINE (see
`upload_document`'s docstring) and every subsequent lifecycle
transition; `app.repositories.patient_document` never decides whether a
transition is allowed, and `app.storage` (see `app/storage/base.py`)
never makes a business decision about what's a valid document — it only
stores/retrieves/deletes bytes under an opaque key this service
generates.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.membership import OrganizationMembership
from app.models.patient_document import DocumentStatus, DocumentType, PatientDocument
from app.repositories import patient as patient_repository
from app.repositories import patient_document as patient_document_repository
from app.services.document_validation import (
    SIGNATURE_HEADER_BYTES,
    sanitize_original_filename,
    sniff_media_type,
)
from app.storage.base import DocumentStorage

_CHUNK_BYTES = 64 * 1024


class AsyncReadable(Protocol):
    """The minimal shape this service needs from an incoming upload —
    matches `fastapi.UploadFile.read` exactly, without coupling this
    module to FastAPI/Starlette types directly."""

    async def read(self, size: int = -1) -> bytes: ...


class DocumentPatientNotFoundError(AppException):
    """404: no patient matches, within the caller's own organization."""

    status_code = 404
    error_code = "document_patient_not_found"


class DocumentPatientInactiveError(AppException):
    """422: the patient exists but is not active."""

    status_code = 422
    error_code = "document_patient_inactive"


class UploaderNotActiveMemberError(AppException):
    """422: the uploading user has no ACTIVE membership in this organization.

    Defense-in-depth, not the primary enforcement point: `get_current_membership`
    (`app.auth.dependencies`) already requires an active membership before
    any route handler runs. This re-check exists so
    `PatientDocumentService` remains correct even if called from a future
    code path that does not go through that HTTP dependency (e.g. a
    future agent tool) — the same reasoning
    `app.services.appointment.AppointmentService` applies to
    practitioner/department activity checks.
    """

    status_code = 422
    error_code = "uploader_not_active_member"


class UnsupportedFileTypeError(AppException):
    """422: the uploaded content's signature does not match the small,
    allowed set of media types (see
    `app.services.document_validation.sniff_media_type`). Covers an
    empty upload too — an empty header matches no signature."""

    status_code = 422
    error_code = "unsupported_file_type"


class DocumentTooLargeError(AppException):
    """413: the upload exceeds the configured maximum size, detected
    DURING streaming — not after fully buffering it into memory."""

    status_code = 413
    error_code = "document_too_large"


class DocumentNotFoundError(AppException):
    """404: no document matches, within the caller's own organization
    (and, if scoped to a patient, that patient's own documents).

    Deliberately raised identically whether the document truly doesn't
    exist, belongs to a different organization, or belongs to a
    different patient within the SAME organization — see
    docs/DOCUMENTS.md "Cross-Tenant & Patient Isolation".
    """

    status_code = 404
    error_code = "document_not_found"


class DocumentNotAvailableError(AppException):
    """409: the document exists but is not currently `available`
    (`pending`, `failed`, or `deleted`) — cannot be downloaded."""

    status_code = 409
    error_code = "document_not_available"


class InvalidDocumentTransitionError(AppException):
    """422: this status transition is not allowed (e.g. deleting an
    already-deleted document)."""

    status_code = 422
    error_code = "invalid_document_transition"


class DocumentDeletionFailedError(AppException):
    """500: the document's stored object could not be removed from
    storage. The document's status is left UNCHANGED — a caller must
    never be told deletion succeeded when the underlying object might
    still exist. See docs/DOCUMENTS.md "Deletion Semantics"."""

    status_code = 500
    error_code = "document_deletion_failed"


@dataclass
class _StreamStats:
    """Mutable accumulator threaded through `_validated_chunks` — by the
    time `DocumentStorage.put()` returns (having fully consumed the
    generator), this holds the complete, authoritative size/hash of what
    was actually written."""

    sha256: hashlib._Hash = field(default_factory=hashlib.sha256)
    total_bytes: int = 0


async def _validated_chunks(
    stream: AsyncReadable, first_chunk: bytes, max_bytes: int, stats: _StreamStats
) -> AsyncIterator[bytes]:
    """Read `stream` in bounded chunks, updating `stats` (running SHA-256
    and total byte count) as each chunk passes through, and yielding it
    onward to `DocumentStorage.put()`.

    Raises `DocumentTooLargeError` the MOMENT the running total exceeds
    `max_bytes` — never reads an unbounded amount into memory first. The
    exception propagates out through `put()`'s own `async for` loop (see
    `app.storage.base.DocumentStorage.put`'s contract).
    """
    stats.total_bytes = len(first_chunk)
    if stats.total_bytes > max_bytes:
        raise DocumentTooLargeError(
            f"Upload exceeds the maximum allowed size of {max_bytes} bytes."
        )
    stats.sha256.update(first_chunk)
    yield first_chunk

    while True:
        chunk = await stream.read(_CHUNK_BYTES)
        if not chunk:
            return
        stats.total_bytes += len(chunk)
        if stats.total_bytes > max_bytes:
            raise DocumentTooLargeError(
                f"Upload exceeds the maximum allowed size of {max_bytes} bytes."
            )
        stats.sha256.update(chunk)
        yield chunk


def _generate_storage_key(organization_id: uuid.UUID) -> str:
    """A server-generated, OPAQUE storage key — never derived from patient
    name, email, date of birth, patient number, or the original
    filename. `organization_id` is included purely for operational
    sharding/inspection convenience; it is never exposed to any API
    client (see docs/DOCUMENTS.md "Storage Key"), so its presence here
    does not leak tenant information through any public surface.
    """
    return f"{organization_id.hex}/{uuid.uuid4().hex}"


class PatientDocumentService:
    """Document upload/lifecycle/retrieval, scoped to one `AsyncSession`
    and one `DocumentStorage` backend."""

    def __init__(
        self, session: AsyncSession, storage: DocumentStorage, *, max_upload_bytes: int
    ) -> None:
        self._session = session
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes

    async def _ensure_uploader_is_active_member(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        result = await self._session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None or not membership.is_active:
            raise UploaderNotActiveMemberError(
                "The uploading user does not have an active membership in this organization."
            )

    async def _mark_failed(self, document: PatientDocument) -> None:
        await self._storage.delete(document.storage_key)
        document.status = DocumentStatus.FAILED
        await self._session.flush()
        await self._session.commit()

    async def upload_document(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        uploaded_by_user_id: uuid.UUID,
        document_type: DocumentType,
        original_filename: str,
        stream: AsyncReadable,
    ) -> PatientDocument:
        """Validate and persist an uploaded document.

        Upload state machine (see docs/DOCUMENTS.md "Upload State
        Machine" and docs/adr/ADR-0008-document-storage-and-security.md):

        1. Tenant/patient/uploader validation (no row created, nothing
           read from `stream` yet, if any of this fails).
        2. Peek the first `SIGNATURE_HEADER_BYTES` and sniff the media
           type. An unrecognized signature is a plain input-validation
           rejection (`UnsupportedFileTypeError`, 422) — NOTHING is
           persisted anywhere (no database row, no storage object). This
           is deliberately NOT treated as a "failed" lifecycle state:
           that state is reserved for an accepted upload whose STORAGE
           WRITE itself failed (step 4), an infrastructure concern, not
           routine client input rejection.
        3. Create the `PatientDocument` row as `PENDING` and COMMIT it —
           durably recording "an upload was accepted and object
           persistence is in progress" before the risky I/O begins, so a
           crash mid-upload leaves an auditable trace rather than
           silently nothing (never "do not silently leave uploaded files
           without corresponding metadata").
        4. Stream the (header + remaining) bytes to storage, enforcing
           the size limit and computing the SHA-256 digest as they pass
           through. On success, update the row to `AVAILABLE` with the
           now-known `size_bytes`/`sha256` and commit. On ANY failure
           (oversized upload, storage I/O error), best-effort delete
           whatever storage wrote so far, mark the row `FAILED`, commit,
           and re-raise — an `AVAILABLE` row pointing at a missing/
           incomplete object is never possible (also enforced by the
           `available_has_size_and_hash` database `CHECK` constraint —
           see `app/models/patient_document.py`).
        """
        patient = await patient_repository.get_by_id(
            self._session, organization_id=organization_id, patient_id=patient_id
        )
        if patient is None:
            raise DocumentPatientNotFoundError(
                "No patient found with this id in this organization."
            )
        if not patient.is_active:
            raise DocumentPatientInactiveError("This patient is not active.")

        await self._ensure_uploader_is_active_member(
            organization_id=organization_id, user_id=uploaded_by_user_id
        )

        header = await stream.read(SIGNATURE_HEADER_BYTES)
        media_type = sniff_media_type(header)
        if media_type is None:
            raise UnsupportedFileTypeError(
                "This file type is not supported. Allowed types: PDF, JPEG, PNG."
            )

        document = PatientDocument(
            organization_id=organization_id,
            patient_id=patient_id,
            uploaded_by_user_id=uploaded_by_user_id,
            document_type=document_type,
            status=DocumentStatus.PENDING,
            original_filename=sanitize_original_filename(original_filename),
            storage_key=_generate_storage_key(organization_id),
            media_type=media_type,
        )
        await patient_document_repository.create(self._session, document)
        await self._session.commit()

        stats = _StreamStats()
        try:
            await self._storage.put(
                document.storage_key,
                _validated_chunks(stream, header, self._max_upload_bytes, stats),
            )
        except Exception:
            await self._mark_failed(document)
            raise

        document.status = DocumentStatus.AVAILABLE
        document.size_bytes = stats.total_bytes
        document.sha256 = stats.sha256.hexdigest()
        await self._session.flush()
        await self._session.commit()
        return document

    async def get_document(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
    ) -> PatientDocument:
        """Tenant-scoped retrieval by id.

        `patient_id`, when given, additionally restricts the result to a
        document belonging to THAT patient — used for `PATIENT`-role
        self-service callers, whose `patient_id` is always derived
        server-side from their own linked `Patient` record (see
        `app.api.v1.endpoints.documents`), never trusted from a request.
        Raises the same `DocumentNotFoundError` whether the document
        doesn't exist, belongs to another organization, or belongs to
        another patient — no case is distinguishable from the response.
        Returns documents of ANY status (including `deleted`) — auditable
        visibility for whoever is already authorized to see this
        document's metadata; only `download_document` gates on status.
        """
        document = await patient_document_repository.get_by_id(
            self._session, organization_id=organization_id, document_id=document_id
        )
        if document is None:
            raise DocumentNotFoundError("No document found with this id in this organization.")
        if patient_id is not None and document.patient_id != patient_id:
            raise DocumentNotFoundError("No document found with this id in this organization.")
        return document

    async def list_documents_for_patient(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PatientDocument]:
        """Tenant- and patient-scoped listing."""
        return list(
            await patient_document_repository.list_by_patient(
                self._session,
                organization_id=organization_id,
                patient_id=patient_id,
                limit=limit,
                offset=offset,
            )
        )

    async def download_document(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
    ) -> tuple[PatientDocument, AsyncIterator[bytes]]:
        """Return `(document, byte_stream)` for a currently `available`
        document. Raises `DocumentNotAvailableError` (409) for any other
        status — a `pending`/`failed`/`deleted` document is never
        streamed. `storage_key` never leaves this method — the caller
        (see `app.api.v1.endpoints.documents`) only ever sees the
        returned `document` (for `Content-Type`/filename) and the byte
        stream itself.
        """
        document = await self.get_document(
            organization_id=organization_id, document_id=document_id, patient_id=patient_id
        )
        if document.status is not DocumentStatus.AVAILABLE:
            raise DocumentNotAvailableError("This document is not currently available.")
        stream = await self._storage.open_read_stream(document.storage_key)
        return document, stream

    async def delete_document(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
    ) -> PatientDocument:
        """`pending`/`available`/`failed` -> `deleted`. ADMIN/STAFF only
        — enforced at the route/RBAC layer (see
        `app.api.v1.endpoints.documents` and docs/DOCUMENTS.md "RBAC":
        patients may not delete their own documents in this story, a
        deliberately conservative policy). `patient_id` is an optional
        additional scope check, the same shape as `get_document`'s.

        Rejects (rather than silently no-ops) an already-`deleted`
        document with `InvalidDocumentTransitionError`, the same uniform
        rule `app.services.appointment.AppointmentService` uses for
        invalid transitions.

        Storage deletion is attempted BEFORE the status is changed. If it
        raises, the document's status is left completely UNCHANGED and
        `DocumentDeletionFailedError` propagates — this method never
        reports success while the underlying object might still exist
        (see docs/DOCUMENTS.md "Deletion Semantics"). `DocumentStorage.delete`
        is idempotent (a no-op if nothing exists under the key), so this
        is safe to call regardless of whether the object was ever
        actually written (e.g. a `failed` document whose storage write
        never completed).
        """
        document = await self.get_document(
            organization_id=organization_id, document_id=document_id, patient_id=patient_id
        )
        if document.status is DocumentStatus.DELETED:
            raise InvalidDocumentTransitionError("This document has already been deleted.")

        try:
            await self._storage.delete(document.storage_key)
        except Exception as exc:
            raise DocumentDeletionFailedError(
                "The document's stored object could not be removed; the document was not deleted."
            ) from exc

        document.status = DocumentStatus.DELETED
        await self._session.flush()
        await self._session.commit()
        return document
