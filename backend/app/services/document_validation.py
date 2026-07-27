"""Untrusted-upload validation: file-signature detection and filename
sanitization.

Pure functions only — no I/O, no database access, no exceptions specific
to any particular caller's error taxonomy (see
`app.services.document.PatientDocumentService` for how these are turned
into domain errors and combined with streaming size enforcement).

Uploaded files are UNTRUSTED INPUT. Nothing here trusts a file extension
or a client-declared `Content-Type` for anything security-relevant — see
docs/DOCUMENTS.md "Allowed File Types" and "Malware-Scanning Boundary".
"""

from __future__ import annotations

import re

from app.models.patient_document import DocumentMediaType

# How many leading bytes must be read before a signature can be
# determined — the longest signature below (PNG) is 8 bytes; a little
# headroom is kept for future signatures. Read once, up front, before any
# byte is written to storage or any database row is created — see
# `app.services.document.PatientDocumentService.upload_document`.
SIGNATURE_HEADER_BYTES = 16

# Deliberately hand-rolled rather than a third-party signature-detection
# library (e.g. `python-magic`, which wraps the OS-level `libmagic` and is
# fragile/unreliable to install on Windows and in CI — exactly what
# STORY-008 was told to avoid): the allowlist is exactly three, very
# well-known, trivially-distinguished formats. A few `bytes.startswith()`
# checks are simpler, add zero third-party dependencies, and are just as
# reliable as a general-purpose library for a set this narrow — see
# docs/adr/ADR-0008-document-storage-and-security.md.
_SIGNATURES: tuple[tuple[bytes, DocumentMediaType], ...] = (
    (b"%PDF-", DocumentMediaType.PDF),
    (b"\xff\xd8\xff", DocumentMediaType.JPEG),
    (b"\x89PNG\r\n\x1a\n", DocumentMediaType.PNG),
)


def sniff_media_type(header: bytes) -> DocumentMediaType | None:
    """Return the `DocumentMediaType` `header`'s magic bytes identify, or
    `None` if it matches none of the small allowlist this project
    supports.

    This is SIGNATURE validation, not malware scanning — a well-formed
    PDF/JPEG/PNG that passes this check can still carry a malicious
    payload (e.g. an embedded exploit in a PDF). See
    docs/DOCUMENTS.md "Malware-Scanning Boundary": signature validation
    != malware scanning, and this story does not claim otherwise.
    """
    for signature, media_type in _SIGNATURES:
        if header.startswith(signature):
            return media_type
    return None


_MAX_FILENAME_LENGTH = 255
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_FALLBACK_FILENAME = "unnamed-file"


def sanitize_original_filename(raw_filename: str) -> str:
    """Turn a client-supplied filename into safe DISPLAY metadata only.

    Never used to build a filesystem path — storage locations are always
    server-generated, opaque keys (see
    `app.services.document._generate_storage_key`) — and never trusted as
    an authoritative media type (see `sniff_media_type`). This function
    exists solely so `PatientDocument.original_filename` is safe to
    render back to a client.

    - Strips directory components from EITHER path style (`/` and `\\` —
      a Windows-style path can arrive from a client regardless of the
      server's own OS, and vice versa), keeping only the final path
      segment.
    - Strips ASCII control characters (0x00-0x1F, 0x7F).
    - Collapses surrounding whitespace.
    - Truncates to `_MAX_FILENAME_LENGTH` characters.
    - Falls back to a fixed placeholder if nothing safe remains (e.g. the
      input was empty, all control characters, a bare path separator, or
      resolves to `.`/`..`).
    """
    basename = raw_filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _CONTROL_CHARS.sub("", basename).strip()
    cleaned = cleaned[:_MAX_FILENAME_LENGTH]
    if cleaned in ("", ".", ".."):
        return _FALLBACK_FILENAME
    return cleaned
