"""Tests for ``jai.models`` – ORM model metadata and type imports."""

from __future__ import annotations

from sqlalchemy import Numeric

from jai.models._types import Money


class TestMoneyType:
    """Verify the ``Money`` column type alias."""

    def test_money_is_numeric(self) -> None:
        """Money should be an instance of Numeric."""
        assert isinstance(Money, Numeric)

    def test_money_precision(self) -> None:
        """Money should have precision 18."""
        assert Money.precision == 18

    def test_money_scale(self) -> None:
        """Money should have scale 3."""
        assert Money.scale == 3
