"""The Document specialist's one tool: `list_patient_documents`.

Calls the REAL existing service layer
(`app.services.document.PatientDocumentService.list_documents_for_patient`)
— never a fake/hardcoded success path. Read-only: no upload, download,
or delete capability exists here, and the agent endpoint this story
serves does not accept document bytes at all.

Deliberately returns a NARROW field set — id, document type, status,
original filename, and upload timestamp only. Never `storage_key`, file
bytes, `sha256`, `size_bytes`, or `uploaded_by_user_id`: this tool exists
so an agent can answer "what documents are on file and what's their
status," never to expose where a document lives in storage or its raw
content. See docs/TOOLS.md "Document Status Tool" and
docs/DOCUMENTS.md "Download Safety" for the same storage-key
non-disclosure rule this tool also upholds.
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.base import (
    ToolCategory,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
    ToolResultStatus,
)
from app.ai.tools.registry import ToolRegistry
from app.core.config import get_settings
from app.models.membership import Role
from app.services.document import PatientDocumentService
from app.storage.factory import get_document_storage

_MAX_RETURNED_DOCUMENTS = 20


class ListPatientDocumentsArguments(BaseModel):
    """Untrusted, model-supplied arguments for `list_patient_documents`.

    `patient_id` is used ONLY when the caller is `ADMIN`/`STAFF` — mirrors
    `app.ai.tools.appointment_tools.BookAppointmentArguments.patient_id`.
    For a `PATIENT`-role caller this field is READ BUT NEVER TRUSTED —
    the handler always substitutes `context.patient_id` instead.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID | None = None


async def _list_patient_documents(
    arguments: BaseModel, context: ToolExecutionContext, session: AsyncSession
) -> ToolResult:
    assert isinstance(arguments, ListPatientDocumentsArguments)  # narrows type for mypy

    if context.role is Role.PATIENT:
        # Server-derived identity ALWAYS wins — see
        # `app.ai.tools.appointment_tools._book_appointment` for the same
        # pattern and docs/AI_SAFETY.md "Authorization Boundary".
        if context.patient_id is None:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="patient_not_linked",
                safe_message="No patient record is linked to your account.",
            )
        patient_id = context.patient_id
    else:
        if arguments.patient_id is None:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="patient_id_required",
                safe_message="A patient must be specified to check document status.",
            )
        patient_id = arguments.patient_id

    settings = get_settings()
    service = PatientDocumentService(
        session, get_document_storage(), max_upload_bytes=settings.document_max_upload_bytes
    )
    documents = await service.list_documents_for_patient(
        organization_id=context.organization_id, patient_id=patient_id
    )

    return ToolResult(
        status=ToolResultStatus.SUCCESS,
        code="documents_listed",
        safe_message=f"Found {len(documents)} document(s) on file.",
        data={
            "documents": [
                {
                    "id": str(document.id),
                    "document_type": document.document_type.value,
                    "status": document.status.value,
                    "original_filename": document.original_filename,
                    "created_at": document.created_at.isoformat(),
                }
                for document in documents[:_MAX_RETURNED_DOCUMENTS]
            ]
        },
    )


LIST_PATIENT_DOCUMENTS_TOOL = ToolDefinition(
    name="list_patient_documents",
    description=(
        "List the administrative documents on file for a patient — id, document "
        "type, status, original filename, and upload date only. Never returns "
        "file contents or storage location."
    ),
    category=ToolCategory.DOCUMENT_STATUS,
    input_schema=ListPatientDocumentsArguments,
    handler=_list_patient_documents,
)


def build_document_tool_registry() -> ToolRegistry:
    """A registry containing only the Document specialist's tool —
    mirrors `app.ai.tools.appointment_tools.build_default_registry`'s
    shape, used by this module's own focused tests. The registry the
    running application actually uses combines every tool — see
    `app.ai.tools.registry_builder.build_full_tool_registry`."""
    registry = ToolRegistry()
    registry.register(LIST_PATIENT_DOCUMENTS_TOOL)
    return registry


@lru_cache
def get_document_tool_registry() -> ToolRegistry:
    return build_document_tool_registry()
