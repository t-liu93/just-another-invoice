"""``Customer`` ORM model – per-company client records (M3).

Design (M3 step 1):
  - First real "per-company business data" table, establishing the pattern
    of ``company_id NOT NULL FK → company.id`` (red-line 2 / §3.3).
  - ``extra`` JSONB carries long-tail fields (notes, social media, etc.).
  - Addresses are in a separate ``address`` table (M3 step 2).
  - ``currency`` is a nullable ISO 4217 alpha-3 string (same calibre as
    ``company.base_currency``); FK to a currency dictionary deferred to M4.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base


class Customer(Base):
    """Client / customer record scoped to a single company."""

    __tablename__ = "customer"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # -- Tenant scoping (red-line 2) ------------------------------------------
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Owning company; v1 single-tenant, RLS reserved.",
    )

    # -- Identity --------------------------------------------------------------
    name: Mapped[str] = mapped_column(Text, nullable=False, comment="Display name.")
    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Legal entity / company name."
    )

    # -- Contact ---------------------------------------------------------------
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Financial --------------------------------------------------------------
    vat_id: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="VAT number for ICP / reverse-charge (M10)."
    )
    currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
        comment="ISO 4217 alpha-3 default currency for this customer.",
    )

    # -- Long-tail (JSONB, red-line 12 adjacent) --------------------------------
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'"),
        comment="Arbitrary extension data (notes, social media, etc.).",
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
