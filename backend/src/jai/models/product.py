"""Product catalogue ORM models – product_category + product (M4 step 3).

Internal cost catalogue used for M6.5 costing / estimation.
- ``ProductCategory``: groups products; carries a default margin rate.
- ``Product``: individual item with purchase cost, margin, default VAT rate,
  supplier text, and JSONB extra.  Sensitive cost/margin fields must never
  leak to customer-facing outputs (red-line 7 extension; enforced in M5/M6.5).

FK semantics (red-line 3 – no manual cascade):
- product.category_id → product_category ON DELETE SET NULL
  (delete a category → products keep their row, category_id becomes NULL)
- product.unit_id → unit ON DELETE SET NULL
- product.default_vat_rate_id → vat_rate ON DELETE SET NULL
- product.company_id / product_category.company_id → company ON DELETE RESTRICT
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base


class ProductCategory(Base):
    """Per-company product category with an optional default margin rate."""

    __tablename__ = "product_category"

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
    default_margin_rate: Mapped[object | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="Default margin rate for products in this category, e.g. 0.3000 = 30%",
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Product(Base):
    """Internal cost catalogue entry (purchase cost + margin + default VAT).

    cost/margin fields are internal-only; never serialise to customer outputs.
    """

    __tablename__ = "product"

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
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_category.id", ondelete="SET NULL"),
        nullable=True,
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="SET NULL"),
        nullable=True,
    )
    purchase_cost_excl_vat: Mapped[object | None] = mapped_column(
        Numeric(14, 3),
        nullable=True,
        comment="Purchase cost excluding VAT, e.g. 100.000",
    )
    margin_rate: Mapped[object | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="Product-level margin override; NULL means inherit category default.",
    )
    default_vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Long-tail fields as JSONB (red-line 10)",
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
