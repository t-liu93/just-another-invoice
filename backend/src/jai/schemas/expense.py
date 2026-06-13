"""Pydantic schemas for expense (M8 steps 1 + 2).

ExpenseInput               – request body (raw user input only; no gross/base_*/snapshots).
ExpenseRead                – full expense as returned by read endpoints.
ExpenseListItem            – minimal overview row for the expenses list.
ExpenseListResponse        – paginated expenses list.
ExpenseAttachmentRead      – single attachment record (no storage_key / disk path exposed).
ExpenseAttachmentListResponse – list of attachments for one expense.

Schema layer never computes amounts (red-line 1).  All monetary fields are
``Decimal``.  Text fields use plain ``str`` (red-line 10).
``storage_key`` and disk paths are intentionally absent from all read schemas.
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

    # Recurring expense backlink (step 3: FK → recurring_expense.id, SET NULL on delete)
    recurring_expense_id: uuid.UUID | None = None

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


# ---------------------------------------------------------------------------
# Attachment read schemas (step 2)
# ---------------------------------------------------------------------------


class ExpenseAttachmentRead(BaseModel):
    """Single attachment as returned by the API.

    ``storage_key`` and disk paths are intentionally absent (red-line 7 /
    M8 D2): clients must not know where files are stored on disk.
    """

    id: uuid.UUID
    expense_id: uuid.UUID
    filename: str | None = None
    mime_type: str
    byte_size: int
    created_at: datetime


class ExpenseAttachmentListResponse(BaseModel):
    """List of attachments for a single expense."""

    items: list[ExpenseAttachmentRead]


# ---------------------------------------------------------------------------
# AI pre-fill response (step 4)
# ---------------------------------------------------------------------------


class ExpenseAIPrefill(BaseModel):
    """Fields pre-filled by the AI receipt extraction pipeline (D5).

    All fields are optional – the model may not be able to extract every
    value, and the caller should treat missing fields as "needs manual input".
    This schema intentionally contains **no credentials** and is **not
    persisted** – it is returned directly to the frontend so the user can
    review and confirm before saving a proper expense record.

    ``suggested_category_name`` is the raw text returned by the model.
    ``suggested_category_id`` is populated by the service when the name can be
    matched (case-insensitive, best-effort) against the company's expense
    categories.

    VAT treatment is intentionally absent: cross-border treatment inference
    is unreliable, so the user always selects it manually (D15 / M8 AI pipeline
    note).
    """

    expense_date: date | None = None
    supplier_name: str | None = None
    net_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    vat_rate_percent: Decimal | None = None
    suggested_category_name: str | None = None
    suggested_category_id: uuid.UUID | None = None
    raw_model_note: str | None = None
    confidence: str | None = None
