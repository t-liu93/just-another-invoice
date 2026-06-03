"""Tests for ``jai.services.money`` – Decimal rounding helpers.

Covers:
  - Happy paths: basic rounding, quantisation
  - Edge cases: half-up behaviour, negative numbers, zero, scale truncation
  - Arithmetic helpers: add, sub, mul
"""

from __future__ import annotations

from decimal import Decimal

from jai.services.money import (
    MONEY_QUANT,
    MONEY_ROUNDING,
    MONEY_SCALE,
    add_money,
    is_money_zero,
    mul_money,
    quantize_money,
    sub_money,
)

# ============================================================================
# quantize_money – core rounding
# ============================================================================


class TestQuantizeMoney:
    """Tests for ``quantize_money()``."""

    # -- Happy paths ---------------------------------------------------------

    def test_exact_scale_unchanged(self) -> None:
        """Values already at scale-3 should pass through unchanged."""
        assert quantize_money(Decimal("1.234")) == Decimal("1.234")

    def test_round_half_up_positive(self) -> None:
        """Positive 4th digit ≥ 5 rounds up."""
        assert quantize_money(Decimal("1.2345")) == Decimal("1.235")

    def test_round_half_up_negative(self) -> None:
        """Negative 4th digit ≥ 5 rounds *away from zero* (half-up)."""
        assert quantize_money(Decimal("-1.2345")) == Decimal("-1.235")

    def test_round_down_positive(self) -> None:
        """Positive 4th digit < 5 rounds down."""
        assert quantize_money(Decimal("1.2344")) == Decimal("1.234")

    def test_round_down_negative(self) -> None:
        """Negative 4th digit < 5 rounds toward zero."""
        assert quantize_money(Decimal("-1.2344")) == Decimal("-1.234")

    # -- Edge cases ----------------------------------------------------------

    def test_zero(self) -> None:
        """Zero should remain zero."""
        assert quantize_money(Decimal("0")) == Decimal("0.000")

    def test_large_value(self) -> None:
        """Large values should still round correctly."""
        assert quantize_money(Decimal("999999999999999.9995")) == Decimal(
            "1000000000000000.000"
        )

    def test_very_small_positive(self) -> None:
        """Very small positive value rounds to zero."""
        assert quantize_money(Decimal("0.0001")) == Decimal("0.000")

    def test_very_small_negative(self) -> None:
        """Very small negative value rounds to zero."""
        assert quantize_money(Decimal("-0.0001")) == Decimal("0.000")

    def test_integer_input(self) -> None:
        """Integer-valued Decimals should get .000 appended."""
        assert quantize_money(Decimal("42")) == Decimal("42.000")

    def test_negative_integer(self) -> None:
        """Negative integer-valued Decimals should get .000 appended."""
        assert quantize_money(Decimal("-7")) == Decimal("-7.000")

    def test_half_exactly_at_boundary(self) -> None:
        """Exactly .0005 → rounds up to .001 (half-up)."""
        assert quantize_money(Decimal("0.0005")) == Decimal("0.001")

    def test_many_decimal_places(self) -> None:
        """Values with many decimal places should round correctly."""
        assert quantize_money(Decimal("1.23456789")) == Decimal("1.235")

    def test_scale_2_input(self) -> None:
        """Values with fewer decimal places get zero-padded."""
        assert quantize_money(Decimal("1.23")) == Decimal("1.230")

    def test_scale_1_input(self) -> None:
        assert quantize_money(Decimal("1.2")) == Decimal("1.200")

    def test_scale_0_input(self) -> None:
        assert quantize_money(Decimal("1")) == Decimal("1.000")

    def test_all_nines_rounding(self) -> None:
        """999.9995 → 1000.000 (carry propagation)."""
        assert quantize_money(Decimal("999.9995")) == Decimal("1000.000")


# ============================================================================
# add_money / sub_money / mul_money
# ============================================================================


class TestAddMoney:
    """Tests for ``add_money()``."""

    def test_simple_addition(self) -> None:
        assert add_money(Decimal("1.100"), Decimal("2.200")) == Decimal("3.300")

    def test_addition_with_rounding(self) -> None:
        """Result of addition is quantised."""
        assert add_money(Decimal("1.1111"), Decimal("2.2222")) == Decimal("3.333")

    def test_addition_negative(self) -> None:
        assert add_money(Decimal("5.000"), Decimal("-3.000")) == Decimal("2.000")

    def test_addition_zero(self) -> None:
        assert add_money(Decimal("0"), Decimal("0")) == Decimal("0.000")


class TestSubMoney:
    """Tests for ``sub_money()``."""

    def test_simple_subtraction(self) -> None:
        assert sub_money(Decimal("5.000"), Decimal("3.000")) == Decimal("2.000")

    def test_subtraction_negative_result(self) -> None:
        assert sub_money(Decimal("3.000"), Decimal("5.000")) == Decimal("-2.000")

    def test_subtraction_with_rounding(self) -> None:
        assert sub_money(Decimal("1.1111"), Decimal("0.0006")) == Decimal("1.111")


class TestMulMoney:
    """Tests for ``mul_money()``."""

    def test_quantity_times_price(self) -> None:
        """Standard line subtotal: 3 × 10.50 = 31.500."""
        assert mul_money(Decimal("3"), Decimal("10.50")) == Decimal("31.500")

    def test_fractional_quantity(self) -> None:
        """2.5 × 4.00 = 10.000."""
        assert mul_money(Decimal("2.5"), Decimal("4.00")) == Decimal("10.000")

    def test_zero_quantity(self) -> None:
        assert mul_money(Decimal("0"), Decimal("99.99")) == Decimal("0.000")

    def test_negative_quantity(self) -> None:
        """Negative quantity (e.g. credit note line)."""
        assert mul_money(Decimal("-2"), Decimal("5.00")) == Decimal("-10.000")

    def test_many_decimals(self) -> None:
        """Product with many decimals gets quantised."""
        assert mul_money(Decimal("3.333"), Decimal("3.333")) == Decimal("11.109")

    def test_one_times_one(self) -> None:
        assert mul_money(Decimal("1"), Decimal("1")) == Decimal("1.000")


# ============================================================================
# is_money_zero
# ============================================================================


class TestIsMoneyZero:
    """Tests for ``is_money_zero()``."""

    def test_zero(self) -> None:
        assert is_money_zero(Decimal("0")) is True

    def test_explicit_zero(self) -> None:
        assert is_money_zero(Decimal("0.000")) is True

    def test_positive(self) -> None:
        assert is_money_zero(Decimal("0.001")) is False

    def test_negative(self) -> None:
        assert is_money_zero(Decimal("-0.001")) is False

    def test_rounds_to_zero(self) -> None:
        """0.0004 rounds down to 0.000 → considered zero."""
        assert is_money_zero(Decimal("0.0004")) is True

    def test_rounds_to_nonzero(self) -> None:
        """0.0005 rounds up to 0.001 → NOT zero."""
        assert is_money_zero(Decimal("0.0005")) is False


# ============================================================================
# Constants
# ============================================================================


class TestConstants:
    """Verify module-level constants are as documented."""

    def test_money_scale(self) -> None:
        assert MONEY_SCALE == 3

    def test_money_quant(self) -> None:
        assert MONEY_QUANT == Decimal("0.001")

    def test_money_rounding_is_half_up(self) -> None:
        from decimal import ROUND_HALF_UP

        assert MONEY_ROUNDING == ROUND_HALF_UP
