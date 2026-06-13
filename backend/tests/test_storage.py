"""Unit tests for services/storage.py (M8 step 2).

Tests (no DB, no network):
- LocalStorage: save / open / delete / exists happy flow
- LocalStorage: path traversal prevention (red-line 7)
- validate_receipt: MIME whitelist (allowed / rejected)
- validate_receipt: size limit
- validate_receipt: magic-bytes double-check (image vs declared MIME)
- validate_receipt: MIME parameter stripping ("image/jpeg; ...")
- MIME_TO_EXT mapping completeness
"""

from __future__ import annotations

import struct

import pytest

from jai.services.storage import (
    MIME_TO_EXT,
    RECEIPT_ALLOWED_MIME,
    LocalStorage,
    ReceiptValidationError,
    validate_receipt,
)

# ---------------------------------------------------------------------------
# Minimal valid magic-byte samples for each allowed MIME type
# ---------------------------------------------------------------------------

# PNG: 8-byte signature
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

# JPEG: SOI marker
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100

# WebP: RIFF....WEBP
_WEBP = b"RIFF" + struct.pack("<I", 200) + b"WEBP" + b"\x00" * 100

# PDF: %PDF
_PDF = b"%PDF-1.4\n" + b"\x00" * 100


# ---------------------------------------------------------------------------
# LocalStorage – happy flow
# ---------------------------------------------------------------------------


