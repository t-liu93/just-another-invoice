"""Payment and quote-payment VAT snapshot ORM models.

Design:
- A payment belongs to an invoice, a quote, or both after quote conversion.
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
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jai.db import Base

# ---------------------------------------------------------------------------
# Money column type alias – matches invoice.py convention
# ---------------------------------------------------------------------------

_MONEY = Numeric(18, 3)
_FX = Numeric(18, 8)


class Payment(Base):
    """Single payment record attached to an invoice and/or source quote."""

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

    # -- Document links -------------------------------------------------------
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quote.id", ondelete="RESTRICT"),
        nullable=True,
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

    taxes: Mapped[list[PaymentTax]] = relationship(
        "PaymentTax",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PaymentTax.sort_order",
    )

    __table_args__ = (
        CheckConstraint(
            "invoice_id IS NOT NULL OR quote_id IS NOT NULL",
            name="ck_payment_document_link",
        ),
    )


class PaymentTax(Base):
    """VAT allocation snapshot for one quote-origin payment bucket."""

    __tablename__ = "payment_tax"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="SET NULL"),
        nullable=True,
    )
    vat_rate_label: Mapped[str] = mapped_column(Text, nullable=False)
    vat_rate_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    vat_treatment_code: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_effect: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_requires_icp: Mapped[bool] = mapped_column(nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_taxable_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_vat_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_gross_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    bucket_key: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
