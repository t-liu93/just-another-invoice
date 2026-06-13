"""Storage abstraction for disk-backed file assets (M8 step 2).

Design
------
- ``Storage`` is an abstract base class defining the minimal interface
  needed by M8 receipts and M9 PDF rendering.  All concrete implementations
  must satisfy this contract.
- ``LocalStorage`` is the production implementation backed by the local
  filesystem.  The storage root is ``config.storage_root``; the directory
  layout is:  ``{root}/{namespace}/{key}``  where the key already encodes
  the full relative path (e.g. ``receipts/{company_id}/{expense_id}/{att_id}.png``).
- **Path traversal prevention (red-line 7)**:
  - The caller supplies a *namespace* (e.g. ``"receipts"``) and a *key*
    that is constructed entirely from controlled values (UUID + whitelisted
    extension).  The client-supplied original filename is **never** used as
    part of the storage key.
  - Before every read/write the resolved absolute path is verified to be
    inside ``storage_root`` (``is_relative_to`` check).  Any attempt to
    escape (e.g. via ``..`` in a stored key) raises ``ValueError``.

Receipt upload validation (mirroring ``services/assets.py``)
-------------------------------------------------------------
``validate_receipt`` checks:
1. MIME type against a whitelist (PNG / JPEG / WebP / PDF).
2. File size against ``config.max_receipt_bytes``.
3. Magic-bytes / Content-Type double-check: the first bytes of the file
   must match the declared MIME type; files with a mismatched magic
   signature are rejected (red-line 7).

The client-supplied filename is stored in the DB for display purposes only;
the actual disk path is derived solely from UUIDs and the whitelisted file
extension mapped from the verified MIME type.
"""

from __future__ import annotations

import abc
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Receipt upload validation constants
# ---------------------------------------------------------------------------

#: MIME types allowed for receipt attachments.
RECEIPT_ALLOWED_MIME: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
    }
)

#: Mapping from MIME type → canonical file extension used in storage key.
#: The extension is controlled by the server and never taken from the client.
MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

#: Magic-byte signatures for supported MIME types.
#: Each value is a sequence of (offset, expected_bytes) pairs.
#: All conditions must match for the signature to be considered valid.
_MAGIC: dict[str, list[tuple[int, bytes]]] = {
    "image/png": [(0, b"\x89PNG\r\n\x1a\n")],
    "image/jpeg": [(0, b"\xff\xd8\xff")],
    "image/webp": [(0, b"RIFF"), (8, b"WEBP")],
    "application/pdf": [(0, b"%PDF")],
}


class ReceiptValidationError(ValueError):
    """Raised when a receipt upload fails validation."""


def _check_magic(content: bytes, mime_type: str) -> bool:
    """Return True if ``content`` has a magic-byte signature matching ``mime_type``.

    Each pattern is a list of (offset, expected) pairs; all must match.
    """
    patterns = _MAGIC.get(mime_type)
    if not patterns:
        return False
    for offset, expected in patterns:
        end = offset + len(expected)
        if len(content) < end:
            return False
        if content[offset:end] != expected:
            return False
    return True


def validate_receipt(
    content: bytes,
    mime_type: str,
    max_bytes: int,
) -> None:
    """Validate a receipt upload.

    Checks performed (red-line 7):
    1. MIME type is in the whitelist.
    2. File size is within the configured limit.
    3. Magic bytes match the declared MIME type (double-check against spoofed
       Content-Type headers or renamed extensions).

    Parameters
    ----------
    content:
        Raw file bytes.
    mime_type:
        MIME type as reported by the client (from the ``Content-Type`` header
        of the multipart part).
    max_bytes:
        Maximum allowed byte size (from ``config.max_receipt_bytes``).

    Raises
    ------
    ReceiptValidationError
        If any check fails.
    """
    mime_type = mime_type.split(";")[0].strip().lower()

    if mime_type not in RECEIPT_ALLOWED_MIME:
        allowed = ", ".join(sorted(RECEIPT_ALLOWED_MIME))
        raise ReceiptValidationError(
            f"Unsupported MIME type: {mime_type!r}. Allowed: {allowed}."
        )

    if len(content) > max_bytes:
        raise ReceiptValidationError(
            f"File too large: {len(content):,} bytes. "
            f"Maximum: {max_bytes:,} bytes ({max_bytes // (1024 * 1024)} MB)."
        )

    if not _check_magic(content, mime_type):
        raise ReceiptValidationError(
            f"File content does not match declared MIME type {mime_type!r}. "
            "Ensure the file is not renamed or corrupted."
        )


# ---------------------------------------------------------------------------
# Storage abstraction
# ---------------------------------------------------------------------------


