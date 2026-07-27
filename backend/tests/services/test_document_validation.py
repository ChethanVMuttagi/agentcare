"""app.services.document_validation tests: signature detection and
filename sanitization. No I/O, no database required."""

from __future__ import annotations

import pytest

from app.models.patient_document import DocumentMediaType
from app.services.document_validation import (
    SIGNATURE_HEADER_BYTES,
    sanitize_original_filename,
    sniff_media_type,
)

# Minimal, synthetic byte sequences carrying real magic-byte signatures —
# not full, structurally valid files (not needed to exercise signature
# detection), never any real/uploaded document content.
_VALID_PDF_HEADER = b"%PDF-1.4\n%synthetic-test-content\n"
_VALID_JPEG_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


# --- sniff_media_type -----------------------------------------------------


def test_sniff_valid_pdf_signature() -> None:
    assert sniff_media_type(_VALID_PDF_HEADER) is DocumentMediaType.PDF


def test_sniff_valid_jpeg_signature() -> None:
    assert sniff_media_type(_VALID_JPEG_HEADER) is DocumentMediaType.JPEG


def test_sniff_valid_png_signature() -> None:
    assert sniff_media_type(_VALID_PNG_HEADER) is DocumentMediaType.PNG


def test_sniff_rejects_empty_content() -> None:
    assert sniff_media_type(b"") is None


def test_sniff_rejects_plain_text_pretending_to_be_a_pdf() -> None:
    # A "fake PDF" containing arbitrary text — no real PDF signature.
    assert sniff_media_type(b"this is just plain text, not a real pdf") is None


def test_sniff_rejects_windows_executable_renamed_to_pdf() -> None:
    # A Windows PE executable's real header ("MZ..."), regardless of
    # whatever filename/extension a client claims for it.
    pe_header = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    assert sniff_media_type(pe_header) is None


def test_sniff_rejects_svg() -> None:
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert sniff_media_type(svg) is None


def test_sniff_rejects_html() -> None:
    html = b"<!DOCTYPE html><html><body>synthetic</body></html>"
    assert sniff_media_type(html) is None


def test_sniff_ignores_extension_mime_mismatch_uses_actual_bytes() -> None:
    # Content is genuinely a JPEG, regardless of what a client's filename
    # or declared Content-Type might claim (e.g. "photo.png").
    assert sniff_media_type(_VALID_JPEG_HEADER) is DocumentMediaType.JPEG
    assert sniff_media_type(_VALID_JPEG_HEADER) is not DocumentMediaType.PNG


def test_signature_header_bytes_is_enough_to_detect_every_signature() -> None:
    for header in (_VALID_PDF_HEADER, _VALID_JPEG_HEADER, _VALID_PNG_HEADER):
        truncated = header[:SIGNATURE_HEADER_BYTES]
        assert sniff_media_type(truncated) is not None


# --- sanitize_original_filename -------------------------------------------


def test_sanitize_keeps_a_normal_filename_unchanged() -> None:
    assert sanitize_original_filename("insurance-card.pdf") == "insurance-card.pdf"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("../../../secret.pdf", "secret.pdf"),
        ("a/b/c/report.pdf", "report.pdf"),
    ],
)
def test_sanitize_strips_unix_style_directory_components(raw: str, expected: str) -> None:
    assert sanitize_original_filename(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("..\\..\\windows\\system32\\config", "config"),
        ("C:\\Users\\someone\\Desktop\\id.pdf", "id.pdf"),
        ("a\\b\\report.pdf", "report.pdf"),
    ],
)
def test_sanitize_strips_windows_style_directory_components(raw: str, expected: str) -> None:
    assert sanitize_original_filename(raw) == expected


def test_sanitize_strips_control_characters() -> None:
    raw = "id\x00card\x1f.pdf\x7f"
    assert sanitize_original_filename(raw) == "idcard.pdf"


def test_sanitize_collapses_surrounding_whitespace() -> None:
    assert sanitize_original_filename("   id-card.pdf   ") == "id-card.pdf"


def test_sanitize_truncates_overly_long_filenames() -> None:
    raw = ("a" * 300) + ".pdf"
    result = sanitize_original_filename(raw)
    assert len(result) == 255


@pytest.mark.parametrize("raw", ["", "   ", "\x00\x1f", ".", "..", "/", "\\"])
def test_sanitize_falls_back_for_nothing_safe_remaining(raw: str) -> None:
    assert sanitize_original_filename(raw) == "unnamed-file"


def test_sanitize_never_returns_something_usable_as_a_path_traversal() -> None:
    result = sanitize_original_filename("../../../etc/passwd")
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
