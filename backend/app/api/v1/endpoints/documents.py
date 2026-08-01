"""Patient document endpoints: upload, listing, retrieval, safe download,
and deletion/retirement.

RBAC (see docs/DOCUMENTS.md "RBAC" and docs/RBAC.md): `ADMIN`/`STAFF` may
upload/list/get/download/delete any document for any patient in their
organization. `PATIENT` may upload/list/get/download ONLY their own
documents, and may NEVER delete a document (a deliberately conservative
policy — see `delete_document` below and docs/DOCUMENTS.md "Deletion
Semantics").

## Patient Identity: Never Trusted From The Client

Every route in this module takes `patient_id` as a URL PATH parameter.
`_resolve_patient_id` is the single place that turns it into a value
safe to act on: for `ADMIN`/`STAFF` it merely confirms the patient
exists in this organization (404 otherwise); for `PATIENT` it additionally
requires the path value to match the caller's OWN linked `Patient` record
(derived server-side from the authenticated `User`, via
`PatientService.get_own_patient_record`) — any other `patient_id` in the
URL 404s, identically to a truly nonexistent one. A patient can
therefore never reach, let alone act on, another patient's documents
merely by editing the URL.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.core.config import Settings, get_settings
from app.core.pagination import MAX_PAGE_SIZE
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.patient_document import DocumentType
from app.models.user import User
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.services.document import PatientDocumentService
from app.services.document_validation import sanitize_original_filename
from app.services.patient import PatientNotFoundError, PatientService
from app.storage.base import DocumentStorage
from app.storage.factory import get_document_storage

router = APIRouter(prefix="/organizations/{organization_id}/patients/{patient_id}/documents")

_require_any_access = require_roles(Role.ADMIN, Role.STAFF, Role.PATIENT)
_require_staff_access = require_roles(Role.ADMIN, Role.STAFF)


async def _resolve_patient_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    current_user: User,
    path_patient_id: uuid.UUID,
) -> uuid.UUID:
    """Validate `path_patient_id` and return the id every subsequent
    service call in this module scopes to. See the module docstring."""
    patient_service = PatientService(session)
    if membership.role is Role.PATIENT:
        own_patient = await patient_service.get_own_patient_record(
            organization_id=organization_id, user_id=current_user.id
        )
        if own_patient.id != path_patient_id:
            # Same shape as a truly nonexistent patient.
            raise PatientNotFoundError("No patient found with this id in this organization.")
        return own_patient.id

    patient = await patient_service.get_patient(
        organization_id=organization_id, patient_id=path_patient_id
    )
    return patient.id


def _document_service(
    session: AsyncSession, storage: DocumentStorage, settings: Settings
) -> PatientDocumentService:
    return PatientDocumentService(
        session, storage, max_upload_bytes=settings.document_max_upload_bytes
    )


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    document_type: Annotated[DocumentType, Form()],
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    """Upload a document for a patient. ADMIN/STAFF may upload for any
    patient in the organization; PATIENT may upload only for themselves.
    Rejects unsupported file types/oversized uploads with a 422/413 —
    see `app.services.document_validation` and
    docs/DOCUMENTS.md "Allowed File Types"/"File Size"."""
    resolved_patient_id = await _resolve_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        path_patient_id=patient_id,
    )
    service = _document_service(session, storage, settings)
    document = await service.upload_document(
        organization_id=organization_id,
        patient_id=resolved_patient_id,
        uploaded_by_user_id=current_user.id,
        document_type=document_type,
        original_filename=file.filename or "",
        stream=file,
    )
    return DocumentResponse.model_validate(document)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    """List a patient's documents. ADMIN/STAFF: any patient in the
    organization. PATIENT: only their own — this route is always
    patient-scoped by the URL itself, so there is no organization-wide
    document listing anywhere in this API."""
    resolved_patient_id = await _resolve_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        path_patient_id=patient_id,
    )
    service = _document_service(session, storage, settings)
    documents = await service.list_documents_for_patient(
        organization_id=organization_id, patient_id=resolved_patient_id, limit=limit, offset=offset
    )
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(doc) for doc in documents]
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
) -> DocumentResponse:
    """Retrieve a document's metadata by id. ADMIN/STAFF: any patient in
    the organization. PATIENT: only their own."""
    resolved_patient_id = await _resolve_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        path_patient_id=patient_id,
    )
    service = _document_service(session, storage, settings)
    document = await service.get_document(
        organization_id=organization_id, document_id=document_id, patient_id=resolved_patient_id
    )
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/download")
async def download_document(
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_any_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
) -> StreamingResponse:
    """Download a document's bytes. Only an `available` document may be
    downloaded (409 otherwise — see
    `app.services.document.DocumentNotAvailableError`). Never exposes
    `storage_key` or any filesystem/object-storage path — the response
    carries only the document's own `media_type` as `Content-Type`, a
    sanitized filename in `Content-Disposition: attachment` (never
    `inline` — untrusted content is never rendered by the browser), and
    `X-Content-Type-Options: nosniff` so a browser never second-guesses
    the declared type. See docs/DOCUMENTS.md "Download Safety"."""
    resolved_patient_id = await _resolve_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        path_patient_id=patient_id,
    )
    service = _document_service(session, storage, settings)
    document, stream = await service.download_document(
        organization_id=organization_id, document_id=document_id, patient_id=resolved_patient_id
    )
    safe_filename = sanitize_original_filename(document.original_filename).replace('"', "'")
    return StreamingResponse(
        stream,
        media_type=document.media_type.value,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{document_id}", response_model=DocumentResponse)
async def delete_document(
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    membership: Annotated[OrganizationMembership, Depends(_require_staff_access)],
    current_user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
) -> DocumentResponse:
    """Retire (soft-delete) a document. ADMIN/STAFF ONLY — `PATIENT`
    cannot reach this route at all (`require_roles` rejects it with 403
    before any document is even looked up). See docs/DOCUMENTS.md
    "Deletion Semantics" for why patient-initiated deletion is not
    offered in this story."""
    resolved_patient_id = await _resolve_patient_id(
        session,
        organization_id=organization_id,
        membership=membership,
        current_user=current_user,
        path_patient_id=patient_id,
    )
    service = _document_service(session, storage, settings)
    document = await service.delete_document(
        organization_id=organization_id, document_id=document_id, patient_id=resolved_patient_id
    )
    return DocumentResponse.model_validate(document)
