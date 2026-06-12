"""Payment ORM model (M7 step 1).

Design:
- Payment belongs to an Invoice (invoice_id FK ON DELETE CASCADE — red-line 3).
- company_id root-anchors each payment for multi-tenancy (red-line 2, RESTRICT).
- payment_method_id FK SET NULL: deleting the dictionary entry keeps the name
  snapshot intact (D8).
- creator_id FK SET NULL: deleting the user doesn't break historical payments.
- All money columns are NUMERIC(18, 3); exchange_rate is NUMERIC(18, 8).
- Text columns use ``Text`` (red-line 10).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base

# ---------------------------------------------------------------------------
# Money column type alias – matches invoice.py convention
# ---------------------------------------------------------------------------

_MONEY = Numeric(18, 3)
_FX = Numeric(18, 8)


class Payment(Base):
    """Single payment record attached to an invoice."""

    __tablename__ = "payment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # -- Tenant scoping (red-line 2) ------------------------------------------
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        name="company_id",
    )

    # -- Parent invoice (CASCADE: deleting invoice removes its payments) ------
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # -- Payment date ---------------------------------------------------------
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # -- Amounts --------------------------------------------------------------
    amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[object] = mapped_column(
        _FX, nullable=False, server_default=text("1")
    )

    # -- Payment method (FK SET NULL + name snapshot) -------------------------
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_method.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_method_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Reference / notes (text, red-line 10) --------------------------------
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Creator (nullable FK: preserve payment if user is deleted) -----------
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -- Timestamps -----------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
