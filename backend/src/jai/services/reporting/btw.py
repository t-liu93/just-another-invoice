"""BTW (omzetbelasting) VAT return report service (M10 step 2).

Public API
----------
``compute_vat_return(session, company, year, quarter, tiers)``
    Compute the BTW quarterly return for the given company, year, and quarter.
    Returns a ``VatReturnReport``.

Design decisions (M10.md D-MAP, D-COUNTRY, D-1D, D-PCT, D-BOX5, D6)
----------------------------------------------------------------------
D1  Pure-read: only SELECT queries, no writes, no new tables.
D2  Basis = factuurstelsel (invoice date / expense date), quarterly.
D3  Revenue: status ∈ {SENT, COMPLETED}; Costs: is_draft = false.
D4  All amounts from ``base_*`` columns (EUR, exchange_rate = 1 in v1).
D6  Sum already-persisted to-the-cent amounts; never re-round aggregates.
    Exception: 4b self-assessed VAT (EU_B2B_REVERSE_PURCH) is computed
    fresh from ``base_net_amount × vat_rate_percent / 100`` and rounded
    to cents before accumulation (D-MAP-IMPL).

NL ruleset
----------
The entire D-MAP table is implemented as pure Python functions that
accept row-level snapshots and return box contributions.  The ruleset is
selected by ``company.country_code``; only ``"NL"`` is implemented in v1.
Non-NL companies fall back to the NL ruleset and receive a warning
(D-COUNTRY).

Rate tier matching
------------------
Boxes 1a / 1b / 1e are selected by comparing ``vat_rate_percent`` against
the configured tier thresholds (``VatRateTiers``):
  - rate == hoog → 1a
  - rate == laag → 1b
  - rate == zero → 1e
  - non-zero, not hoog/laag → 1c
For APPLY_RATE effects only; zero-rate effects (ZERO_REVERSE, ZERO_EXPORT)
bypass tier matching and route directly to 3a / 3b / 1e.

Private-use correction (1d, D-1D)
----------------------------------
1d is populated **only** when ``is_last_period_of_year`` is True (Q4).
The value is the sum over **the entire calendar year** (Jan–Dec) of
    Σ (deductible expenses) [ base_vat_amount × (1 − business_percentage/100) ]
This represents the private-use portion of VAT that was fully deducted
throughout the year, now corrected at year-end.

Disclaimer (D-DISCLAIMER)
--------------------------
A fixed English disclaimer string is returned in every response.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import InvoiceStatus
from jai.models.expense import Expense
from jai.models.invoice import Invoice
from jai.schemas.report import (
    VatBoxBaseOnly,
    VatBoxBaseVat,
    VatBoxVatOnly,
    VatReturnBoxes,
    VatReturnReport,
    VatReturnTotals,
)
from jai.schemas.setting import VatRateTiers
from jai.services.money import quantize_to_minor_unit

if TYPE_CHECKING:
    from jai.models.company import Company

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ZERO = Decimal("0")

_DISCLAIMER = (
    "This output is for bookkeeping assistance only and does not constitute "
    "tax or accounting advice. Always verify with your accountant and/or the "
    "Belastingdienst before filing your VAT return. "
    "This open-source project accepts no liability for the accuracy of the figures."
)

# VAT treatment effect codes (snapshot values stored on Invoice / Expense).
_EFFECT_APPLY_RATE = "APPLY_RATE"
_EFFECT_ZERO_REVERSE = "ZERO_REVERSE"
_EFFECT_ZERO_EXPORT = "ZERO_EXPORT"


# ---------------------------------------------------------------------------
# Quarter date helpers
# ---------------------------------------------------------------------------


def _quarter_date_range(year: int, quarter: int) -> tuple[date, date]:
    """Return the inclusive [from, to] date range for the given quarter."""
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    # End day: last day of end_month.
    import calendar
    end_day = calendar.monthrange(year, end_month)[1]
    return date(year, start_month, 1), date(year, end_month, end_day)


# ---------------------------------------------------------------------------
# Tier resolution
# ---------------------------------------------------------------------------


def _classify_rate(rate_percent: Decimal, tiers: VatRateTiers) -> str:
    """Return the tier label ('hoog', 'laag', 'zero', 'other') for a rate value."""
    rate_int = int(rate_percent)  # tier thresholds are integers; rate must match exactly
    if rate_int == tiers.hoog:
        return "hoog"
    if rate_int == tiers.laag:
        return "laag"
    if rate_int == tiers.zero:
        return "zero"
    return "other"


# ---------------------------------------------------------------------------
# Accumulator dataclass (mutable, internal)
# ---------------------------------------------------------------------------


@dataclass
class _BoxAccumulator:
    """Mutable accumulator for BTW box values.  Sums are never re-rounded (D6)."""

    # 1a – hoog tarief
    b1a_base: Decimal = field(default_factory=lambda: _ZERO)
    b1a_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 1b – laag tarief
    b1b_base: Decimal = field(default_factory=lambda: _ZERO)
    b1b_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 1c – other rates (non-zero, non-hoog, non-laag)
    b1c_base: Decimal = field(default_factory=lambda: _ZERO)
    b1c_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 1d – privégebruik (only populated in Q4)
    b1d_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 1e – 0% / not-taxed-at-you (base only)
    b1e_base: Decimal = field(default_factory=lambda: _ZERO)
    # 2a – domestic reverse-charge (recipient side); v1 always 0
    b2a_base: Decimal = field(default_factory=lambda: _ZERO)
    b2a_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 3a – export non-EU (base only)
    b3a_base: Decimal = field(default_factory=lambda: _ZERO)
    # 3b – ICP (EU B2B reverse-charge / intracommunautaire levering, base only)
    b3b_base: Decimal = field(default_factory=lambda: _ZERO)
    # 3c – distance / installation sales EU; v1 always 0
    b3c_base: Decimal = field(default_factory=lambda: _ZERO)
    # 4a – non-EU import (art.23); v1 always 0
    b4a_base: Decimal = field(default_factory=lambda: _ZERO)
    b4a_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 4b – EU intra-community acquisition self-assessment
    b4b_base: Decimal = field(default_factory=lambda: _ZERO)
    b4b_vat: Decimal = field(default_factory=lambda: _ZERO)
    # 5b – input VAT (voorbelasting)
    b5b_vat: Decimal = field(default_factory=lambda: _ZERO)


# ---------------------------------------------------------------------------
# NL ruleset: invoice line-level contribution
# ---------------------------------------------------------------------------


def _apply_invoice_line_nl(
    acc: _BoxAccumulator,
    treatment_effect: str,
    requires_icp: bool,
    treatment_code: str,
    vat_rate_percent: Decimal,
    taxable_base: Decimal,
    vat_amount: Decimal,
    tiers: VatRateTiers,
) -> None:
    """Apply a single invoice VAT line to the accumulator (NL ruleset).

    Parameters
    ----------
    acc:
        Mutable accumulator to update.
    treatment_effect:
        The ``vat_treatment_effect`` snapshot: APPLY_RATE | ZERO_REVERSE | ZERO_EXPORT.
    requires_icp:
        Whether this treatment triggers an ICP (Intrastat) obligation.
    treatment_code:
        The ``vat_treatment_code`` snapshot (used for EU_B2C routing).
    vat_rate_percent:
        The ``vat_rate_percent`` snapshot from InvoiceLine or InvoiceTax.
    taxable_base:
        The ``taxable_amount`` snapshot (already to the cent, D6).
    vat_amount:
        The ``vat_total`` / ``tax_amount`` snapshot (already to the cent, D6).
    tiers:
        VatRateTiers configuration (hoog/laag/zero thresholds).
    """
    if treatment_effect == _EFFECT_ZERO_EXPORT:
        # D-MAP row 5: EXPORT_NON_EU → 3a (base only)
        acc.b3a_base += taxable_base
        return

    if treatment_effect == _EFFECT_ZERO_REVERSE and requires_icp:
        # D-MAP row 6: EU_B2B_REVERSE → 3b (base only, ICP)
        acc.b3b_base += taxable_base
        return

    if treatment_effect == _EFFECT_ZERO_REVERSE and not requires_icp:
        # Catch-all: any other zero-reverse (e.g. domestic 0%-rated services
        # flagged ZERO_REVERSE without ICP).  Route to 1e (base only) as a
        # safe fallback.
        acc.b1e_base += taxable_base
        return

    if treatment_effect == _EFFECT_APPLY_RATE:
        # D-MAP rows 1–4: NL_DOMESTIC and EU_B2C both go through tier matching.
        tier = _classify_rate(vat_rate_percent, tiers)
        if tier == "hoog":
            acc.b1a_base += taxable_base
            acc.b1a_vat += vat_amount
        elif tier == "laag":
            acc.b1b_base += taxable_base
            acc.b1b_vat += vat_amount
        elif tier == "zero":
            # APPLY_RATE at 0% (e.g. NL_DOMESTIC with 0% rate): → 1e
            acc.b1e_base += taxable_base
        else:
            # Non-hoog, non-laag, non-zero → 1c
            acc.b1c_base += taxable_base
            acc.b1c_vat += vat_amount
        return

    # Unknown effect: silently ignore (future-proofing).
    return


# ---------------------------------------------------------------------------
# NL ruleset: expense-level contribution
# ---------------------------------------------------------------------------


def _apply_expense_nl(
    acc: _BoxAccumulator,
    treatment_effect: str,
    treatment_code: str,
    vat_rate_percent: Decimal,
    base_net_amount: Decimal,
    base_vat_amount: Decimal,
    deductible: bool,
) -> None:
    """Apply a single expense to the accumulator (NL ruleset).

    Parameters
    ----------
    acc:
        Mutable accumulator to update.
    treatment_effect:
        The ``vat_treatment_effect`` snapshot.
    treatment_code:
        The ``vat_treatment_code`` snapshot (used to identify EU_B2C_PURCH).
    vat_rate_percent:
        The ``vat_rate_percent`` snapshot.
    base_net_amount:
        The ``base_net_amount`` column (EUR, already to-the-cent).
    base_vat_amount:
        The ``base_vat_amount`` column (EUR, already to-the-cent).
    deductible:
        The ``deductible`` flag on the expense.
    """
    # D-MAP row 10: EU_B2C_PURCH (consumer-side EU foreign VAT) → no NL box.
    # Official: "in andere EU-landen betaalde btw mag u NIET aftrekken in uw
    # Nederlandse btw-aangifte" (guide §5b, Let op!).
    if treatment_code == "EU_B2C_PURCH":
        return

    # D-MAP row 9: IMPORT_NON_EU → v1 not implemented.
    if treatment_code == "IMPORT_NON_EU":
        return

    if treatment_effect == _EFFECT_APPLY_RATE:
        # D-MAP row 7: NL_DOMESTIC_PURCH → 5b (full VAT, if deductible; no business% here)
        if deductible:
            acc.b5b_vat += base_vat_amount
        return

    if treatment_effect == _EFFECT_ZERO_REVERSE:
        # D-MAP row 8: EU_B2B_REVERSE_PURCH → 4b (self-assessed) + 5b (same amount, if deductible)
        # Self-assessed VAT = quantize_to_minor_unit(base_net × rate / 100)  (D-MAP-IMPL)
        self_assessed_vat = quantize_to_minor_unit(
            base_net_amount * vat_rate_percent / Decimal("100")
        )
        acc.b4b_base += base_net_amount
        acc.b4b_vat += self_assessed_vat
        if deductible:
            acc.b5b_vat += self_assessed_vat
        return

    # Fallback: unknown zero-rate expense – ignore.
    return


# ---------------------------------------------------------------------------
# 1d: private-use correction (D-1D)
# ---------------------------------------------------------------------------


def _compute_1d_vat(expenses: list[Expense]) -> Decimal:
    """Compute the 1d private-use correction for the full year.

    Only deductible expenses contribute; business_percentage = 100 contributes 0.

    1d = Σ (deductible expenses) [ base_vat_amount × (1 − business_percentage / 100) ]

    Each per-expense amount is rounded to cents before summing (D6 / D-MAP-IMPL).
    """
    total = _ZERO
    for exp in expenses:
        if not exp.deductible:
            continue
        biz_pct = Decimal(str(exp.business_percentage))
        if biz_pct >= Decimal("100"):
            continue  # 100% business → zero private-use correction
        private_fraction = Decimal("1") - biz_pct / Decimal("100")
        contribution = quantize_to_minor_unit(
            Decimal(str(exp.base_vat_amount)) * private_fraction
        )
        total += contribution
    return total


# ---------------------------------------------------------------------------
# Main async orchestration
# ---------------------------------------------------------------------------


async def compute_vat_return(
    session: AsyncSession,
    company: Company,
    year: int,
    quarter: int,
    tiers: VatRateTiers,
) -> VatReturnReport:
    """Compute the BTW VAT return for *company* for *year* Q*quarter*.

    Parameters
    ----------
    session:
        Read-only async SQLAlchemy session.
    company:
        Company ORM object (needs ``id`` and ``country_code``).
    year:
        Calendar year (e.g. 2026).
    quarter:
        Quarter 1–4.
    tiers:
        VatRateTiers with hoog/laag/zero thresholds.

    Returns
    -------
    VatReturnReport
        Complete VAT return with boxes, totals, warnings, and disclaimer.
    """
    date_from, date_to = _quarter_date_range(year, quarter)
    is_last_period = quarter == 4
    company_id: uuid.UUID = company.id

    warnings: list[str] = []

    # D-COUNTRY: only NL is implemented; all others fall back to NL + warning.
    country_code = getattr(company, "country_code", None) or ""
    if country_code.upper() != "NL":
        warnings.append(
            f"Company country code is '{country_code}' (not 'NL'). "
            "Only the Dutch (NL) BTW ruleset is currently implemented. "
            "The NL ruleset has been applied as a fallback. "
            "Please verify the figures with a local tax advisor."
        )

    acc = _BoxAccumulator()

    # -----------------------------------------------------------------------
    # 1. Revenue: invoices with status SENT or COMPLETED in [date_from, date_to]
    # -----------------------------------------------------------------------
    revenue_statuses = [InvoiceStatus.SENT, InvoiceStatus.COMPLETED]
    stmt_inv = select(Invoice).where(
        and_(
            Invoice.company_id == company_id,
            Invoice.status.in_(revenue_statuses),
            Invoice.invoice_date >= date_from,
            Invoice.invoice_date <= date_to,
        )
    )
    result_inv = await session.execute(stmt_inv)
    invoices = result_inv.scalars().all()

    # Track EU B2B invoices that need ICP but have no customer vat_id.
    icp_invoices_missing_vat_id: list[str] = []

    for inv in invoices:
        effect = inv.vat_treatment_effect
        requires_icp = inv.vat_treatment_requires_icp
        treatment_code = inv.vat_treatment_code

        if inv.tax_mode.value == "DOCUMENT":
            # DOCUMENT mode: aggregated per tax rate via InvoiceTax rows.
            taxes = inv.taxes  # lazy="selectin" → already loaded
            for tax in taxes:
                rate_pct = Decimal(str(tax.vat_rate_percent))
                taxable = quantize_to_minor_unit(Decimal(str(tax.taxable_amount)))
                vat_amt = quantize_to_minor_unit(Decimal(str(tax.tax_amount)))
                _apply_invoice_line_nl(
                    acc,
                    effect,
                    requires_icp,
                    treatment_code,
                    rate_pct,
                    taxable,
                    vat_amt,
                    tiers,
                )
        else:
            # LINE mode: per-line VAT via InvoiceLineTax rows.
            for line in inv.lines:
                for line_tax in line.line_taxes:
                    rate_pct = Decimal(str(line_tax.vat_rate_percent))
                    taxable = quantize_to_minor_unit(Decimal(str(line_tax.taxable_amount)))
                    vat_amt = quantize_to_minor_unit(Decimal(str(line_tax.tax_amount)))
                    _apply_invoice_line_nl(
                        acc,
                        effect,
                        requires_icp,
                        treatment_code,
                        rate_pct,
                        taxable,
                        vat_amt,
                        tiers,
                    )

        # D5 / warning: EU B2B invoices going to 3b require a customer vat_id.
        if requires_icp and effect == _EFFECT_ZERO_REVERSE:
            from jai.models.customer import Customer  # avoid circular at module level

            stmt_cust = select(Customer).where(Customer.id == inv.customer_id)
            cust_result = await session.execute(stmt_cust)
            customer = cust_result.scalar_one_or_none()
            if customer is None or not customer.vat_id:
                icp_invoices_missing_vat_id.append(str(inv.invoice_number))

    if icp_invoices_missing_vat_id:
        missing_str = ", ".join(icp_invoices_missing_vat_id[:10])
        extra = (
            f" (and {len(icp_invoices_missing_vat_id) - 10} more)"
            if len(icp_invoices_missing_vat_id) > 10
            else ""
        )
        warnings.append(
            f"The following EU B2B (ICP) invoices are missing a customer VAT ID, "
            f"which is required for the Opgaaf ICP declaration: {missing_str}{extra}."
        )

    # -----------------------------------------------------------------------
    # 2. Expenses: confirmed expenses in [date_from, date_to]
    # -----------------------------------------------------------------------
    stmt_exp = select(Expense).where(
        and_(
            Expense.company_id == company_id,
            Expense.is_draft.is_(False),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
    )
    result_exp = await session.execute(stmt_exp)
    expenses_in_period = result_exp.scalars().all()

    for exp in expenses_in_period:
        _apply_expense_nl(
            acc,
            exp.vat_treatment_effect,
            exp.vat_treatment_code,
            Decimal(str(exp.vat_rate_percent)),
            quantize_to_minor_unit(Decimal(str(exp.base_net_amount))),
            quantize_to_minor_unit(Decimal(str(exp.base_vat_amount))),
            exp.deductible,
        )

    # -----------------------------------------------------------------------
    # 3. 1d – private-use correction (D-1D): only in Q4, based on full year
    # -----------------------------------------------------------------------
    if is_last_period:
        # Fetch all confirmed deductible expenses for the full calendar year.
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        stmt_year_exp = select(Expense).where(
            and_(
                Expense.company_id == company_id,
                Expense.is_draft.is_(False),
                Expense.deductible.is_(True),
                Expense.expense_date >= year_start,
                Expense.expense_date <= year_end,
            )
        )
        result_year_exp = await session.execute(stmt_year_exp)
        year_expenses = list(result_year_exp.scalars().all())
        acc.b1d_vat = _compute_1d_vat(year_expenses)

    # -----------------------------------------------------------------------
    # 4. Build schema objects from accumulator
    # -----------------------------------------------------------------------
    boxes = VatReturnBoxes(
        box_1a=VatBoxBaseVat(base=acc.b1a_base, vat=acc.b1a_vat),
        box_1b=VatBoxBaseVat(base=acc.b1b_base, vat=acc.b1b_vat),
        box_1c=VatBoxBaseVat(base=acc.b1c_base, vat=acc.b1c_vat),
        box_1d=VatBoxVatOnly(vat=acc.b1d_vat),
        box_1e=VatBoxBaseOnly(base=acc.b1e_base),
        box_2a=VatBoxBaseVat(base=acc.b2a_base, vat=acc.b2a_vat),
        box_3a=VatBoxBaseOnly(base=acc.b3a_base),
        box_3b=VatBoxBaseOnly(base=acc.b3b_base),
        box_3c=VatBoxBaseOnly(base=acc.b3c_base),
        box_4a=VatBoxBaseVat(base=acc.b4a_base, vat=acc.b4a_vat),
        box_4b=VatBoxBaseVat(base=acc.b4b_base, vat=acc.b4b_vat),
        box_5b=VatBoxVatOnly(vat=acc.b5b_vat),
    )

    # D-BOX5: output_vat_total = 1a+1b+1c VAT + 1d + 2a+4a+4b self-assessed VAT
    output_vat_total = (
        acc.b1a_vat
        + acc.b1b_vat
        + acc.b1c_vat
        + acc.b1d_vat
        + acc.b2a_vat
        + acc.b4a_vat
        + acc.b4b_vat
    )
    net_payable_or_refundable = output_vat_total - acc.b5b_vat

    totals = VatReturnTotals(
        output_vat_total=VatBoxVatOnly(vat=output_vat_total),
        net_payable_or_refundable=VatBoxVatOnly(vat=net_payable_or_refundable),
    )

    return VatReturnReport(
        year=year,
        quarter=quarter,
        date_from=date_from,
        date_to=date_to,
        is_last_period_of_year=is_last_period,
        boxes=boxes,
        totals=totals,
        warnings=warnings,
        disclaimer=_DISCLAIMER,
    )
