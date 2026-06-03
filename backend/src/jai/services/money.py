"""Money / Decimal arithmetic helpers.

Convention (agreed in M0, to be refined in M5):
- All monetary values are stored as ``NUMERIC(18, 3)`` in PostgreSQL.
- ``quantize_money()`` applies **ROUND_HALF_UP** with 3 decimal places.
- M0 provides the tooling; the per-line vs per-total rounding strategy
  (i.e. *when* to call ``quantize_money`` in a pricing pipeline) will be
  locked down in M5 when the pricing engine is built.

Rounding rule recap – ``ROUND_HALF_UP`` (a.k.a. commercial rounding):
    1.2345 → 1.235
    1.2344 → 1.234
   -1.2345 → -1.235
   -1.2344 → -1.234
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# -- Constants ----------------------------------------------------------------

#: Number of decimal places for all monetary values.
MONEY_SCALE = 3

#: The quantizer used for rounding: ``Decimal("0.001")``.
MONEY_QUANT = Decimal("0.001")

#: Rounding mode – commercial rounding (half-up).
MONEY_ROUNDING = ROUND_HALF_UP


# -- Public helpers -----------------------------------------------------------

def quantize_money(value: Decimal) -> Decimal:
    """Round *value* to 3 decimal places using ``ROUND_HALF_UP``.

    Parameters
    ----------
    value:
        Any ``Decimal`` amount.  If it has more than 3 fractional digits
        the excess digits are rounded using commercial rounding.

    Returns
    -------
    Decimal
        The value quantised to ``MONEY_SCALE`` (3) decimal places.

    Examples
    --------
    >>> quantize_money(Decimal("1.2345"))
    Decimal('1.235')
    >>> quantize_money(Decimal("1.2344"))
    Decimal('1.234')
    >>> quantize_money(Decimal("-1.2345"))
    Decimal('-1.235')
    """
    return value.quantize(MONEY_QUANT, rounding=MONEY_ROUNDING)


def add_money(a: Decimal, b: Decimal) -> Decimal:
    """Add two monetary values and quantise the result."""
    return quantize_money(a + b)


def sub_money(a: Decimal, b: Decimal) -> Decimal:
    """Subtract two monetary values and quantise the result."""
    return quantize_money(a - b)


def mul_money(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """Multiply quantity by unit price and quantise.

    This is the standard "line subtotal" operation.
    """
    return quantize_money(quantity * unit_price)


def is_money_zero(value: Decimal) -> bool:
    """Check whether a monetary value is zero (after quantisation)."""
    return quantize_money(value) == Decimal("0.000")
