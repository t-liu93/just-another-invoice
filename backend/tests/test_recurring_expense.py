"""Unit tests for recurring expense service pure helpers (M8 step 3).

Tests ``services.recurring_expense.compute_next_run`` – a pure function with
no DB access that can be tested in isolation.

Coverage:
- MONTHLY: typical case, cross-year, month-end retirement (Jan 31 → Feb 28)
- QUARTERLY: typical case, cross-year, month-end retirement
- YEARLY: typical case, cross-year, leap-year retirement (Feb 29 → Feb 28)
"""

from __future__ import annotations

from datetime import date

from jai.models._enums import RecurringFrequency
from jai.services.recurring_expense import compute_next_run

M = RecurringFrequency.MONTHLY
Q = RecurringFrequency.QUARTERLY
Y = RecurringFrequency.YEARLY


class TestComputeNextRunMonthly:
    """``compute_next_run`` with MONTHLY frequency."""

    def test_typical_mid_month(self) -> None:
        """Normal mid-month advance: Mar 15 → Apr 15."""
        assert compute_next_run(M, date(2026, 3, 15)) == date(2026, 4, 15)

    def test_cross_year(self) -> None:
        """Crossing a year boundary: Dec 1 → Jan 1 of next year."""
        assert compute_next_run(M, date(2026, 12, 1)) == date(2027, 1, 1)

    def test_jan31_retires_to_feb28(self) -> None:
        """Jan 31 + 1 month → Feb 28 (non-leap year)."""
        assert compute_next_run(M, date(2026, 1, 31)) == date(2026, 2, 28)

    def test_jan31_retires_to_feb29_leap(self) -> None:
        """Jan 31 + 1 month → Feb 29 in a leap year."""
        assert compute_next_run(M, date(2024, 1, 31)) == date(2024, 2, 29)

    def test_mar31_retires_to_apr30(self) -> None:
        """Mar 31 + 1 month → Apr 30 (April has 30 days)."""
        assert compute_next_run(M, date(2026, 3, 31)) == date(2026, 4, 30)

    def test_may31_to_jun30(self) -> None:
        """May 31 + 1 month → Jun 30."""
        assert compute_next_run(M, date(2026, 5, 31)) == date(2026, 6, 30)

    def test_start_of_month(self) -> None:
        """First of month stays on first: Feb 1 → Mar 1."""
        assert compute_next_run(M, date(2026, 2, 1)) == date(2026, 3, 1)

    def test_end_of_month_stays_end(self) -> None:
        """30-day month end stays at 30: Apr 30 → May 30."""
        assert compute_next_run(M, date(2026, 4, 30)) == date(2026, 5, 30)


class TestComputeNextRunQuarterly:
    """``compute_next_run`` with QUARTERLY frequency."""

    def test_typical(self) -> None:
        """Jan 1 + 3 months → Apr 1."""
        assert compute_next_run(Q, date(2026, 1, 1)) == date(2026, 4, 1)

    def test_cross_year(self) -> None:
        """Oct 1 + 3 months → Jan 1 next year."""
        assert compute_next_run(Q, date(2026, 10, 1)) == date(2027, 1, 1)

    def test_nov30_retires_to_feb28(self) -> None:
        """Nov 30 + 3 months → Feb 28 (non-leap year)."""
        assert compute_next_run(Q, date(2025, 11, 30)) == date(2026, 2, 28)

    def test_aug31_to_nov30(self) -> None:
        """Aug 31 + 3 months → Nov 30 (November has 30 days)."""
        assert compute_next_run(Q, date(2026, 8, 31)) == date(2026, 11, 30)

    def test_cross_year_with_retirement(self) -> None:
        """Oct 31 + 3 months → Jan 31 next year."""
        assert compute_next_run(Q, date(2026, 10, 31)) == date(2027, 1, 31)


class TestComputeNextRunYearly:
    """``compute_next_run`` with YEARLY frequency."""

    def test_typical(self) -> None:
        """Jan 15 2026 → Jan 15 2027."""
        assert compute_next_run(Y, date(2026, 1, 15)) == date(2027, 1, 15)

    def test_cross_year_end(self) -> None:
        """Dec 31 2026 → Dec 31 2027."""
        assert compute_next_run(Y, date(2026, 12, 31)) == date(2027, 12, 31)

    def test_feb29_retires_to_feb28(self) -> None:
        """Feb 29 (leap year) + 1 year → Feb 28 (non-leap year)."""
        assert compute_next_run(Y, date(2024, 2, 29)) == date(2025, 2, 28)

    def test_feb28_to_feb28(self) -> None:
        """Feb 28 2026 + 1 year → Feb 28 2027."""
        assert compute_next_run(Y, date(2026, 2, 28)) == date(2027, 2, 28)

    def test_feb28_to_feb29_leap_year(self) -> None:
        """Feb 28 2027 + 1 year → Feb 28 2028 (leap year has Feb 29 but day=28 stays 28)."""
        assert compute_next_run(Y, date(2027, 2, 28)) == date(2028, 2, 28)

    def test_multiple_steps_via_loop(self) -> None:
        """Chaining 4 yearly steps from Jan 1 2023 arrives at Jan 1 2027."""
        d = date(2023, 1, 1)
        for _ in range(4):
            d = compute_next_run(Y, d)
        assert d == date(2027, 1, 1)
