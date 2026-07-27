"""`LocalDocumentStorage`: a filesystem-backed `DocumentStorage`.

LOCAL DEVELOPMENT ONLY — see docs/DOCUMENTS.md "Local Development
Storage" and `app.core.config.Settings._forbid_local_document_storage_outside_development`,
which refuses to start the application with this backend selected
outside `development`/`test`. Local disk storage is not durable, not
shared across instances, and not an appropriate backing store for a real
deployment — a future story is expected to add an S3-compatible
`DocumentStorage` implementation for that.

Every path this module ever touches is derived from a server-generated,
opaque `storage_key` (see `app.services.document`) and defensively
re-validated against the configured storage root on every call — never
trusted merely because the caller is internal code (see `_resolve`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from app.storage.exceptions import StorageKeyInvalidError, StorageObjectNotFoundError

_READ_CHUNK_BYTES = 64 * 1024


class LocalDocumentStorage:
    """Filesystem-backed `DocumentStorage` rooted at a single configured
    directory. See the module docstring."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        """Return the absolute filesystem path for `storage_key`, raising
        `StorageKeyInvalidError` unless it resolves to a location STRICTLY
        inside the configured root.

        Handles both traversal styles (`../`) and an absolute-path
        override attempt (`pathlib`'s `/` operator discards the left
        operand entirely when the right operand is itself absolute — e.g.
        `Path("/root") / "/etc/passwd"` == `Path("/etc/passwd")` on
        POSIX, and the equivalent applies to a Windows drive-absolute
        path) — both are caught uniformly by the `is_relative_to` check
        below, which runs AFTER `.resolve()` normalizes `..` components
        and symlinks, rather than by pattern-matching the raw string.
        """
        if not storage_key:
            raise StorageKeyInvalidError("storage_key must not be empty.")
        candidate = (self._root / storage_key).resolve()
        if not candidate.is_relative_to(self._root):
            raise StorageKeyInvalidError(
                f"storage_key {storage_key!r} resolves outside the configured storage root."
            )
        return candidate

    async def put(self, storage_key: str, chunks: AsyncIterator[bytes]) -> int:
        """See `app.storage.base.DocumentStorage.put`.

        Writes to a temporary file in the SAME directory as the final
        target, then `os.replace()`s it into place — atomic on every
        platform this project supports (same-filesystem rename), so a
        reader can never observe a partially-written object under
        `storage_key`. If `chunks` (the caller's own iterator) raises —
        e.g. `DocumentTooLargeError` detected mid-stream by
        `app.services.document` — the temp file is removed and the
        original exception propagates unchanged.
        """
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"

        written = 0
        try:
            with tmp_path.open("wb") as handle:
                async for chunk in chunks:
                    handle.write(chunk)
                    written += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return written

    async def open_read_stream(self, storage_key: str) -> AsyncIterator[bytes]:
        """See `app.storage.base.DocumentStorage.open_read_stream`."""
        target = self._resolve(storage_key)
        if not target.is_file():
            raise StorageObjectNotFoundError(storage_key)
        return self._read_chunks(target)

    @staticmethod
    async def _read_chunks(target: Path) -> AsyncIterator[bytes]:
        with target.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                yield chunk

    async def delete(self, storage_key: str) -> None:
        """See `app.storage.base.DocumentStorage.delete`."""
        self._resolve(storage_key).unlink(missing_ok=True)

    async def exists(self, storage_key: str) -> bool:
        """See `app.storage.base.DocumentStorage.exists`."""
        return self._resolve(storage_key).is_file()
