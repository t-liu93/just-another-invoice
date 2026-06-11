"""Pydantic schemas for invoice (M5 steps 1 & 3).

Step 1 schemas (pricing preview):
  DiscountInput, InvoiceLineInput, InvoiceCalculationRequest,
  InvoiceLineCalculationRead, InvoiceLineTaxRead, InvoiceTaxRead,
  VatTreatmentSnapshot, InvoiceCalculationRead

Step 3 schemas (CRUD):
  InvoiceWrite, InvoiceRead, InvoiceLineRead, InvoiceLineReadTax,
  InvoiceListResponse, InvoiceStatusWrite,
  ProductInvoiceOptionRead, ProductInvoiceOptionListResponse

All monetary fields are ``Decimal``; the pricing engine quantises to 3 dp
(ROUND_HALF_UP).  Schema layer never computes amounts (red-line 1).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from jai.models._enums import DiscountType, InvoicePaidStatus, InvoiceStatus, InvoiceTaxMode

# ---------------------------------------------------------------------------
# Discount
# ---------------------------------------------------------------------------


class DiscountInput(BaseModel):
    """Discount specification applied to a line or document."""

    type: DiscountType = DiscountType.NONE
    value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Discount value. Ignored when type=NONE.",
    )


# ---------------------------------------------------------------------------
# Line input
# ---------------------------------------------------------------------------


class InvoiceLineInput(BaseModel):
    """Single line item in an invoice calculation or write request."""

    product_id: uuid.UUID | None = None
    name: str = Field(min_length=1)
    description: str | None = None
    quantity: Decimal = Field(gt=0, description="Must be > 0.")
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    unit_price: Decimal = Field(
        ge=0,
        description="Per-unit price (excl or incl VAT depending on flag).",
    )
    discount: DiscountInput = DiscountInput()
    vat_rate_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("description")
    @classmethod
    def strip_description(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
        return v


# ---------------------------------------------------------------------------
# Calculation request (step 1)
# ---------------------------------------------------------------------------


class InvoiceCalculationRequest(BaseModel):
    """Request body for ``POST /api/v1/invoices/calculate``."""

    customer_id: uuid.UUID
    invoice_date: date
    due_date: date | None = None
    currency: str | None = Field(
        default=None,
        max_length=3,
        min_length=3,
        description="ISO 4217. Must equal company base_currency in M5.",
    )
    tax_mode: InvoiceTaxMode
    amounts_include_vat: bool = False
    vat_treatment_id: uuid.UUID | None = None
    document_vat_rate_id: uuid.UUID | None = None
    discount: DiscountInput = DiscountInput()
    lines: list[InvoiceLineInput] = Field(min_length=1, description="At least 1 line required.")


# ---------------------------------------------------------------------------
# VatTreatment snapshot
# ---------------------------------------------------------------------------


class VatTreatmentSnapshot(BaseModel):
    """Snapshot of the VAT treatment applied to this calculation/invoice."""

    id: uuid.UUID
    code: str
    label: str
    effect: str
    requires_icp: bool


# ---------------------------------------------------------------------------
# Tax read schemas
# ---------------------------------------------------------------------------


class InvoiceLineTaxRead(BaseModel):
    """Per-line tax breakdown (LINE mode)."""

    vat_rate_id: uuid.UUID
    vat_rate_label: str
    vat_rate_percent: Decimal
    effective_vat_percent: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


class InvoiceTaxRead(BaseModel):
    """Per-document tax breakdown (DOCUMENT mode)."""

    vat_rate_id: uuid.UUID
    vat_rate_label: str
    vat_rate_percent: Decimal
    effective_vat_percent: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


# ---------------------------------------------------------------------------
# Line calculation result (step 1)
# ---------------------------------------------------------------------------


class InvoiceLineCalculationRead(BaseModel):
    """Per-line calculation result returned by the pricing engine."""

    product_id: uuid.UUID | None = None
    name: str
    description: str | None = None
    quantity: Decimal
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    unit_price: Decimal
    discount_type: DiscountType
    discount_value: Decimal
    vat_rate_id: uuid.UUID | None = None
    vat_rate_label: str | None = None
    vat_rate_percent: Decimal | None = None
    subtotal_excl_vat: Decimal
    subtotal_incl_vat: Decimal
    line_discount_amount: Decimal
    document_discount_share: Decimal
    taxable_amount: Decimal
    vat_total: Decimal
    total_incl_vat: Decimal


# ---------------------------------------------------------------------------
# Full calculation response (step 1)
# ---------------------------------------------------------------------------


class InvoiceCalculationRead(BaseModel):
    """Response body for ``POST /api/v1/invoices/calculate``."""

    tax_mode: InvoiceTaxMode
    amounts_include_vat: bool
    discount_type: DiscountType
    discount_value: Decimal
    vat_treatment_snapshot: VatTreatmentSnapshot | None = None
    subtotal_excl_vat: Decimal
    line_discount_total: Decimal
    document_discount_amount: Decimal
    taxable_amount: Decimal
    vat_total: Decimal
    total_incl_vat: Decimal
    lines: list[InvoiceLineCalculationRead]
    line_taxes: list[InvoiceLineTaxRead]
    document_taxes: list[InvoiceTaxRead]


# ---------------------------------------------------------------------------
# Invoice write (step 3)
# ---------------------------------------------------------------------------


class InvoiceWrite(BaseModel):
    """Request body for ``POST /PUT /api/v1/invoices``."""

    customer_id: uuid.UUID
    reference_number: str | None = None
    invoice_date: date
    due_date: date | None = None
    currency: str | None = Field(
        default=None,
        max_length=3,
        min_length=3,
        description="Must equal company base_currency in M5.",
    )
    tax_mode: InvoiceTaxMode
    amounts_include_vat: bool = False
    vat_treatment_id: uuid.UUID | None = None
    document_vat_rate_id: uuid.UUID | None = None
    discount: DiscountInput = DiscountInput()
    notes: str | None = None
    lines: list[InvoiceLineInput] = Field(min_length=1, description="At least 1 line required.")


# ---------------------------------------------------------------------------
# Invoice line read (step 3)
# ---------------------------------------------------------------------------


class InvoiceLineReadTax(BaseModel):
    """Per-line tax read (for InvoiceLineRead)."""

    id: uuid.UUID
    vat_rate_id: uuid.UUID
    vat_rate_label: str
    vat_rate_percent: Decimal
    effective_vat_percent: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


class InvoiceLineRead(BaseModel):
    """Invoice line as returned in InvoiceRead.

    Never exposes product.purchase_cost / margin / supplier / extra (red-line 7).
    """

    id: uuid.UUID
    sort_order: int
    product_id: uuid.UUID | None = None
    name: str
    description: str | None = None
    quantity: Decimal
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    unit_price: Decimal
    discount_type: DiscountType
    discount_value: Decimal
    vat_rate_id: uuid.UUID | None = None
    vat_rate_label: str | None = None
    vat_rate_percent: Decimal | None = None
    subtotal_excl_vat: Decimal
    subtotal_incl_vat: Decimal
    line_discount_amount: Decimal
    document_discount_share: Decimal
    taxable_amount: Decimal
    vat_total: Decimal
    total_incl_vat: Decimal
    line_taxes: list[InvoiceLineReadTax] = []


class InvoiceTaxRowRead(BaseModel):
    """Document-level tax row as returned in InvoiceRead."""

    id: uuid.UUID
    vat_rate_id: uuid.UUID
    vat_rate_label: str
    vat_rate_percent: Decimal
    effective_vat_percent: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


# ---------------------------------------------------------------------------
# Invoice read (step 3)
# ---------------------------------------------------------------------------


class InvoiceRead(BaseModel):
    """Full invoice representation returned by CRUD endpoints."""

    id: uuid.UUID
    company_id: uuid.UUID
    customer_id: uuid.UUID

    invoice_number: str
    sequence_number: int
    customer_sequence_number: int | None = None
    unique_hash: str | None = None
    reference_number: str | None = None

    invoice_date: date
    due_date: date | None = None

    status: InvoiceStatus
    paid_status: InvoicePaidStatus

    currency: str
    exchange_rate: Decimal

    tax_mode: InvoiceTaxMode
    amounts_include_vat: bool
    vat_treatment_id: uuid.UUID
    document_vat_rate_id: uuid.UUID | None = None

    vat_treatment_snapshot: VatTreatmentSnapshot

    discount_type: DiscountType
    discount_value: Decimal
    document_discount_amount: Decimal

    subtotal_excl_vat: Decimal
    line_discount_total: Decimal
    taxable_amount: Decimal
    vat_total: Decimal
    total_incl_vat: Decimal
    due_amount: Decimal

    base_subtotal_excl_vat: Decimal
    base_line_discount_total: Decimal
    base_taxable_amount: Decimal
    base_vat_total: Decimal
    base_total_incl_vat: Decimal
    base_due_amount: Decimal

    notes: str | None = None
    creator_id: uuid.UUID | None = None

    lines: list[InvoiceLineRead] = []
    taxes: list[InvoiceTaxRowRead] = []

    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Invoice list (step 3)
# ---------------------------------------------------------------------------


class InvoiceListItem(BaseModel):
    """Summary row in InvoiceListResponse."""

    id: uuid.UUID
    company_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    invoice_number: str
    reference_number: str | None = None
    invoice_date: date
    due_date: date | None = None
    status: InvoiceStatus
    paid_status: InvoicePaidStatus
    currency: str
    total_incl_vat: Decimal
    due_amount: Decimal
    vat_treatment_snapshot: VatTreatmentSnapshot
    created_at: datetime
    updated_at: datetime


class InvoiceListResponse(BaseModel):
    """Paginated invoice list."""

    items: list[InvoiceListItem]
    total: int


# ---------------------------------------------------------------------------
# Status transition (step 3)
# ---------------------------------------------------------------------------


class InvoiceStatusWrite(BaseModel):
    """Request body for ``POST /api/v1/invoices/{id}/status``."""

    status: InvoiceStatus


# ---------------------------------------------------------------------------
# Invoice product options (step 3) – customer-safe projection
# ---------------------------------------------------------------------------


class ProductInvoiceOptionRead(BaseModel):
    """Customer-safe product option for invoice line auto-fill.

    Never includes: purchase_cost_excl_vat, margin_rate, effective_margin_rate,
    supplier, extra (red-line 7 extension).
    """

    id: uuid.UUID
    name: str
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    default_vat_rate_id: uuid.UUID | None = None


class ProductInvoiceOptionListResponse(BaseModel):
    """List of customer-safe product options."""

    items: list[ProductInvoiceOptionRead]
