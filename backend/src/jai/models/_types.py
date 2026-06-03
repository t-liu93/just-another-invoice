"""Custom column types shared across ORM models.

``Money`` – a convenience alias for ``Numeric(18, 3)`` used for all
monetary columns.  The scale (3 decimal places) matches the project
convention: amounts are stored to 1/1000 of the base currency unit.
Rounding rules (``ROUND_HALF_UP``) and the per-line vs per-total
strategy are codified in ``services/money.py`` and will be refined in
M5 (pricing engine).
"""

from __future__ import annotations

from sqlalchemy import Numeric

# 18 digits total, 3 after the decimal point → e.g. 999_999_999_999_999.999
Money = Numeric(18, 3)
