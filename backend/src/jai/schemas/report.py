"""Pydantic schemas for the M10 reporting module.

ProfitLossSeriesItem   – one time-bucket (month or quarter) in the P/L series.
ProfitLossReport       – full P/L report response.
VatReturnReport        – BTW/VAT return summary (step 2, NL ruleset).

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


# ---------------------------------------------------------------------------
# BTW / VAT return report (step 2)
# ---------------------------------------------------------------------------


class VatBoxBaseVat(BaseModel):
    """A VAT return box with both taxable base and VAT amount.

    Used for boxes 1a, 1b, 1c, 2a, 4a, 4b.
    """

    base: Decimal = Field(description="Taxable base amount (net, EUR).")
    vat: Decimal = Field(description="VAT / BTW amount (EUR).")


class VatBoxVatOnly(BaseModel):
    """A VAT return box that holds only a VAT amount (no base column).

    Used for box 1d (privégebruik, BTW only) and 5b (voorbelasting).
    """

    vat: Decimal = Field(description="VAT / BTW amount (EUR).")


class VatBoxBaseOnly(BaseModel):
    """A VAT return box that holds only a taxable base (no VAT column).

    Used for boxes 1e, 3a, 3b, 3c (0% / ICP / export – no BTW due).
    """

    base: Decimal = Field(description="Taxable base amount (net, EUR).")


class VatReturnBoxes(BaseModel):
    """All BTW return boxes as defined by the NL Belastingdienst form.

    Boxes with ``base + vat``: 1a, 1b, 1c, 2a, 4a, 4b.
    Boxes with ``vat only``: 1d, 5b.
    Boxes with ``base only``: 1e, 3a, 3b, 3c.

    v1 boxes that are always zero but kept for schema completeness:
    1c (other rates), 2a (domestic reverse-charge), 3c (distance selling), 4a (non-EU import).
    """

    box_1a: VatBoxBaseVat = Field(description="Domestic / EU-B2C hoog-tarief (21%) supplies.")
    box_1b: VatBoxBaseVat = Field(description="Domestic / EU-B2C laag-tarief (9%) supplies.")
    box_1c: VatBoxBaseVat = Field(description="Other rates (e.g. 13% forfait); v1 always 0.")
    box_1d: VatBoxVatOnly = Field(
        description="Privégebruik (private-use correction); only non-zero in Q4 (year-end)."
    )
    box_1e: VatBoxBaseOnly = Field(
        description="0% or reverse-charge supplier-side supplies (net only)."
    )
    box_2a: VatBoxBaseVat = Field(
        description="Domestic reverse-charge (recipient side); v1 always 0."
    )
    box_3a: VatBoxBaseOnly = Field(description="Exports to non-EU countries (net only).")
    box_3b: VatBoxBaseOnly = Field(description="ICP (EU B2B reverse-charge) supplies (net only).")
    box_3c: VatBoxBaseOnly = Field(description="Distance / installation sales EU; v1 always 0.")
    box_4a: VatBoxBaseVat = Field(
        description="Non-EU import self-assessment (art.23); v1 always 0."
    )
    box_4b: VatBoxBaseVat = Field(description="EU intra-community acquisition self-assessment.")
    box_5b: VatBoxVatOnly = Field(description="Input VAT (voorbelasting) deductible.")


class VatReturnTotals(BaseModel):
    """Derived / auxiliary totals (not official box numbers, for display only).

    These mirror the numbers the tax authority's form program computes
    automatically.  Named after the Dutch official wording, not traditional
    5a / 5c labels (see D-BOX5 in M10.md).
    """

    output_vat_total: VatBoxVatOnly = Field(
        description=(
            "Sum of all output VAT (≈ traditional '5a').  "
            "= 1a.vat + 1b.vat + 1c.vat + 1d.vat + 2a.vat + 4a.vat + 4b.vat"
        )
    )
    net_payable_or_refundable: VatBoxVatOnly = Field(
        description=(
            "Net VAT payable (+) or refundable (−) "
            "(Totaal te betalen / terug te vragen; ≈ traditional '5c').  "
            "= output_vat_total.vat − 5b.vat"
        )
    )


class VatReturnReport(BaseModel):
    """Response schema for GET /api/v1/reports/vat-return.

    The ``from`` / ``to`` fields use serialisation aliases so the JSON keys
    match the query-parameter convention used by the P/L report.
    """

    model_config = {"populate_by_name": True}

    year: int = Field(description="Calendar year of the VAT return period.")
    quarter: int = Field(description="Quarter of the VAT return period (1–4).")
    date_from: date = Field(serialization_alias="from", description="First day of the quarter.")
    date_to: date = Field(serialization_alias="to", description="Last day of the quarter.")
    is_last_period_of_year: bool = Field(
        description="True when quarter == 4; triggers 1d private-use calculation."
    )
    boxes: VatReturnBoxes = Field(description="Individual BTW return boxes.")
    totals: VatReturnTotals = Field(description="Derived totals (not official box numbers).")
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Advisory messages, e.g. missing VAT-ID on ICP customers, "
            "or non-NL company using the NL ruleset as fallback."
        ),
    )
    disclaimer: str = Field(
        description=(
            "Fixed disclaimer: this output is for bookkeeping assistance only; "
            "not tax or accounting advice.  Verify with your accountant / tax authority."
        )
    )
