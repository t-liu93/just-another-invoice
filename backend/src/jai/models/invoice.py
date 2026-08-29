"""Invoice ORM models – Invoice / InvoiceLine / InvoiceTax / InvoiceLineTax (M5 step 3).

Design:
- Invoice is the root business document; company_id + customer_id are both
  RESTRICT (red-line 3 / 2).
- InvoiceLine, InvoiceTax, InvoiceLineTax cascade on DELETE from parent so
  removing an invoice removes all its sub-rows automatically (red-line 3).
- product_id / unit_id use SET NULL so deleting catalogue entries never
  breaks historical invoices.
- vat_rate_id on lines / taxes uses RESTRICT – you must not delete a rate
  that is referenced by any invoice line (enforced at DB level).
- The unique constraint on (company_id, invoice_number) is the DB-level
  safeguard against numbering races (red-line 4).
- All money columns are NUMERIC(18, 3) – same scale as quantize_money().
"""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jai.db import Base
from jai.models._enums import (
    AdvanceInputMode,
    DiscountType,
    InvoiceCreditStatus,
    InvoiceDocumentKind,
    InvoicePaidStatus,
    InvoiceSettlementStatus,
    InvoiceStatus,
    InvoiceTaxMode,
)

if TYPE_CHECKING:
    from jai.models.document import (
        FinalAdvanceApplication,
        InvoiceCorrection,
        InvoiceCreditBasisLine,
        InvoicePartySnapshot,
    )

# ---------------------------------------------------------------------------
# Money column type alias for readability
# ---------------------------------------------------------------------------

_MONEY = Numeric(18, 3)
_RATE = Numeric(6, 3)
_FX = Numeric(18, 8)


