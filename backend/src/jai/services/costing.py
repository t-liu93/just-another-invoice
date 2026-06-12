"""Costing engine -- authoritative cost-to-sell-price calculation for estimates (M6.5 step 1).

Public API
----------
- ``compute_estimate`` -- pure calculation core (no DB, testable in isolation)

Design notes
------------
All monetary arithmetic uses ``Decimal`` with ``quantize_money()`` (3 dp,
``ROUND_HALF_UP``).  The engine computes:

1. Per-line: line_total = quantize(unit_cost_excl_vat * quantity)
2. Per-line: margin_amount = quantize(line_total * margin_rate)
3. Per-line: line_sell_excl_vat = line_total + margin_amount
4. Rolling: total_margin = sum(margin_amount)
5. Rolling: total_excl_vat = sum(line_sell_excl_vat)
6. Per-group: group_sell_excl_vat = sum(group lines' line_sell_excl_vat)
7. Indicative: total_incl_vat_indicative = quantize(total_excl_vat * (1 + pct/100))

Rounding: each line_total and margin_amount are quantised individually.
Rolling totals are sums of already-quantised values (no re-quantisation).
This matches the M5 pricing engine convention.

Overflow guard
--------------
All amounts that are persisted to ``NUMERIC(18,3)`` columns are checked
against ``_MONEY_MAX`` after calculation.  Inputs can individually satisfy
the schema bounds yet produce computed values that exceed the DB column
(e.g. unit_cost=99999999999.999 × quantity=10001 > 999999999999999.999).
``decimal.InvalidOperation`` from ``quantize_money()`` (Python default
context: 28 significant digits) is also surfaced as ``ValueError`` so both
error kinds map to HTTP 422 at the API layer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from jai.schemas.estimate import (
    EstimateCalculationRead,
    EstimateCalculationRequest,
    EstimateGroupCalculationRead,
    EstimateLineCalculationRead,
)
from jai.services.money import quantize_money

# Upper bound for NUMERIC(18,3): 15 integer digits + 3 decimal places.
_MONEY_MAX = Decimal("999999999999999.999")


def _check_money(v: Decimal, label: str) -> None:
    """Raise ValueError if *v* exceeds the DB NUMERIC(18,3) upper bound."""
    if v > _MONEY_MAX:
        raise ValueError(
            f"Computed {label} ({v}) exceeds the maximum storable amount "
            f"({_MONEY_MAX}).  Reduce quantity, unit cost, or margin rate."
        )


def compute_estimate(
    request: EstimateCalculationRequest,
    standard_vat_percent: Decimal | None = None,
) -> EstimateCalculationRead:
    """Pure calculation core -- no DB access, no persistence.

    Parameters
    ----------
    request:
        Estimate calculation request with lines and groups.
    standard_vat_percent:
        The company's standard VAT rate percent for indicative incl. VAT calc.
        If ``None``, indicative VAT fields are omitted (``null``).

    Returns
    -------
    ``EstimateCalculationRead`` with all amounts quantised.

    Raises
    ------
    ValueError
        If any computed amount exceeds ``NUMERIC(18,3)`` or if arithmetic
        precision is exhausted (``decimal.InvalidOperation``).
    """
    try:
        return _compute_estimate_inner(request, standard_vat_percent)
    except InvalidOperation as exc:
        raise ValueError(
            f"Computed amount exceeds arithmetic precision: {exc}.  "
            "Reduce quantity, unit cost, or margin rate."
        ) from exc


def _compute_estimate_inner(
    request: EstimateCalculationRequest,
    standard_vat_percent: Decimal | None,
) -> EstimateCalculationRead:
    # Track group sell prices: ref -> accumulated sell
    group_sells: dict[str, Decimal] = {
        g.ref: Decimal("0.000") for g in request.groups
    }

    # Per-line calculation
    line_results: list[EstimateLineCalculationRead] = []
    total_margin = Decimal("0.000")
    total_excl_vat = Decimal("0.000")

    for i, line in enumerate(request.lines):
        line_total = quantize_money(line.unit_cost_excl_vat * line.quantity)
        _check_money(line_total, "line_total")

        margin_amount = quantize_money(line_total * line.margin_rate)
        _check_money(margin_amount, "margin_amount")

        line_sell = line_total + margin_amount
        _check_money(line_sell, "line_sell_excl_vat")

        total_margin += margin_amount
        total_excl_vat += line_sell

        # Add to group if group_ref matches a known group
        if line.group_ref is not None and line.group_ref in group_sells:
            group_sells[line.group_ref] += line_sell
            _check_money(group_sells[line.group_ref], "group_sell_excl_vat")

        line_results.append(
            EstimateLineCalculationRead(
                sort_order=i,
                line_total=line_total,
                margin_amount=margin_amount,
                line_sell_excl_vat=line_sell,
                group_ref=line.group_ref,
            )
        )

    _check_money(total_margin, "total_margin")
    _check_money(total_excl_vat, "total_excl_vat")

    # Per-group calculation (preserve input order)
    group_results = [
        EstimateGroupCalculationRead(
            ref=g.ref,
            group_sell_excl_vat=group_sells[g.ref],
        )
        for g in request.groups
    ]

    # Indicative VAT (informational, not authoritative; not persisted)
    total_incl_vat_indicative: Decimal | None = None
    indicative_vat_rate_percent: Decimal | None = None
    if standard_vat_percent is not None:
        indicative_vat_rate_percent = standard_vat_percent
        total_incl_vat_indicative = quantize_money(
            total_excl_vat * (Decimal("1") + standard_vat_percent / Decimal("100"))
        )

    return EstimateCalculationRead(
        lines=line_results,
        groups=group_results,
        total_margin=total_margin,
        total_excl_vat=total_excl_vat,
        total_incl_vat_indicative=total_incl_vat_indicative,
        indicative_vat_rate_percent=indicative_vat_rate_percent,
    )