class Storage(abc.ABC):
    """Abstract storage backend.

    Minimum interface required by M8 receipts and M9 PDF attachments.
    The *namespace* parameter allows logical separation (e.g. ``"receipts"``,
    ``"pdfs"``) within a single backend root.
    """

    @abc.abstractmethod
    def save(
        self,
        namespace: str,
        key: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """Persist ``content`` and return an opaque storage key.

        The returned key can later be passed to :meth:`open`, :meth:`delete`,
        and :meth:`exists`.  Implementations must ensure the key is stable and
        does not contain sensitive path information that should not be exposed
        to clients.

        Parameters
        ----------
        namespace:
            Logical namespace (e.g. ``"receipts"``).  Used as the first path
            component in ``LocalStorage``.
        key:
            Relative path within the namespace (e.g.
            ``"{company_id}/{expense_id}/{att_id}.png"``).  Must consist only
            of safe path components (no ``..``, no absolute paths).
        content:
            Raw bytes to store.
        content_type:
            MIME type (stored as metadata by implementations that support it).

        Returns
        -------
        str
            An opaque storage key that can be passed back to :meth:`open` etc.
        """

    @abc.abstractmethod
    def open(self, key: str) -> bytes:
        """Return the content for a previously stored key.

        Raises
        ------
        FileNotFoundError
            If the key does not exist in storage.
        """

    @abc.abstractmethod
    def delete(self, key: str) -> None:
        """Remove the file identified by ``key``.

        This is a best-effort delete; implementations should log but not raise
        if the file is already missing.
        """

    @abc.abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the key exists in storage."""


# ---------------------------------------------------------------------------
# LocalStorage
# ---------------------------------------------------------------------------


class LocalStorage(Storage):
    """Filesystem-backed storage implementation.

    Layout::

        {root}/{namespace}/{relative_key}

    For receipts the caller supplies a key like::

        receipts/{company_id}/{expense_id}/{attachment_id}.{ext}

    The ``save`` method returns the full storage key in the form
    ``{namespace}/{relative_key}``; ``open``/``delete``/``exists`` accept
    the same opaque key.

    Path traversal prevention (red-line 7)
    ---------------------------------------
    Before every filesystem operation the resolved absolute path is checked
    to be strictly inside ``self.root``.  Any attempt to escape (e.g. via
    ``..`` components in a stored key) raises ``ValueError``.
    """

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _resolve_safe(self, *parts: str) -> Path:
        """Resolve parts relative to root and assert the result is inside root.

        Raises
        ------
        ValueError
            If the resolved path escapes the storage root.
        """
        joined = self.root.joinpath(*parts).resolve()
        if not joined.is_relative_to(self.root):
            raise ValueError(
                f"Storage path escapes root: {joined!r} is not under {self.root!r}."
            )
        return joined

    def save(
        self,
        namespace: str,
        key: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """Save ``content`` to ``{root}/{namespace}/{key}`` and return the storage key."""
        storage_key = f"{namespace}/{key}"
        target = self._resolve_safe(namespace, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        logger.debug("LocalStorage.save: wrote %d bytes to %s", len(content), target)
        return storage_key

    def open(self, key: str) -> bytes:
        """Read and return file bytes for ``key``."""
        target = self._resolve_safe(key)
        if not target.is_file():
            raise FileNotFoundError(f"Storage key not found: {key!r}")
        return target.read_bytes()

    def delete(self, key: str) -> None:
        """Delete the file for ``key``.  Logs but does not raise on missing files."""
        try:
            target = self._resolve_safe(key)
        except ValueError:
            logger.warning("LocalStorage.delete: rejected unsafe key %r", key)
            return
        if target.exists():
            target.unlink()
            logger.debug("LocalStorage.delete: removed %s", target)
        else:
            logger.warning("LocalStorage.delete: key not found (already removed?) %r", key)

    def exists(self, key: str) -> bool:
        """Return True if the file for ``key`` exists."""
        try:
            target = self._resolve_safe(key)
        except ValueError:
            return False
        return target.is_file()


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------


_storage_instance: Storage | None = None


def get_storage() -> Storage:
    """Return the application-scoped ``LocalStorage`` instance.

    Reads ``config.storage_root`` on first call; subsequent calls return the
    cached instance.  Tests override this by monkeypatching or by calling
    ``set_storage()``.
    """
    global _storage_instance
    if _storage_instance is None:
        from jai.config import get_settings

        settings = get_settings()
        _storage_instance = LocalStorage(settings.storage_root)
    return _storage_instance


def set_storage(storage: Storage) -> None:
    """Override the module-level storage instance (used in tests)."""
    global _storage_instance
    _storage_instance = storage