class Invoice(Base):
    """Top-level invoice document."""

    __tablename__ = "invoice"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # -- Tenant scoping (red-line 2) ------------------------------------------
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # -- Customer (RESTRICT: cannot delete customer referenced by invoice) ----
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # -- Numbering (red-line 4) -----------------------------------------------
    # Number/sequence are allocated at the DRAFT -> SENT (issue) transition,
    # not at create. A DRAFT therefore carries no number: deleting it leaves no
    # gap in the legal invoice sequence. NULLs are distinct in PG, so many
    # unnumbered drafts coexist while ``uq_invoice_company_number`` still
    # enforces uniqueness once a number exists.
    invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_sequence_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unique_hash: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Reserved for M9 public link; not active in M5."
    )
    reference_number: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Dates ----------------------------------------------------------------
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    supply_or_advance_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    document_kind: Mapped[InvoiceDocumentKind] = mapped_column(
        nullable=False, server_default="STANDARD"
    )
    quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quote.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="M12 authoritative quote provenance; converted_invoice_id remains compatible.",
    )
    # Formal Advance command provenance. Gross intent is stored after frozen
    # minor-unit normalization; percentage is exact up to its explicit 3-dp
    # command limit, so issue can replay the allocator without DB rounding.
    # These columns deliberately remain nullable for safely-upgraded legacy
    # rows; an old Advance DRAFT without provenance is rejected at issue rather
    # than being reconstructed from rounded line snapshots.
    advance_input_mode: Mapped[AdvanceInputMode | None] = mapped_column(Text, nullable=True)
    advance_gross_amount: Mapped[object | None] = mapped_column(Numeric(18, 3), nullable=True)
    advance_percentage: Mapped[object | None] = mapped_column(Numeric(6, 3), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    # Original accepted-Quote totals frozen when a Final is created.  They are
    # presentation/variance snapshots, never a second pricing authority.
    final_original_taxable_amount: Mapped[object | None] = mapped_column(_MONEY, nullable=True)
    final_original_vat_amount: Mapped[object | None] = mapped_column(_MONEY, nullable=True)
    final_original_gross_amount: Mapped[object | None] = mapped_column(_MONEY, nullable=True)

    # -- Lifecycle status -----------------------------------------------------
    status: Mapped[InvoiceStatus] = mapped_column(nullable=False, server_default="DRAFT")
    paid_status: Mapped[InvoicePaidStatus] = mapped_column(nullable=False, server_default="UNPAID")

    # -- Currency (M5: must equal company.base_currency) ---------------------
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[object] = mapped_column(_FX, nullable=False, server_default=text("1"))

    # -- VAT settings ---------------------------------------------------------
    tax_mode: Mapped[InvoiceTaxMode] = mapped_column(nullable=False)
    amounts_include_vat: Mapped[bool] = mapped_column(Boolean, nullable=False)
    vat_treatment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_treatment.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="RESTRICT"),
        nullable=True,
        comment="DOCUMENT mode only; NULL in LINE mode.",
    )

    # -- VAT treatment snapshot (immutable after creation) --------------------
    vat_treatment_code: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_label: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_effect: Mapped[str] = mapped_column(Text, nullable=False)
    vat_treatment_requires_icp: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # -- Discount -------------------------------------------------------------
    discount_type: Mapped[DiscountType] = mapped_column(nullable=False, server_default="NONE")
    discount_value: Mapped[object] = mapped_column(
        Numeric(10, 3), nullable=False, server_default=text("0")
    )
    document_discount_amount: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )

    # -- Calculated amounts ---------------------------------------------------
    subtotal_excl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)
    line_discount_total: Mapped[object] = mapped_column(_MONEY, nullable=False)
    taxable_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    vat_total: Mapped[object] = mapped_column(_MONEY, nullable=False)
    total_incl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)
    due_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    payable_before_payments: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    incoming_payment_total: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    credited_total: Mapped[object] = mapped_column(_MONEY, nullable=False, server_default=text("0"))
    refunded_total: Mapped[object] = mapped_column(_MONEY, nullable=False, server_default=text("0"))
    refund_due_amount: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    settlement_status: Mapped[InvoiceSettlementStatus] = mapped_column(
        nullable=False, server_default="OPEN"
    )
    credit_status: Mapped[InvoiceCreditStatus] = mapped_column(
        nullable=False, server_default="NOT_CREDITED"
    )

    # -- Base-currency mirrors (M5: same as above, exchange_rate = 1) --------
    base_subtotal_excl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_line_discount_total: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_taxable_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_vat_total: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_total_incl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_due_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    base_payable_before_payments: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    base_incoming_payment_total: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    base_credited_total: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    base_refunded_total: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )
    base_refund_due_amount: Mapped[object] = mapped_column(
        _MONEY, nullable=False, server_default=text("0")
    )

    # -- Content block snapshot text (M6 step 4) -----------------------------
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    warranty_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Creator (nullable FK: preserve invoice if user is deleted) ----------
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

    # -- Relationships --------------------------------------------------------
    lines: Mapped[list[InvoiceLine]] = relationship(
        "InvoiceLine",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InvoiceLine.sort_order",
    )
    taxes: Mapped[list[InvoiceTax]] = relationship(
        "InvoiceTax",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    party_snapshot: Mapped[InvoicePartySnapshot | None] = relationship(
        "InvoicePartySnapshot", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    credit_basis_lines: Mapped[list[InvoiceCreditBasisLine]] = relationship(
        "InvoiceCreditBasisLine",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InvoiceCreditBasisLine.sort_order",
    )
    final_advance_applications: Mapped[list[FinalAdvanceApplication]] = relationship(
        "FinalAdvanceApplication",
        foreign_keys="FinalAdvanceApplication.final_invoice_id",
        cascade="all, delete-orphan",
        # Only Final reads request this explicitly.  A default select-in here
        # would add a useless query to every legacy Standard chain projection.
        lazy="noload",
        order_by="FinalAdvanceApplication.sort_order",
    )
    correction: Mapped[InvoiceCorrection | None] = relationship(
        "InvoiceCorrection",
        foreign_keys="InvoiceCorrection.credit_note_id",
        cascade="all, delete-orphan",
        uselist=False,
        # Read/list serializers opt in explicitly.  A chain projection does
        # not serialize correction rows, so eager loading here would add a
        # query to every historical Standard-only chain.  ``raise`` rather
        # than ``noload`` leaves the explicit selectinload path usable.
        lazy="raise",
    )

    # -- Table constraints ----------------------------------------------------
    __table_args__ = (
        UniqueConstraint("company_id", "invoice_number", name="uq_invoice_company_number"),
    )


class InvoiceLine(Base):
    """Single line item within an invoice."""

    __tablename__ = "invoice_line"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -- Source / display fields ----------------------------------------------
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Long-form description; text (red-line 10)."
    )
    quantity: Mapped[object] = mapped_column(_MONEY, nullable=False)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="SET NULL"),
        nullable=True,
    )
    unit_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Input fields ---------------------------------------------------------
    unit_price: Mapped[object] = mapped_column(_MONEY, nullable=False)
    discount_type: Mapped[DiscountType] = mapped_column(nullable=False, server_default="NONE")
    discount_value: Mapped[object] = mapped_column(
        Numeric(10, 3), nullable=False, server_default=text("0")
    )
    vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="RESTRICT"),
        nullable=True,
        comment="NULL in DOCUMENT mode; RESTRICT when set.",
    )

    # -- VAT rate snapshot ----------------------------------------------------
    vat_rate_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    vat_rate_percent: Mapped[object | None] = mapped_column(_RATE, nullable=True)

    # -- Calculated snapshots -------------------------------------------------
    subtotal_excl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)
    subtotal_incl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)
    line_discount_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    document_discount_share: Mapped[object] = mapped_column(_MONEY, nullable=False)
    taxable_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    vat_total: Mapped[object] = mapped_column(_MONEY, nullable=False)
    total_incl_vat: Mapped[object] = mapped_column(_MONEY, nullable=False)

    # -- Relationships --------------------------------------------------------
    line_taxes: Mapped[list[InvoiceLineTax]] = relationship(
        "InvoiceLineTax",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class InvoiceTax(Base):
    """Document-level tax entry (DOCUMENT mode)."""

    __tablename__ = "invoice_tax"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vat_rate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="RESTRICT"),
        nullable=False,
    )

    vat_rate_label: Mapped[str] = mapped_column(Text, nullable=False)
    vat_rate_percent: Mapped[object] = mapped_column(_RATE, nullable=False)
    effective_vat_percent: Mapped[object] = mapped_column(_RATE, nullable=False)
    taxable_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    tax_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)


class InvoiceLineTax(Base):
    """Per-line tax entry (LINE mode)."""

    __tablename__ = "invoice_line_tax"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    invoice_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_line.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vat_rate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="RESTRICT"),
        nullable=False,
    )

    vat_rate_label: Mapped[str] = mapped_column(Text, nullable=False)
    vat_rate_percent: Mapped[object] = mapped_column(_RATE, nullable=False)
    effective_vat_percent: Mapped[object] = mapped_column(_RATE, nullable=False)
    taxable_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
    tax_amount: Mapped[object] = mapped_column(_MONEY, nullable=False)
