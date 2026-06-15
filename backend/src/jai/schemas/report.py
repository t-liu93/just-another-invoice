"""Pydantic schemas for the M10 reporting module.

ProfitLossSeriesItem   – one time-bucket (month or quarter) in the P/L series.
ProfitLossReport       – full P/L report response.

Schema layer never computes amounts (red-line 1).  All monetary fields are
``Decimal`` serialised as strings, matching the established money schema
convention (see payment.py).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ProfitLossSeriesItem(BaseModel):
    """One time-bucket in the P/L time series.

    ``period`` is an ISO date string representing the start of the bucket:
    - month granularity  → ``YYYY-MM-01``
    - quarter granularity → ``YYYY-01-01`` / ``YYYY-04-01`` / ``YYYY-07-01`` / ``YYYY-10-01``
    """

    period: str = Field(description="Bucket start date in YYYY-MM-DD format.")
    revenue_net: Decimal = Field(description="Net taxable revenue in this period (EUR).")
    expense_actual: Decimal = Field(
        description="Depreciation-adjusted, business%-prorated expense cost in this period (EUR)."
    )
    profit: Decimal = Field(description="revenue_net − expense_actual for this period.")


# ---------------------------------------------------------------------------
# Top-level report
# ---------------------------------------------------------------------------


class ProfitLossReport(BaseModel):
    """Response schema for GET /api/v1/reports/profit-loss.

    Monetary fields are Decimal (serialised as strings by FastAPI/Pydantic v2
    when ``model_config`` sets ``json_encoders`` or when callers use
    ``model.model_dump(mode='json')``).

    The ``from`` / ``to`` query parameter names are Python reserved words so
    internally they are stored as ``date_from`` / ``date_to`` but serialised
    (and documented in OpenAPI) as ``from`` / ``to``.
    """

    model_config = {"populate_by_name": True}

    date_from: date = Field(serialization_alias="from")
    date_to: date = Field(serialization_alias="to")
    granularity: str = Field(description="'month' or 'quarter'.")
    revenue_net: Decimal = Field(
        description="Total net taxable revenue across the full date range (EUR)."
    )
    expense_actual: Decimal = Field(
        description="Total depreciation-adjusted, business%-prorated expense cost (EUR)."
    )
    profit: Decimal = Field(description="revenue_net − expense_actual across the full range.")
    series: list[ProfitLossSeriesItem] = Field(
        description="Time-bucketed breakdown by month or quarter."
    )
    by_category: None = Field(
        default=None,
        description="Reserved for future per-category breakdown (step 1: always null).",
    )
