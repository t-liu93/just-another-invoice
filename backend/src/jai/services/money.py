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

Two-tier rounding (introduced in M7.5):
- ``quantize_money()``        – 3-decimal intermediate precision, used
  throughout the pricing pipeline for mid-calculation amounts.
- ``quantize_to_minor_unit()`` – final client-facing / document-level
  precision, rounded to the currency's minor unit (EUR = 2 = "cents").
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

#: Number of decimal places for EUR's minor unit (cents).
#: v1 hardcoded to EUR = 2; future multi-currency will parameterise this
#: per-currency (e.g. JPY = 0, KWD = 3).
MINOR_UNIT: int = 2

#: Quantizer for EUR minor-unit rounding: ``Decimal("0.01")``.
MINOR_QUANT: Decimal = Decimal("0.01")


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


def quantize_to_minor_unit(value: Decimal, minor_unit: int = 2) -> Decimal:
    """Round *value* to the currency's minor unit using ``ROUND_HALF_UP``.

    This is the **document-level / client-facing** rounding step, distinct from
    ``quantize_money()`` which is the **intermediate calculation** step:

    * ``quantize_money()`` → 3 decimal places.  Used inside the pricing
      pipeline for all mid-calculation amounts (subtotals, running totals,
      etc.) to avoid premature precision loss.
    * ``quantize_to_minor_unit()`` → ``minor_unit`` decimal places (default 2,
      i.e. "cents" for EUR).  Applied **once**, as the final step on every
      line's net / VAT / total before the value is written to the document
      or persisted.  This is the value the client sees, uses for payment,
      and that appears on bank statements.

    Division of responsibility (D6, M7.5):
        v1 EUR is hard-coded to ``minor_unit=2`` via the module-level
        ``MINOR_UNIT`` constant.  The ``minor_unit`` parameter is the
        hook for future multi-currency support (e.g. JPY → 0, KWD → 3)
        without changing call-sites.

    Parameters
    ----------
    value:
        Any ``Decimal`` amount.
    minor_unit:
        Number of decimal places for the target currency's minor unit.
        Defaults to 2 (EUR cents).  Use 0 for currencies with no minor
        unit (e.g. JPY), 3 for milli-unit currencies (e.g. KWD).

    Returns
    -------
    Decimal
        *value* quantised to ``minor_unit`` decimal places with
        ``ROUND_HALF_UP``.

    Examples
    --------
    >>> quantize_to_minor_unit(Decimal("1.005"))
    Decimal('1.01')
    >>> quantize_to_minor_unit(Decimal("1.004"))
    Decimal('1.00')
    >>> quantize_to_minor_unit(Decimal("-1.005"))
    Decimal('-1.01')
    >>> quantize_to_minor_unit(Decimal("3865.166"))
    Decimal('3865.17')
    >>> quantize_to_minor_unit(Decimal("1.5"), minor_unit=0)
    Decimal('2')
    """
    quant = Decimal(10) ** -minor_unit
    return value.quantize(quant, rounding=MONEY_ROUNDING)


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
