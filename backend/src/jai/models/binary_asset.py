"""``BinaryAsset`` ORM model – generic small-blob storage.

Design (M2 step 2):
  - Stores small binary blobs (logos, future small assets) directly in
    ``bytea``.  Intentionally **not** for large file uploads (receipts go to
    file/disk storage in M8 with a storage abstraction).
  - ``sha256`` is optional, reserved for future dedup.
  - ``company.logo_id`` FK → ``binary_asset.id`` (``ON DELETE SET NULL``) is
    added in the same migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base


class BinaryAsset(Base):
    """Generic small-blob storage (logos, icons, etc.)."""

    __tablename__ = "binary_asset"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    content: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
