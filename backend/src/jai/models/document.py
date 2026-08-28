"""M12 issue-time document snapshots and immutable credit basis rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import PartySnapshotProvenance

_MONEY = Numeric(18, 3)
_RATE = Numeric(6, 3)


class InvoicePartySnapshot(Base):
    """One immutable seller/buyer identity snapshot for an issued invoice."""

    __tablename__ = "invoice_party_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    provenance: Mapped[PartySnapshotProvenance] = mapped_column(nullable=False)
    seller_name: Mapped[str] = mapped_column(Text, nullable=False)
    seller_legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller_vat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller_coc_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller_address: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    buyer_name: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_vat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_address: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    locale: Mapped[str] = mapped_column(Text, nullable=False)
    logo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("binary_asset.id", ondelete="RESTRICT"),
        nullable=True,
        comment="Retained issue-time logo; logo maintenance keeps referenced assets.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class InvoiceCreditBasisLine(Base):
    """Immutable issued charge basis used by later source-bound Credit Notes."""

    __tablename__ = "invoice_credit_basis_line"
    __table_args__ = (
        UniqueConstraint("invoice_line_id", name="uq_credit_basis_invoice_line"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_line.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    unit_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vat_rate_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    vat_rate_percent: Mapped[Decimal | None] = mapped_column(_RATE, nullable=True)
    vat_treatment_code: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_effect: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_requires_icp: Mapped[bool] = mapped_column(Boolean, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_net_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_vat_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_gross_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
