"""Pydantic schemas for quote (M6).

Step 1 schemas (pricing preview):
  QuoteCalculationRequest – mirrors InvoiceCalculationRequest with quote_date
  QuoteCalculationRead    – proper Pydantic subclass (identical fields, own OpenAPI schema)

Line/discount inputs reuse invoice schema types directly (same model, red-line 1).
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field

from jai.models._enums import InvoiceTaxMode
from jai.schemas.invoice import (
    DiscountInput,
    InvoiceCalculationRead,
    InvoiceLineInput,
)

# ---------------------------------------------------------------------------
# Calculation request (step 1)
# ---------------------------------------------------------------------------


class QuoteCalculationRequest(BaseModel):
    """Request body for ``POST /api/v1/quotes/calculate``."""

    customer_id: uuid.UUID
    quote_date: date
    valid_until: date | None = None
    currency: str | None = Field(
        default=None,
        max_length=3,
        min_length=3,
        description="ISO 4217. Must equal company base_currency in M6.",
    )
    tax_mode: InvoiceTaxMode
    amounts_include_vat: bool = False
    vat_treatment_id: uuid.UUID | None = None
    document_vat_rate_id: uuid.UUID | None = None
    discount: DiscountInput = DiscountInput()
    lines: list[InvoiceLineInput] = Field(min_length=1, description="At least 1 line required.")


# ---------------------------------------------------------------------------
# Calculation response (step 1)
# ---------------------------------------------------------------------------


class QuoteCalculationRead(InvoiceCalculationRead):
    """Quote pricing preview result.

    Identical field structure to ``InvoiceCalculationRead``; defined as its own
    Pydantic model so FastAPI emits a distinct ``QuoteCalculationRead`` component
    in OpenAPI and ``schema.d.ts`` carries the quote-specific name.  Future steps
    can add quote-only fields here without touching the invoice schema.
    """


# Re-export shared input types for consumers that import from schemas.quote
__all__ = [
    "DiscountInput",
    "InvoiceLineInput",
    "QuoteCalculationRead",
    "QuoteCalculationRequest",
]
