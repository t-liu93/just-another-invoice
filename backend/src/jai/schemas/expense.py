"""Pydantic schemas for expense (M8 step 1).

ExpenseInput        – request body (raw user input only; no gross/base_*/snapshots).
ExpenseRead         – full expense as returned by read endpoints.
ExpenseListItem     – minimal overview row for the expenses list.
ExpenseListResponse – paginated expenses list.

Schema layer never computes amounts (red-line 1).  All monetary fields are
``Decimal``.  Text fields use plain ``str`` (red-line 10).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator  # noqa: F401

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class ExpenseInput(BaseModel):
    """Request body for POST/PUT /api/v1/expenses.

    Only collects raw user input – net/vat amounts, date, category, treatment,
    rate, supplier, reference, note, deductible.  All computed fields
    (gross, base_*, snapshots, currency, exchange_rate, is_draft) are derived
    by the service layer (red-line 1).
    """

    expense_date: date
    category_id: uuid.UUID
    supplier_name: str | None = None
    vat_treatment_id: uuid.UUID
    vat_rate_id: uuid.UUID
    net_amount: Decimal = Field(ge=0, description="Net (excl. VAT) amount ≥ 0.")
    vat_amount: Decimal = Field(ge=0, description="VAT amount ≥ 0.")
    deductible: bool | None = Field(
        default=None,
        description=(
            "Whether this expense is VAT-deductible. "
            "Defaults to category.default_deductible, then True."
        ),
    )
    reference: str | None = None
    note: str | None = None

    @field_validator("net_amount", "vat_amount")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Amount must be ≥ 0.")
        return v


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class ExpenseRead(BaseModel):
    """Full expense record as returned by read endpoints."""

    id: uuid.UUID
    expense_date: date

    # Category (FK SET NULL + snapshot)
    category_id: uuid.UUID | None = None
    category_name: str | None = None

    supplier_name: str | None = None

    # VAT treatment (FK RESTRICT + snapshot)
    vat_treatment_id: uuid.UUID | None = None
    vat_treatment_code: str
    vat_treatment_label: str
    vat_treatment_effect: str

    # VAT rate (FK RESTRICT + snapshot)
    vat_rate_id: uuid.UUID | None = None
    vat_rate_percent: Decimal
    vat_rate_label: str

    # Amounts
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal

    # Deductibility
    deductible: bool

    # Currency (D10)
    currency: str
    exchange_rate: Decimal
    base_net_amount: Decimal
    base_vat_amount: Decimal
    base_gross_amount: Decimal

    # Optional fields
    reference: str | None = None
    note: str | None = None

    # Draft flag (D3)
    is_draft: bool

    # Attachment count (populated by service; step 2 will use real counts)
    attachment_count: int = 0

    # Timestamps
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# List item
# ---------------------------------------------------------------------------


class ExpenseListItem(BaseModel):
    """Minimal overview row for GET /api/v1/expenses."""

    id: uuid.UUID
    expense_date: date
    category_name: str | None = None
    supplier_name: str | None = None
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    deductible: bool
    is_draft: bool
    attachment_count: int = 0


# ---------------------------------------------------------------------------
# List response
# ---------------------------------------------------------------------------


class ExpenseListResponse(BaseModel):
    """Paginated expenses list."""

    items: list[ExpenseListItem]
    total: int
