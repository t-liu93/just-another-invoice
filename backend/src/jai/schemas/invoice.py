"""Pydantic schemas for invoice calculation (M5 step 1).

``DiscountInput``      – discount specification (type + value)
``InvoiceLineInput``   – single line item input
``InvoiceCalculationRequest`` – request body for POST /invoices/calculate
``InvoiceLineCalculationRead`` – per-line calculation result
``InvoiceLineTaxRead`` – per-line tax breakdown
``InvoiceTaxRead``     – per-document tax breakdown
``VatTreatmentSnapshot`` – treatment snapshot included in response
``InvoiceCalculationRead``  – full calculation response

All monetary fields are ``Decimal``; the pricing engine quantises to
3 decimal places (``ROUND_HALF_UP``).  Schema layer never computes
amounts (red-line 1).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from jai.models._enums import DiscountType, InvoiceTaxMode

# ---------------------------------------------------------------------------
# Shared enums re-exported for convenience in generated schema.d.ts
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Discount
# ---------------------------------------------------------------------------


class DiscountInput(BaseModel):
    """Discount specification applied to a line or document.

    ``NONE`` means no discount (``value`` treated as 0 regardless).
    ``PERCENTAGE``: ``value`` is 0–100 (inclusive).
    ``FIXED``: ``value`` is a fixed monetary amount.
    """

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
    """Single line item in an invoice calculation request.

    ``product_id`` is optional and only recorded for reference; the caller
    must always supply the customer-facing ``name`` and ``unit_price``.
    """

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
# Calculation request
# ---------------------------------------------------------------------------


class InvoiceCalculationRequest(BaseModel):
    """Request body for ``POST /api/v1/invoices/calculate``.

    Shares core fields with ``InvoiceWrite`` (step 3).  Only computes a
    preview – nothing is persisted.
    """

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
    """Snapshot of the VAT treatment applied to this calculation."""

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
# Line calculation result
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
# Full calculation response
# ---------------------------------------------------------------------------


class InvoiceCalculationRead(BaseModel):
    """Response body for ``POST /api/v1/invoices/calculate``.

    All monetary fields are computed by the backend pricing engine.
    """

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
