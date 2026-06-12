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


class PaymentRead(BaseModel):
    """Full payment record as returned by read endpoints."""

    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str
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


# ---------------------------------------------------------------------------
# Aggregate response
# ---------------------------------------------------------------------------


class InvoicePaymentsResponse(BaseModel):
    """Aggregate: invoice payment state + ordered list of payment records.

    Returned by POST/GET /api/v1/invoices/{id}/payments.
    ``items`` is ordered by (payment_date ASC, created_at ASC).
    """

    invoice_id: uuid.UUID
    invoice_number: str
    total_incl_vat: Decimal
    base_total_incl_vat: Decimal
    paid_total: Decimal
    base_paid_total: Decimal
    due_amount: Decimal
    base_due_amount: Decimal
    paid_status: InvoicePaidStatus
    status: InvoiceStatus
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
    invoice_id: uuid.UUID
    invoice_number: str
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