class TestLocalStorage:
    def test_save_open_roundtrip(self, tmp_path):
        """Save bytes, then open returns the same bytes."""
        storage = LocalStorage(str(tmp_path))
        key = storage.save("receipts", "ns/test.png", _PNG, "image/png")
        assert storage.open(key) == _PNG

    def test_exists_true_after_save(self, tmp_path):
        storage = LocalStorage(str(tmp_path))
        key = storage.save("receipts", "c1/e1/a1.pdf", _PDF, "application/pdf")
        assert storage.exists(key) is True

    def test_exists_false_before_save(self, tmp_path):
        storage = LocalStorage(str(tmp_path))
        assert storage.exists("receipts/no/such/file.png") is False

    def test_delete_removes_file(self, tmp_path):
        storage = LocalStorage(str(tmp_path))
        key = storage.save("receipts", "c1/e1/a1.jpg", _JPEG, "image/jpeg")
        assert storage.exists(key) is True
        storage.delete(key)
        assert storage.exists(key) is False

    def test_delete_missing_file_does_not_raise(self, tmp_path):
        """Deleting a non-existent key must not raise (logs only)."""
        storage = LocalStorage(str(tmp_path))
        # Should complete without raising
        storage.delete("receipts/nonexistent/file.png")

    def test_open_missing_key_raises_file_not_found(self, tmp_path):
        storage = LocalStorage(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            storage.open("receipts/does/not/exist.png")

    def test_nested_directories_created(self, tmp_path):
        """Save creates nested parent directories automatically."""
        storage = LocalStorage(str(tmp_path))
        key = storage.save(
            "receipts",
            "company-id/expense-id/att-id.webp",
            _WEBP,
            "image/webp",
        )
        assert storage.exists(key) is True

    def test_storage_key_is_namespace_slash_relative(self, tmp_path):
        """The returned storage key must be namespace/relative_key."""
        storage = LocalStorage(str(tmp_path))
        key = storage.save("receipts", "a/b/c.png", _PNG, "image/png")
        assert key == "receipts/a/b/c.png"


# ---------------------------------------------------------------------------
# LocalStorage – path traversal prevention (red-line 7)
# ---------------------------------------------------------------------------


class TestLocalStoragePathTraversal:
    def test_traversal_via_dotdot_in_key_rejected(self, tmp_path):
        """A key with '..' components must be rejected by _resolve_safe."""
        storage = LocalStorage(str(tmp_path))
        with pytest.raises(ValueError, match="escapes root"):
            storage.open("../../etc/passwd")

    def test_traversal_in_namespace_rejected(self, tmp_path):
        """Namespace with '..' must be rejected."""
        storage = LocalStorage(str(tmp_path))
        with pytest.raises(ValueError, match="escapes root"):
            storage._resolve_safe("..", "evil.txt")

    def test_absolute_key_rejected(self, tmp_path):
        """An absolute path masquerading as a key must be rejected."""
        storage = LocalStorage(str(tmp_path))
        # '/etc/passwd' joined to root becomes root + /etc/passwd = /etc/passwd
        # which is NOT relative to root.
        with pytest.raises(ValueError, match="escapes root"):
            storage.open("/etc/passwd")


# ---------------------------------------------------------------------------
# validate_receipt – MIME whitelist
# ---------------------------------------------------------------------------


class TestValidateReceiptMime:
    @pytest.mark.parametrize(
        "mime, content",
        [
            ("image/png", _PNG),
            ("image/jpeg", _JPEG),
            ("image/webp", _WEBP),
            ("application/pdf", _PDF),
        ],
    )
    def test_allowed_mime_types_accepted(self, mime: str, content: bytes):
        """All four allowed MIME types are accepted with valid magic bytes."""
        validate_receipt(content, mime, max_bytes=10 * 1024 * 1024)

    @pytest.mark.parametrize(
        "bad_mime",
        [
            "text/plain",
            "application/json",
            "image/gif",
            "image/svg+xml",
            "application/octet-stream",
            "text/csv",
            "image/tiff",
        ],
    )
    def test_non_whitelisted_mime_rejected(self, bad_mime: str):
        """MIME types outside the whitelist raise ReceiptValidationError."""
        with pytest.raises(ReceiptValidationError, match="Unsupported MIME type"):
            validate_receipt(_PNG, bad_mime, max_bytes=10 * 1024 * 1024)

    def test_mime_parameter_stripped(self):
        """'image/jpeg; charset=binary' is treated as 'image/jpeg'."""
        validate_receipt(_JPEG, "image/jpeg; charset=binary", max_bytes=10 * 1024 * 1024)


# ---------------------------------------------------------------------------
# validate_receipt – size limit
# ---------------------------------------------------------------------------


class TestValidateReceiptSize:
    def test_file_exactly_at_limit_accepted(self):
        """File size == max_bytes is accepted."""
        max_bytes = len(_PNG)
        validate_receipt(_PNG, "image/png", max_bytes=max_bytes)

    def test_file_over_limit_rejected(self):
        """File size > max_bytes raises ReceiptValidationError."""
        max_bytes = len(_PNG) - 1
        with pytest.raises(ReceiptValidationError, match="too large"):
            validate_receipt(_PNG, "image/png", max_bytes=max_bytes)

    def test_tiny_limit_rejects_any_content(self):
        """max_bytes=0 rejects even an empty-ish file."""
        with pytest.raises(ReceiptValidationError, match="too large"):
            validate_receipt(_PNG, "image/png", max_bytes=1)


# ---------------------------------------------------------------------------
# validate_receipt – magic-bytes double-check
# ---------------------------------------------------------------------------


class TestValidateReceiptMagicBytes:
    def test_jpeg_content_declared_as_jpeg_accepted(self):
        validate_receipt(_JPEG, "image/jpeg", max_bytes=10 * 1024 * 1024)

    def test_png_content_declared_as_png_accepted(self):
        validate_receipt(_PNG, "image/png", max_bytes=10 * 1024 * 1024)

    def test_webp_content_declared_as_webp_accepted(self):
        validate_receipt(_WEBP, "image/webp", max_bytes=10 * 1024 * 1024)

    def test_pdf_content_declared_as_pdf_accepted(self):
        validate_receipt(_PDF, "application/pdf", max_bytes=10 * 1024 * 1024)

    def test_png_content_declared_as_jpeg_rejected(self):
        """PNG bytes declared as image/jpeg → magic mismatch → 422."""
        with pytest.raises(ReceiptValidationError, match="does not match"):
            validate_receipt(_PNG, "image/jpeg", max_bytes=10 * 1024 * 1024)

    def test_jpeg_content_declared_as_png_rejected(self):
        with pytest.raises(ReceiptValidationError, match="does not match"):
            validate_receipt(_JPEG, "image/png", max_bytes=10 * 1024 * 1024)

    def test_pdf_content_declared_as_png_rejected(self):
        with pytest.raises(ReceiptValidationError, match="does not match"):
            validate_receipt(_PDF, "image/png", max_bytes=10 * 1024 * 1024)

    def test_random_bytes_declared_as_pdf_rejected(self):
        """Random bytes declared as PDF fail the %PDF magic check."""
        random_bytes = b"\x00\x01\x02\x03" * 50
        with pytest.raises(ReceiptValidationError, match="does not match"):
            validate_receipt(random_bytes, "application/pdf", max_bytes=10 * 1024 * 1024)

    def test_empty_file_rejected(self):
        """Empty content fails both size (if max_bytes > 0) and magic checks."""
        with pytest.raises(ReceiptValidationError, match="does not match"):
            validate_receipt(b"", "image/png", max_bytes=10 * 1024 * 1024)

    def test_disguised_extension_rejected(self):
        """A JPEG file renamed to .png (served as image/png) is rejected."""
        # _JPEG starts with 0xFF 0xD8 0xFF which fails the PNG magic check
        with pytest.raises(ReceiptValidationError, match="does not match"):
            validate_receipt(_JPEG, "image/png", max_bytes=10 * 1024 * 1024)


# ---------------------------------------------------------------------------
# MIME_TO_EXT coverage
# ---------------------------------------------------------------------------


def test_mime_to_ext_covers_all_allowed():
    """Every MIME type in RECEIPT_ALLOWED_MIME has an entry in MIME_TO_EXT."""
    for mime in RECEIPT_ALLOWED_MIME:
        assert mime in MIME_TO_EXT, f"Missing extension mapping for {mime!r}"


def test_mime_to_ext_values_are_simple_strings():
    """Extension values must be plain strings without leading dots."""
    for mime, ext in MIME_TO_EXT.items():
        assert isinstance(ext, str), f"Extension for {mime!r} is not a str: {ext!r}"
        assert not ext.startswith("."), f"Extension for {mime!r} has a leading dot: {ext!r}"
