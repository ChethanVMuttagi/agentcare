"""`DocumentStorage`: the storage interface `PatientDocumentService` depends on.

Deliberately a `typing.Protocol`, not tied to any particular SDK or
credential model — `app.services.document.PatientDocumentService` is
written against this interface only, never against
`LocalDocumentStorage` (`app/storage/local.py`) or any future
S3-compatible implementation directly. This is what lets a production
object-storage backend be added later without touching business logic —
see docs/DOCUMENTS.md "Storage Abstraction" and
docs/adr/ADR-0008-document-storage-and-security.md.

Every method operates on a `storage_key` — an OPAQUE, server-generated
identifier (see `app.services.document._generate_storage_key`). No method
here ever accepts a patient name, filename, or any other caller-supplied
value as part of a storage location.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class DocumentStorage(Protocol):
    """Storage backend for document object bytes. See the module docstring."""

    async def put(self, storage_key: str, chunks: AsyncIterator[bytes]) -> int:
        """Persist `chunks` under `storage_key`, returning the total number
        of bytes written.

        Implementations MUST NOT leave a partial or corrupt object visible
        under `storage_key` if this raises — either the full object is
        written, or nothing is (see `LocalDocumentStorage.put`'s
        write-to-temp-then-atomic-rename strategy). If the caller's own
        `chunks` iterator raises (e.g. a size-limit violation detected
        mid-stream), that exception propagates through `put()` unchanged;
        it is not this method's job to interpret it.
        """
        ...

    async def open_read_stream(self, storage_key: str) -> AsyncIterator[bytes]:
        """Return an async iterator yielding `storage_key`'s bytes in
        chunks.

        Raises `app.storage.exceptions.StorageObjectNotFoundError`
        immediately (before returning the iterator) if no object exists
        under `storage_key` — a caller can rely on the exception
        surfacing before it starts consuming the stream, not partway
        through.
        """
        ...

    async def delete(self, storage_key: str) -> None:
        """Remove the object under `storage_key`. A no-op, not an error,
        if no such object exists — deletion is idempotent."""
        ...

    async def exists(self, storage_key: str) -> bool:
        """Whether an object currently exists under `storage_key`."""
        ...
