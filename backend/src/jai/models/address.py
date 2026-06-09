"""``Address`` ORM model – structured address linked to a customer (M3 step 2).

Design:
  - One row per (customer, type) pair; UNIQUE(customer_id, type) enforced in DB.
  - FK → customer.id with ON DELETE CASCADE; the ORM relationship carries
    cascade="all, delete-orphan" so both layers stay aligned (red-line 3).
  - European/Dutch address fields: street + house_number (text, not int, to
    accommodate "12-14") + optional toevoeging + postal_code/city/province +
    country_code (ISO 3166-1 alpha-2, char 2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import AddressType


class Address(Base):
    """Billing or shipping address for a customer."""

    __tablename__ = "address"
    __table_args__ = (
        UniqueConstraint("customer_id", "type", name="uq_address_customer_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[AddressType] = mapped_column(
        Enum(AddressType, name="addresstype"),
        nullable=False,
    )

    # -- European/Dutch structured fields ---------------------------------------
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
        String(2), nullable=True, comment="ISO 3166-1 alpha-2 country code."
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
