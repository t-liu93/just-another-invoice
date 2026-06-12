"""Estimate ORM models – Estimate / EstimateGroup / EstimateLine (M6.5 step 2).

Internal cost-to-sell-price work sheet; owner-only, never customer-facing.

FK semantics (red-line 3 – no manual cascade):
- estimate.company_id       → company       ON DELETE RESTRICT
- estimate.customer_id      → customer      ON DELETE SET NULL   (nullable)
- estimate.generated_quote_id → quote       ON DELETE SET NULL   (nullable, reverse link)
- estimate_group.estimate_id → estimate     ON DELETE CASCADE
- estimate_group.vat_rate_id → vat_rate     ON DELETE RESTRICT   (nullable)
- estimate_line.estimate_id  → estimate     ON DELETE CASCADE
- estimate_line.group_id     → estimate_group ON DELETE SET NULL (nullable)
- estimate_line.product_id   → product      ON DELETE SET NULL   (nullable; snapshot protects cost)
- estimate_line.unit_id      → unit         ON DELETE SET NULL   (nullable)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jai.db import Base

_MONEY = Numeric(18, 3)
_COST = Numeric(14, 3)
_RATE = Numeric(6, 4)
_QTY = Numeric(18, 3)


class Estimate(Base):
    """Internal cost estimation work sheet (owner-only, never customer-facing)."""

    __tablename__ = "estimate"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reverse link to last generated quote; SET NULL if quote deleted (red-line 3)
    generated_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quote.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Persisted totals (authoritative; recalculated on every write)
    total_margin: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    total_excl_vat: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    groups: Mapped[list[EstimateGroup]] = relationship(
        "EstimateGroup",
        cascade="all, delete-orphan",
        lazy="selectin",
        # ``id`` tiebreaker keeps the order deterministic when ``sort_order``
        # values collide (read path mirrors generate_quote_from_estimate).
        order_by="EstimateGroup.sort_order, EstimateGroup.id",
    )
    lines: Mapped[list[EstimateLine]] = relationship(
        "EstimateLine",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EstimateLine.sort_order",
    )


class EstimateGroup(Base):
    """Customer-facing projection of a set of estimate lines (one group → one quote line)."""

    __tablename__ = "estimate_group"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    estimate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estimate.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    public_description: Mapped[str] = mapped_column(Text, nullable=False)
    vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="RESTRICT"),
        nullable=True,
    )
    group_sell_excl_vat: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EstimateLine(Base):
    """Internal cost/margin row (owner-only; cost columns never enter customer-facing outputs)."""

    __tablename__ = "estimate_line"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    estimate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estimate.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("estimate_group.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Source product (SET NULL on delete; snapshot fields below preserve cost data)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="SET NULL"),
        nullable=True,
    )
    unit_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cost snapshot – immutable after creation; M4 price changes do NOT affect existing estimates
    unit_cost_excl_vat: Mapped[object] = mapped_column(_COST, nullable=False)
    quantity: Mapped[object] = mapped_column(_QTY, nullable=False)
    margin_rate: Mapped[object] = mapped_column(
        _RATE, nullable=False, server_default=text("0")
    )

    # Calculated amount snapshots (set at create/update, preserved after that)
    line_total: Mapped[object] = mapped_column(_MONEY, nullable=False)
    margin_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    line_sell_excl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
