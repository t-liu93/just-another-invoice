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

from pydantic import BaseModel, ConfigDict, Field

from jai.models._enums import InvoicePaidStatus, InvoiceStatus, PaymentDirection


class PaymentInputErrorDetail(BaseModel):
    """Safe, machine-readable 409/422 detail for mutable payment endpoints."""

    code: str
    message: str


class PaymentInputErrorResponse(BaseModel):
    """Stable error envelope used by Refund CRUD and generic payment mutations."""

    detail: PaymentInputErrorDetail


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class PaymentInput(BaseModel):
    """Request body for POST /api/v1/invoices/{id}/payments.

    Only collects raw user input – amount, date, optional method, reference,
    note.  base_amount / currency / exchange_rate are derived by the service
    (D2: single base currency).
    """

    # The route fixes direction and document context: callers provide only
    # raw cash input for either the incoming-payment or dedicated refund flow.
    model_config = ConfigDict(extra="forbid")

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
    origin_type: Literal["INVOICE", "QUOTE", "CREDIT_NOTE"]
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    quote_id: uuid.UUID | None = None
    quote_number: str | None = None
    direction: PaymentDirection = PaymentDirection.INCOMING
    credit_note_id: uuid.UUID | None = None
    credit_note_number: str | None = None
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
    refund: RefundCollectionRead | None = None


class RefundCollectionRead(BaseModel):
    """Refund cash linked to one issued Credit Note; all money is service-derived."""

    credit_note_id: uuid.UUID
    credit_note_number: str | None = None
    source_invoice_id: uuid.UUID
    currency: str
    issued_entitlement: Decimal
    base_issued_entitlement: Decimal
    refunded_total: Decimal
    base_refunded_total: Decimal
    remaining_entitlement: Decimal
    base_remaining_entitlement: Decimal
    chain_refund_due_amount: Decimal
    base_chain_refund_due_amount: Decimal
    items: list[PaymentRead] = []


# ---------------------------------------------------------------------------
# Global payments overview (step 3)
# ---------------------------------------------------------------------------


class PaymentListItem(BaseModel):
    """Minimal overview row for the global payments list (GET /api/v1/payments).

    Only exposes summary fields – no internal base_*/reference/note fields.
    Minimal exposure principle: give the overview page what it needs, nothing more.
    """

    id: uuid.UUID
    origin_type: Literal["INVOICE", "QUOTE", "CREDIT_NOTE"]
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    quote_id: uuid.UUID | None = None
    quote_number: str | None = None
    direction: PaymentDirection = PaymentDirection.INCOMING
    credit_note_id: uuid.UUID | None = None
    credit_note_number: str | None = None
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
