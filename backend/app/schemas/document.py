"""PatientDocument API schemas.

Never returns SQLAlchemy models directly. `DocumentResponse` exposes only
administrative metadata — `storage_key` (or any filesystem/object-storage
path) is NEVER included; see docs/DOCUMENTS.md "Download Safety".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.patient_document import DocumentMediaType, DocumentStatus, DocumentType


class DocumentResponse(BaseModel):
    """A document's metadata, as returned by the API. Administrative
    fields only — no `storage_key`, no filesystem/object-storage path."""

    id: uuid.UUID
    organization_id: uuid.UUID
    patient_id: uuid.UUID
    uploaded_by_user_id: uuid.UUID
    document_type: DocumentType
    status: DocumentStatus
    original_filename: str
    media_type: DocumentMediaType
    size_bytes: int | None
    sha256: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    """`GET .../patients/{patient_id}/documents` response envelope."""

    documents: list[DocumentResponse]
