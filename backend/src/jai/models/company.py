"""``Company`` ORM model – singleton business profile (v1 one row).

Design (M2/M3):
  - Stores the company's business identity: name, VAT number, KvK number,
    contact details, structured European/Dutch address (M3 step 3), base currency.
  - ``logo_id`` is a nullable FK to ``binary_asset`` (added in M2 step 2).
  - Address fields aligned with customer ``Address`` table (M3 step 3):
    street / house_number / house_number_addition / postal_code / city /
    province / country_code.  ``address_line1``/``address_line2`` removed.
  - v1 enforces a single row via service-layer logic.
  - ``base_currency`` is a 3-letter ISO 4217 code (validated at schema level).
  - ``country_code`` is a 2-letter ISO 3166-1 alpha-2 code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
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
    legal_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        # Rendered as a trade-name disclosure line on documents when set.
        comment="Registered legal name for trade-name disclosure.",
    )
    vat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    coc_number: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Contact ----------------------------------------------------------------
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Address (structured, European/Dutch – aligned with customer.Address) --
    street: Mapped[str | None] = mapped_column(Text, nullable=True)
    house_number: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Text to accommodate ranges like '12-14'."
    )
    house_number_addition: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Toevoeging: A, bis, apartment number, etc."
    )
    postal_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    province: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="State/province; typically empty for NL."
    )
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

    # -- Logo (FK to binary_asset) ----------------------------------------------
    logo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("binary_asset.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to binary_asset.id (ON DELETE SET NULL).",
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
