"""Shared immutable reporting-event helpers for M12 projections.

The BTW, ICP and P/L services each read their own normalized source rows, but
share this period identity so correction warnings cannot disagree about dates.
"""

from __future__ import annotations

from datetime import date


def quarter_label(value: date) -> str:
    """Return the stable calendar-quarter label for a dated reporting event."""
    return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
