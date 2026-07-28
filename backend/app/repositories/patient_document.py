"""Tenant-scoped persistence/query operations for `PatientDocument`.

Every read here REQUIRES an `organization_id` — same discipline as every
other repository in this codebase (see `app.repositories.appointment`).
This module only adds, flushes, and queries. It never commits, and it
performs no RBAC/authorization decisions and no business validation
(upload validation, storage orchestration) — see
`app.services.document.PatientDocumentService`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_document import DocumentStatus, PatientDocument


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, document_id: uuid.UUID
) -> PatientDocument | None:
    """Return the document with `document_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(PatientDocument).where(
            PatientDocument.organization_id == organization_id,
            PatientDocument.id == document_id,
        )
    )
    return result.scalar_one_or_none()


async def list_by_patient(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    patient_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[PatientDocument]:
    """Return documents for one patient within `organization_id`, newest first."""
    result = await session.execute(
        select(PatientDocument)
        .where(
            PatientDocument.organization_id == organization_id,
            PatientDocument.patient_id == patient_id,
        )
        .order_by(PatientDocument.created_at.desc(), PatientDocument.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def count_by_status(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[DocumentStatus, int]:
    """Organization-wide document counts grouped by `status` — the
    documents breakdown for the Milestone B analytics summary
    (`app.api.v1.endpoints.analytics`)."""
    result = await session.execute(
        select(PatientDocument.status, func.count())
        .where(PatientDocument.organization_id == organization_id)
        .group_by(PatientDocument.status)
    )
    return {status: count for status, count in result.all()}


async def create(session: AsyncSession, document: PatientDocument) -> PatientDocument:
    """Add and flush a new `PatientDocument`. Does NOT commit — see
    `app.services.document.PatientDocumentService.upload_document`."""
    session.add(document)
    await session.flush()
    return document
