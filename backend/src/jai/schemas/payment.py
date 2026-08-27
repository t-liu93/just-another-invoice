"""Pydantic schemas for payments (M7 step 1 + step 3).

PaymentInput        – request body for recording a payment (raw input only).
PaymentRead         – full payment representation returned by read endpoints.
InvoicePaymentsResponse – aggregate: invoice summary + list of its payments.
PaymentListItem     – minimal overview row for the global payments list (step 3).
PaymentListResponse – paginated global payments list (step 3).

Schema layer never computes amounts (red-line 1).  All monetary fields are
``Decimal``.  Text fields are plain ``str`` (red-line 10).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from jai.models._enums import InvoicePaidStatus, InvoiceStatus

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class PaymentInput(BaseModel):
    """Request body for POST /api/v1/invoices/{id}/payments.

    Only collects raw user input – amount, date, optional method, reference,
    note.  base_amount / currency / exchange_rate are derived by the service
    (D2: single base currency).
    """

    payment_date: date
    amount: Decimal = Field(gt=0, description="Payment amount – must be > 0.")
    payment_method_id: uuid.UUID | None = None
    reference: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class PaymentTaxRead(BaseModel):
    """Persisted VAT allocation for one payment/rate bucket."""

    vat_rate_id: uuid.UUID | None = None
    vat_rate_label: str
    vat_rate_percent: Decimal
    taxable_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    base_taxable_amount: Decimal
    base_vat_amount: Decimal
    base_gross_amount: Decimal


class PaymentRead(BaseModel):
    """Full payment record as returned by read endpoints."""

    id: uuid.UUID
    origin_type: Literal["INVOICE", "QUOTE"]
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    quote_id: uuid.UUID | None = None
    quote_number: str | None = None
    payment_date: date
    amount: Decimal
    base_amount: Decimal
    currency: str
    payment_method_id: uuid.UUID | None = None
    payment_method_name: str | None = None
    reference: str | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime
    tax_breakdown: list[PaymentTaxRead] = []


# ---------------------------------------------------------------------------
# Aggregate response
# ---------------------------------------------------------------------------


class InvoicePaymentsResponse(BaseModel):
    """Aggregate: invoice payment state + ordered list of payment records.

    Returned by POST/GET /api/v1/invoices/{id}/payments.
    ``items`` is ordered by (payment_date ASC, created_at ASC).

    ``invoice_number`` is optional: the aggregate (read) endpoint is also called
    for an unnumbered DRAFT (numbering is deferred to the DRAFT -> SENT issue
    transition), in which case it is ``None`` and ``items`` is empty.  A single
    payment, by contrast, only ever exists on an issued (numbered) invoice.
    """

    invoice_id: uuid.UUID
    invoice_number: str | None = None
    total_incl_vat: Decimal
    base_total_incl_vat: Decimal
    paid_total: Decimal
    base_paid_total: Decimal
    due_amount: Decimal
    base_due_amount: Decimal
    paid_status: InvoicePaidStatus
    status: InvoiceStatus
    items: list[PaymentRead] = []


class QuotePaymentsResponse(BaseModel):
    """Authoritative derived payment aggregate for one quote."""

    quote_id: uuid.UUID
    quote_number: str
    converted_invoice_id: uuid.UUID | None = None
    total_incl_vat: Decimal
    paid_total: Decimal
    remaining_amount: Decimal
    items: list[PaymentRead] = []


class PaymentMutationResponse(BaseModel):
    """All aggregates affected by editing or deleting a payment."""

    payment_id: uuid.UUID
    deleted: bool
    quote: QuotePaymentsResponse | None = None
    invoice: InvoicePaymentsResponse | None = None


# ---------------------------------------------------------------------------
# Global payments overview (step 3)
# ---------------------------------------------------------------------------


class PaymentListItem(BaseModel):
    """Minimal overview row for the global payments list (GET /api/v1/payments).

    Only exposes summary fields – no internal base_*/reference/note fields.
    Minimal exposure principle: give the overview page what it needs, nothing more.
    """

    id: uuid.UUID
    origin_type: Literal["INVOICE", "QUOTE"]
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    quote_id: uuid.UUID | None = None
    quote_number: str | None = None
    customer_id: uuid.UUID
    customer_name: str
    payment_date: date
    amount: Decimal
    payment_method_name: str | None = None
    created_at: datetime


class PaymentListResponse(BaseModel):
    """Paginated global payments list."""

    items: list[PaymentListItem]
    total: int
