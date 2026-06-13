"""Unit tests for expense service pure helpers (M8 step 1).

Tests ``services.expense.compute_expense_amounts`` and
``services.expense.resolve_deductible`` – both are pure functions with no DB
access and can be tested in isolation.

Coverage:
- gross = net + vat, each quantised to 2 dp (EUR minor unit)
- net × rate ≠ vat is accepted (D8: no equality check)
- deductible default: category True / False / None (→ fallback True) + explicit override
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from jai.services.expense import (
    compute_expense_amounts,
    compute_expense_vat_from_rate,
    resolve_deductible,
)

# ---------------------------------------------------------------------------
# compute_expense_amounts
# ---------------------------------------------------------------------------


class TestComputeExpenseAmounts:
    """``compute_expense_amounts`` – quantisation and gross calculation."""

    def test_typical_invoice(self) -> None:
        """100 + 21 = 121; each already at 2 dp."""
        net, vat, gross = compute_expense_amounts(Decimal("100"), Decimal("21"))
        assert net == Decimal("100.00")
        assert vat == Decimal("21.00")
        assert gross == Decimal("121.00")

    def test_gross_is_net_plus_vat(self) -> None:
        """gross = quantize_to_minor_unit(net_q + vat_q)."""
        net, vat, gross = compute_expense_amounts(Decimal("99.99"), Decimal("20.99"))
        assert net == Decimal("99.99")
        assert vat == Decimal("20.99")
        assert gross == Decimal("120.98")

    def test_vat_not_equal_net_times_rate_is_accepted(self) -> None:
        """D8: no validation that vat == net × rate.

        If 21% of 100.00 = 21.00 but supplier printed 21.05 on the invoice,
        both must be stored as given.
        """
        # net=100, vat=21.05 (real ticket rounding artefact)
        net, vat, gross = compute_expense_amounts(Decimal("100"), Decimal("21.05"))
        assert net == Decimal("100.00")
        assert vat == Decimal("21.05")
        assert gross == Decimal("121.05")

    def test_zero_vat(self) -> None:
        """Zero-rated expense: vat = 0."""
        net, vat, gross = compute_expense_amounts(Decimal("50"), Decimal("0"))
        assert net == Decimal("50.00")
        assert vat == Decimal("0.00")
        assert gross == Decimal("50.00")

    def test_zero_net_and_vat(self) -> None:
        """Edge case: both zero."""
        net, vat, gross = compute_expense_amounts(Decimal("0"), Decimal("0"))
        assert net == Decimal("0.00")
        assert vat == Decimal("0.00")
        assert gross == Decimal("0.00")

    def test_quantise_rounding_applied(self) -> None:
        """Inputs with > 2 dp are rounded to 2 dp before summing."""
        # 10.005 rounds to 10.01 (ROUND_HALF_UP)
        net, vat, gross = compute_expense_amounts(Decimal("10.005"), Decimal("2.095"))
        assert net == Decimal("10.01")
        assert vat == Decimal("2.10")
        assert gross == Decimal("12.11")  # 10.01 + 2.10

    def test_large_amounts(self) -> None:
        """Large values: no overflow in NUMERIC(18,3)."""
        net, vat, gross = compute_expense_amounts(Decimal("9999999.99"), Decimal("2099999.99"))
        assert net == Decimal("9999999.99")
        assert vat == Decimal("2099999.99")
        assert gross == Decimal("12099999.98")

    def test_gross_quantised_after_addition(self) -> None:
        """After quantising net and vat, the sum may still need final quantisation."""
        # net_q = 1.005 → rounds to 1.01; vat_q = 2.005 → rounds to 2.01
        # gross = 1.01 + 2.01 = 3.02 (already 2 dp)
        net, vat, gross = compute_expense_amounts(Decimal("1.005"), Decimal("2.005"))
        assert net == Decimal("1.01")
        assert vat == Decimal("2.01")
        assert gross == Decimal("3.02")


# ---------------------------------------------------------------------------
# compute_expense_vat_from_rate
# ---------------------------------------------------------------------------


class TestComputeExpenseVatFromRate:
    """``compute_expense_vat_from_rate`` – VAT derived from net × rate%."""

    def test_typical_21_percent(self) -> None:
        """100 × 21% = 21.00; gross = 121.00."""
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(Decimal("100"), Decimal("21"))
        assert net_q == Decimal("100.00")
        assert vat_q == Decimal("21.00")
        assert gross_q == Decimal("121.00")

    def test_rounding_at_half_up_boundary(self) -> None:
        """99.99 × 21% = 20.9979 → rounds HALF_UP to 21.00; gross = 120.99."""
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(Decimal("99.99"), Decimal("21"))
        assert net_q == Decimal("99.99")
        # 99.99 * 0.21 = 20.9979 → ROUND_HALF_UP → 21.00
        assert vat_q == Decimal("21.00")
        assert gross_q == Decimal("120.99")

    def test_zero_net(self) -> None:
        """Zero net → zero vat and gross."""
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(Decimal("0"), Decimal("21"))
        assert net_q == Decimal("0.00")
        assert vat_q == Decimal("0.00")
        assert gross_q == Decimal("0.00")

    def test_9_percent(self) -> None:
        """50 × 9% = 4.50; gross = 54.50."""
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(Decimal("50"), Decimal("9"))
        assert net_q == Decimal("50.00")
        assert vat_q == Decimal("4.50")
        assert gross_q == Decimal("54.50")

    def test_zero_rate(self) -> None:
        """0% rate (reverse-charge): vat = 0, gross = net."""
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(Decimal("150"), Decimal("0"))
        assert net_q == Decimal("150.00")
        assert vat_q == Decimal("0.00")
        assert gross_q == Decimal("150.00")

    def test_round_half_up_exactly_at_boundary(self) -> None:
        """1.005 × 21% = 0.21105 → ROUND_HALF_UP → 0.21; gross = 1.22."""
        # net_q = quantize_to_minor_unit(1.005) = 1.01 (ROUND_HALF_UP)
        # vat_q = quantize_to_minor_unit(1.01 * 21 / 100) = quantize(0.2121) = 0.21
        # gross_q = quantize_to_minor_unit(1.01 + 0.21) = 1.22
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(Decimal("1.005"), Decimal("21"))
        assert net_q == Decimal("1.01")
        assert vat_q == Decimal("0.21")
        assert gross_q == Decimal("1.22")

    def test_large_amount(self) -> None:
        """Large amount still quantises correctly."""
        net_q, vat_q, gross_q = compute_expense_vat_from_rate(
            Decimal("10000"), Decimal("21")
        )
        assert net_q == Decimal("10000.00")
        assert vat_q == Decimal("2100.00")
        assert gross_q == Decimal("12100.00")


# ---------------------------------------------------------------------------
# resolve_deductible
# ---------------------------------------------------------------------------


class TestResolveDeductible:
    """``resolve_deductible`` – three-tier default (D9)."""

    # -- Explicit overrides always win ------------------------------------------

    def test_explicit_true_overrides_category_false(self) -> None:
        result = resolve_deductible(explicit=True, category_default=False)
        assert result is True

    def test_explicit_false_overrides_category_true(self) -> None:
        result = resolve_deductible(explicit=False, category_default=True)
        assert result is False

    def test_explicit_false_overrides_category_none(self) -> None:
        result = resolve_deductible(explicit=False, category_default=None)
        assert result is False

    def test_explicit_true_overrides_category_none(self) -> None:
        result = resolve_deductible(explicit=True, category_default=None)
        assert result is True

    # -- Category default (no explicit) ----------------------------------------

    def test_category_true_when_no_explicit(self) -> None:
        result = resolve_deductible(explicit=None, category_default=True)
        assert result is True

    def test_category_false_when_no_explicit(self) -> None:
        result = resolve_deductible(explicit=None, category_default=False)
        assert result is False

    # -- Global fallback → True ------------------------------------------------

    def test_fallback_true_when_both_none(self) -> None:
        """D9: category has no default and user gave no explicit → True."""
        result = resolve_deductible(explicit=None, category_default=None)
        assert result is True


# ---------------------------------------------------------------------------
# Integration-level guard: ExpenseInput rejects negative amounts
# ---------------------------------------------------------------------------


class TestExpenseInputValidation:
    """Schema-level validation: negative amounts are rejected."""

    def test_negative_net_rejected(self) -> None:
        import uuid

        from jai.schemas.expense import ExpenseInput

        with pytest.raises(ValueError):
            ExpenseInput(
                expense_date="2026-01-01",
                category_id=uuid.uuid4(),
                vat_treatment_id=uuid.uuid4(),
                vat_rate_id=uuid.uuid4(),
                net_amount=Decimal("-1"),
                vat_amount=Decimal("0"),
            )

    def test_negative_vat_rejected(self) -> None:
        import uuid

        from jai.schemas.expense import ExpenseInput

        with pytest.raises(ValueError):
            ExpenseInput(
                expense_date="2026-01-01",
                category_id=uuid.uuid4(),
                vat_treatment_id=uuid.uuid4(),
                vat_rate_id=uuid.uuid4(),
                net_amount=Decimal("100"),
                vat_amount=Decimal("-1"),
            )

    def test_zero_amounts_accepted(self) -> None:
        """Zero net or vat is valid (e.g. zero-rate expense)."""
        import uuid

        from jai.schemas.expense import ExpenseInput

        inp = ExpenseInput(
            expense_date="2026-01-01",
            category_id=uuid.uuid4(),
            vat_treatment_id=uuid.uuid4(),
            vat_rate_id=uuid.uuid4(),
            net_amount=Decimal("0"),
            vat_amount=Decimal("0"),
        )
        assert inp.net_amount == Decimal("0")
        assert inp.vat_amount == Decimal("0")
