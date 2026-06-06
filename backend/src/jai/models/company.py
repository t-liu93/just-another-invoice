"""``Company`` ORM model – singleton business profile (v1 one row).

Design (M2):
  - Stores the company's business identity: name, VAT number, KvK number,
    contact details, address (inline standard fields), base currency.
  - ``logo_id`` is a nullable FK to ``binary_asset`` (added in step 2).
  - v1 enforces a single row via service-layer logic.
  - ``base_currency`` is a 3-letter ISO 4217 code (validated at schema level).
  - ``country_code`` is a 2-letter ISO 3166-1 alpha-2 code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base


class Company(Base):
    """Business profile – singleton (v1 one row)."""

    __tablename__ = "company"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # -- Identity ---------------------------------------------------------------
    name: Mapped[str] = mapped_column(Text, nullable=False)
    vat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    coc_number: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Contact ----------------------------------------------------------------
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Address (inline, single address) --------------------------------------
    address_line1: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_line2: Mapped[str | None] = mapped_column(Text, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_code: Mapped[str | None] = mapped_column(
        String(2),
        nullable=True,
        comment="ISO 3166-1 alpha-2 country code.",
    )

    # -- Financial / numbering --------------------------------------------------
    base_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        comment="ISO 4217 3-letter currency code.",
    )

    # -- Logo (FK to binary_asset, added in step 2) ----------------------------
    logo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="FK to binary_asset.id (ON DELETE SET NULL, added in step 2).",
    )

    # -- Timestamps -------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
