"""app.storage.local.LocalDocumentStorage tests.

Uses only pytest-managed temporary directories (`tmp_path`) — never the
real configured `DOCUMENT_STORAGE_PATH`, and never a tracked source
directory. No database required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.storage.exceptions import StorageKeyInvalidError, StorageObjectNotFoundError
from app.storage.local import LocalDocumentStorage


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _failing_chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part
    raise RuntimeError("synthetic mid-stream failure")


async def _collect(stream: AsyncIterator[bytes]) -> bytes:
    buffer = b""
    async for chunk in stream:
        buffer += chunk
    return buffer


@pytest.fixture()
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "documents")


async def test_put_then_open_read_stream_round_trips_bytes(
    storage: LocalDocumentStorage,
) -> None:
    key = "org-a/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    written = await storage.put(key, _chunks(b"hello ", b"world"))

    assert written == len(b"hello world")
    stream = await storage.open_read_stream(key)
    assert await _collect(stream) == b"hello world"


async def test_exists_true_after_put_false_before(storage: LocalDocumentStorage) -> None:
    key = "org-a/exists-key"
    assert await storage.exists(key) is False

    await storage.put(key, _chunks(b"data"))

    assert await storage.exists(key) is True


async def test_delete_removes_object(storage: LocalDocumentStorage) -> None:
    key = "org-a/delete-key"
    await storage.put(key, _chunks(b"data"))
    assert await storage.exists(key) is True

    await storage.delete(key)

    assert await storage.exists(key) is False


async def test_delete_is_idempotent_for_missing_object(storage: LocalDocumentStorage) -> None:
    # Must NOT raise — deletion is a no-op, not an error, for a key that
    # was never written (or already deleted).
    await storage.delete("org-a/never-existed")


async def test_open_read_stream_raises_for_missing_object(
    storage: LocalDocumentStorage,
) -> None:
    with pytest.raises(StorageObjectNotFoundError):
        await storage.open_read_stream("org-a/does-not-exist")


async def test_storage_key_is_opaque_and_supports_nested_prefix(
    storage: LocalDocumentStorage,
) -> None:
    # Mirrors the real key shape app.services.document generates:
    # f"{organization_id.hex}/{uuid4().hex}" — a two-level nested path,
    # containing nothing derived from patient/file identity.
    key = "0123456789abcdef0123456789abcdef/fedcba9876543210fedcba9876543210"

    await storage.put(key, _chunks(b"opaque-key-content"))

    stream = await storage.open_read_stream(key)
    assert await _collect(stream) == b"opaque-key-content"


@pytest.mark.parametrize(
    "malicious_key",
    [
        "../outside.txt",
        "../../etc/passwd",
        "a/../../outside.txt",
        "/etc/passwd",
        "..\\outside.txt",
        "..\\..\\windows\\system32\\config",
        "C:\\Windows\\System32\\config",
        "",
    ],
)
async def test_traversal_and_invalid_keys_are_rejected(
    storage: LocalDocumentStorage, malicious_key: str
) -> None:
    with pytest.raises(StorageKeyInvalidError):
        await storage.put(malicious_key, _chunks(b"data"))


async def test_put_confines_writes_inside_configured_root(
    storage: LocalDocumentStorage, tmp_path: Path
) -> None:
    key = "org-a/confinement-key"

    await storage.put(key, _chunks(b"data"))

    written_path = tmp_path / "documents" / "org-a" / "confinement-key"
    assert written_path.is_file()
    assert written_path.read_bytes() == b"data"
    # Nothing was written directly under tmp_path (outside the storage root).
    assert not (tmp_path / "confinement-key").exists()


async def test_put_leaves_no_object_or_temp_file_on_mid_stream_failure(
    storage: LocalDocumentStorage, tmp_path: Path
) -> None:
    key = "org-a/failure-key"

    with pytest.raises(RuntimeError, match="synthetic mid-stream failure"):
        await storage.put(key, _failing_chunks(b"partial-data"))

    assert await storage.exists(key) is False
    # No leftover `.tmp-*` file anywhere under the storage root.
    root = tmp_path / "documents"
    leftover_temp_files = list(root.rglob(".*tmp-*"))
    assert leftover_temp_files == []


async def test_put_overwrites_are_atomic_readers_never_see_partial_content(
    storage: LocalDocumentStorage,
) -> None:
    key = "org-a/atomic-key"
    await storage.put(key, _chunks(b"original-content"))

    await storage.put(key, _chunks(b"replacement-content"))

    stream = await storage.open_read_stream(key)
    assert await _collect(stream) == b"replacement-content"


async def test_read_stream_yields_multiple_chunks_for_large_object(
    storage: LocalDocumentStorage,
) -> None:
    # Larger than LocalDocumentStorage's internal read chunk size (64 KiB)
    # so open_read_stream is proven to actually loop, not just read once.
    payload = b"x" * (64 * 1024 + 100)
    key = "org-a/large-key"
    await storage.put(key, _chunks(payload))

    stream = await storage.open_read_stream(key)
    chunks = [chunk async for chunk in stream]

    assert len(chunks) >= 2
    assert b"".join(chunks) == payload
