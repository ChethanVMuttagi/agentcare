"""Builds the configured `DocumentStorage` backend from `Settings`.

The one place that knows `document_storage_backend` maps to a concrete
implementation — `app.services.document.PatientDocumentService` never
constructs a storage backend itself, only receives one (see
`app.api.v1.endpoints.documents`).
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.storage.base import DocumentStorage
from app.storage.local import LocalDocumentStorage


def build_document_storage(settings: Settings) -> DocumentStorage:
    """Construct the `DocumentStorage` backend `settings` selects.

    Only `"local"` is implemented in this story
    (`Settings._forbid_local_document_storage_outside_development`
    already refuses to start the application with `"local"` outside
    `development`/`test`). Any other value raises `NotImplementedError`
    rather than silently falling back to something unintended — a future
    story adding a real object-storage backend registers it here.
    """
    if settings.document_storage_backend == "local":
        return LocalDocumentStorage(settings.document_storage_path)
    raise NotImplementedError(
        f"No DocumentStorage implementation exists for "
        f"DOCUMENT_STORAGE_BACKEND={settings.document_storage_backend!r}."
    )


@lru_cache
def get_document_storage() -> DocumentStorage:
    """FastAPI-dependency-friendly, cached accessor — mirrors
    `app.db.session.get_engine`'s lazy-construct-once-per-process pattern."""
    return build_document_storage(get_settings())
