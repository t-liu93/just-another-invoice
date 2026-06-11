"""Unit tests for services.costing.compute_estimate (M6.5 step 1).

Pure calculation tests -- no DB, no HTTP.
Covers:
- Per-line: line_total / margin_amount / line_sell_excl_vat
- Rolling: total_margin / total_excl_vat
- Per-group: group_sell_excl_vat
- ROUND_HALF_UP boundary
- margin_rate = 0 (labor / shipping / overhead)
- Ungrouped lines: count toward total, not toward group
- Indicative VAT with / without standard rate
- Empty lines / empty groups
- Multiple groups, multiple lines per group
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from jai.schemas.estimate import (
    EstimateCalculationRequest,
    EstimateGroupInput,
    EstimateLineInput,
)
from jai.services.costing import compute_estimate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line(
    name: str = "Item",
    *,
    unit_cost: Decimal = Decimal("100"),
    quantity: Decimal = Decimal("1"),
    margin_rate: Decimal = Decimal("0"),
    group_ref: str | None = None,
) -> EstimateLineInput:
    return EstimateLineInput(
        name=name,
        unit_cost_excl_vat=unit_cost,
        quantity=quantity,
        margin_rate=margin_rate,
        group_ref=group_ref,
    )


def _group(
    ref: str = "G1",
    public_description: str = "Group description",
    vat_rate_id: str | None = None,
    sort_order: int = 0,
) -> EstimateGroupInput:
    return EstimateGroupInput(
        ref=ref,
        public_description=public_description,
        sort_order=sort_order,
    )


# ---------------------------------------------------------------------------
# Per-line calculation
# ---------------------------------------------------------------------------


class TestPerLineCalculation:
    """Test per-line line_total, margin_amount, line_sell_excl_vat."""

    def test_single_line_with_margin(self) -> None:
        """100 * 1 at 30% margin → total=100, margin=30, sell=130."""
        req = EstimateCalculationRequest(
            lines=[_line("Device", unit_cost=Decimal("100"), margin_rate=Decimal("0.3"))],
        )
        result = compute_estimate(req)
        assert len(result.lines) == 1
        line = result.lines[0]
        assert line.line_total == Decimal("100.000")
        assert line.margin_amount == Decimal("30.000")
        assert line.line_sell_excl_vat == Decimal("130.000")

    def test_single_line_quantity_2(self) -> None:
        """100 * 2 at 20% → total=200, margin=40, sell=240."""
        req = EstimateCalculationRequest(
            lines=[
                _line(
                    "Device",
                    unit_cost=Decimal("100"),
                    quantity=Decimal("2"),
                    margin_rate=Decimal("0.2"),
                ),
            ],
        )
        result = compute_estimate(req)
        line = result.lines[0]
        assert line.line_total == Decimal("200.000")
        assert line.margin_amount == Decimal("40.000")
        assert line.line_sell_excl_vat == Decimal("240.000")

    def test_margin_rate_zero_labor(self) -> None:
        """Labor: cost=75, qty=1, margin=0 → total=75, margin=0, sell=75."""
        req = EstimateCalculationRequest(
            lines=[_line("Labor", unit_cost=Decimal("75"), margin_rate=Decimal("0"))],
        )
        result = compute_estimate(req)
        line = result.lines[0]
        assert line.line_total == Decimal("75.000")
        assert line.margin_amount == Decimal("0.000")
        assert line.line_sell_excl_vat == Decimal("75.000")

    def test_margin_rate_zero_shipping(self) -> None:
        """Shipping: cost=25, qty=1, margin=0 → total=25, margin=0, sell=25."""
        req = EstimateCalculationRequest(
            lines=[_line("Shipping", unit_cost=Decimal("25"), margin_rate=Decimal("0"))],
        )
        result = compute_estimate(req)
        line = result.lines[0]
        assert line.line_total == Decimal("25.000")
        assert line.margin_amount == Decimal("0.000")
        assert line.line_sell_excl_vat == Decimal("25.000")

    def test_zero_cost(self) -> None:
        """Zero cost line: total=0, margin=0, sell=0."""
        req = EstimateCalculationRequest(
            lines=[_line("Free", unit_cost=Decimal("0"), margin_rate=Decimal("0.3"))],
        )
        result = compute_estimate(req)
        line = result.lines[0]
        assert line.line_total == Decimal("0.000")
        assert line.margin_amount == Decimal("0.000")
        assert line.line_sell_excl_vat == Decimal("0.000")

    def test_default_margin_rate_is_zero(self) -> None:
        """margin_rate defaults to 0 when not specified."""
        line = _line("No Margin", unit_cost=Decimal("100"))
        assert line.margin_rate == Decimal("0")
        req = EstimateCalculationRequest(lines=[line])
        result = compute_estimate(req)
        assert result.lines[0].margin_amount == Decimal("0.000")

    def test_sort_order_sequential(self) -> None:
        """Lines get sequential sort_order starting from 0."""
        req = EstimateCalculationRequest(
            lines=[_line("A"), _line("B"), _line("C")],
        )
        result = compute_estimate(req)
        assert result.lines[0].sort_order == 0
        assert result.lines[1].sort_order == 1
        assert result.lines[2].sort_order == 2


# ---------------------------------------------------------------------------
# Rolling totals
# ---------------------------------------------------------------------------


class TestRollingTotals:
    """Test total_margin and total_excl_vat as sums of line values."""

    def test_two_lines(self) -> None:
        """Line1: 100*1 at 30% → sell=130, margin=30.
        Line2: 50*2 at 20% → total=100, margin=20, sell=120.
        total_margin=50, total_excl_vat=250.
        """
        req = EstimateCalculationRequest(
            lines=[
                _line("A", unit_cost=Decimal("100"), margin_rate=Decimal("0.3")),
                _line(
                    "B",
                    unit_cost=Decimal("50"),
                    quantity=Decimal("2"),
                    margin_rate=Decimal("0.2"),
                ),
            ],
        )
        result = compute_estimate(req)
        assert result.total_margin == Decimal("50.000")
        assert result.total_excl_vat == Decimal("250.000")

    def test_labor_plus_device(self) -> None:
        """Device: 200*1 at 25% → margin=50, sell=250.
        Labor: 75*1 at 0% → margin=0, sell=75.
        total_margin=50 (only device), total_excl_vat=325.
        """
        req = EstimateCalculationRequest(
            lines=[
                _line("Device", unit_cost=Decimal("200"), margin_rate=Decimal("0.25")),
                _line("Labor", unit_cost=Decimal("75"), margin_rate=Decimal("0")),
            ],
        )
        result = compute_estimate(req)
        assert result.total_margin == Decimal("50.000")
        assert result.total_excl_vat == Decimal("325.000")

    def test_three_lines_mixed_margin(self) -> None:
        """Three lines: device + accessory + labor."""
        req = EstimateCalculationRequest(
            lines=[
                _line(
                    "Charger",
                    unit_cost=Decimal("500"),
                    margin_rate=Decimal("0.3"),
                ),
                _line(
                    "Cable",
                    unit_cost=Decimal("50"),
                    quantity=Decimal("2"),
                    margin_rate=Decimal("0.2"),
                ),
                _line("Install", unit_cost=Decimal("75"), margin_rate=Decimal("0")),
            ],
        )
        result = compute_estimate(req)
        # Charger: total=500, margin=150, sell=650
        # Cable: total=100, margin=20, sell=120
        # Install: total=75, margin=0, sell=75
        assert result.total_margin == Decimal("170.000")
        assert result.total_excl_vat == Decimal("845.000")


# ---------------------------------------------------------------------------
# Per-group sell price
# ---------------------------------------------------------------------------


class TestGroupSellPrice:
    """Test group_sell_excl_vat = sum of group lines' line_sell_excl_vat."""

    def test_two_groups(self) -> None:
        """Group A: 2 device lines. Group B: 1 labor line (margin=0)."""
        req = EstimateCalculationRequest(
            groups=[_group("A"), _group("B")],
            lines=[
                _line(
                    "Device1",
                    unit_cost=Decimal("200"),
                    margin_rate=Decimal("0.3"),
                    group_ref="A",
                ),
                _line(
                    "Device2",
                    unit_cost=Decimal("100"),
                    margin_rate=Decimal("0.2"),
                    group_ref="A",
                ),
                _line(
                    "Labor",
                    unit_cost=Decimal("75"),
                    margin_rate=Decimal("0"),
                    group_ref="B",
                ),
            ],
        )
        result = compute_estimate(req)
        assert len(result.groups) == 2
        # Group A: Device1 sell=260 (200+60), Device2 sell=120 (100+20) → 380
        group_a = next(g for g in result.groups if g.ref == "A")
        assert group_a.group_sell_excl_vat == Decimal("380.000")
        # Group B: Labor sell=75 (75+0)
        group_b = next(g for g in result.groups if g.ref == "B")
        assert group_b.group_sell_excl_vat == Decimal("75.000")

    def test_group_with_no_lines(self) -> None:
        """Empty group → group_sell_excl_vat = 0."""
        req = EstimateCalculationRequest(
            groups=[_group("Empty")],
            lines=[],
        )
        result = compute_estimate(req)
        assert result.groups[0].group_sell_excl_vat == Decimal("0.000")

    def test_preserves_group_order(self) -> None:
        """Groups are returned in input order."""
        req = EstimateCalculationRequest(
            groups=[_group("Z"), _group("A"), _group("M")],
            lines=[],
        )
        result = compute_estimate(req)
        assert [g.ref for g in result.groups] == ["Z", "A", "M"]


# ---------------------------------------------------------------------------
# Ungrouped lines
# ---------------------------------------------------------------------------


class TestUngroupedLines:
    """Ungrouped lines count toward total but not toward any group."""

    def test_no_group_ref(self) -> None:
        """Line without group_ref → contributes to total only."""
        req = EstimateCalculationRequest(
            groups=[_group("G1")],
            lines=[
                _line(
                    "Grouped",
                    unit_cost=Decimal("100"),
                    margin_rate=Decimal("0.2"),
                    group_ref="G1",
                ),
                _line("Ungrouped", unit_cost=Decimal("50"), margin_rate=Decimal("0")),
            ],
        )
        result = compute_estimate(req)
        # Grouped sell = 120, Ungrouped sell = 50
        assert result.total_excl_vat == Decimal("170.000")
        group_g1 = result.groups[0]
        assert group_g1.group_sell_excl_vat == Decimal("120.000")

    def test_invalid_group_ref_rejected(self) -> None:
        """Line with group_ref not matching any group → ValidationError."""
        with pytest.raises(ValidationError, match="does not match any group"):
            EstimateCalculationRequest(
                groups=[_group("G1")],
                lines=[
                    _line(
                        "Orphan",
                        unit_cost=Decimal("100"),
                        margin_rate=Decimal("0"),
                        group_ref="UNKNOWN",
                    ),
                ],
            )

    def test_no_groups_at_all(self) -> None:
        """No groups defined → all lines ungrouped, totals still correct."""
        req = EstimateCalculationRequest(
            groups=[],
            lines=[
                _line("A", unit_cost=Decimal("100"), margin_rate=Decimal("0.1")),
                _line("B", unit_cost=Decimal("50"), margin_rate=Decimal("0")),
            ],
        )
        result = compute_estimate(req)
        assert len(result.groups) == 0
        assert result.total_excl_vat == Decimal("160.000")  # 110 + 50
        assert result.total_margin == Decimal("10.000")


# ---------------------------------------------------------------------------
# ROUND_HALF_UP boundary
# ---------------------------------------------------------------------------


class TestRoundingBoundary:
    """Test ROUND_HALF_UP rounding at exact 0.0005 boundary."""

    def test_line_total_rounds_half_up(self) -> None:
        """1.2345 * 1 → line_total = 1.235 (half-up)."""
        req = EstimateCalculationRequest(
            lines=[_line("R", unit_cost=Decimal("1.2345"), quantity=Decimal("1"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].line_total == Decimal("1.235")

    def test_line_total_rounds_down(self) -> None:
        """1.2344 * 1 → line_total = 1.234 (round down)."""
        req = EstimateCalculationRequest(
            lines=[_line("R", unit_cost=Decimal("1.2344"), quantity=Decimal("1"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].line_total == Decimal("1.234")

    def test_margin_amount_rounds_half_up(self) -> None:
        """line_total=100.000 * 0.12345 → margin = 12.345 (half-up)."""
        req = EstimateCalculationRequest(
            lines=[_line("R", unit_cost=Decimal("100"), margin_rate=Decimal("0.12345"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].margin_amount == Decimal("12.345")

    def test_margin_amount_rounds_down(self) -> None:
        """line_total=100.000 * 0.12344 → margin = 12.344 (round down)."""
        req = EstimateCalculationRequest(
            lines=[_line("R", unit_cost=Decimal("100"), margin_rate=Decimal("0.12344"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].margin_amount == Decimal("12.344")

    def test_no_double_rounding_in_totals(self) -> None:
        """Totals are sums of already-quantised values, no re-quantisation.

        Two lines that would round differently if re-quantised:
        line1 sell = 33.333, line2 sell = 33.333 → total = 66.666 (exact sum).
        """
        req = EstimateCalculationRequest(
            lines=[
                _line(
                    "A",
                    unit_cost=Decimal("100"),
                    quantity=Decimal("1"),
                    margin_rate=Decimal("0.33333"),
                ),
                _line(
                    "B",
                    unit_cost=Decimal("100"),
                    quantity=Decimal("1"),
                    margin_rate=Decimal("0.33333"),
                ),
            ],
        )
        result = compute_estimate(req)
        # Each: total=100, margin=quantize(100*0.33333)=33.333, sell=133.333
        assert result.lines[0].line_sell_excl_vat == Decimal("133.333")
        assert result.lines[1].line_sell_excl_vat == Decimal("133.333")
        assert result.total_excl_vat == Decimal("266.666")


# ---------------------------------------------------------------------------
# Indicative VAT
# ---------------------------------------------------------------------------


class TestIndicativeVat:
    """Test total_incl_vat_indicative and indicative_vat_rate_percent."""

    def test_with_standard_rate(self) -> None:
        """Standard 21%: total_excl=1000 → indicative = 1210.000."""
        req = EstimateCalculationRequest(
            lines=[_line("A", unit_cost=Decimal("1000"))],
        )
        result = compute_estimate(req, standard_vat_percent=Decimal("21"))
        assert result.indicative_vat_rate_percent == Decimal("21")
        assert result.total_incl_vat_indicative == Decimal("1210.000")

    def test_with_standard_rate_9_percent(self) -> None:
        """Standard 9%: total_excl=1000 → indicative = 1090.000."""
        req = EstimateCalculationRequest(
            lines=[_line("A", unit_cost=Decimal("1000"))],
        )
        result = compute_estimate(req, standard_vat_percent=Decimal("9"))
        assert result.indicative_vat_rate_percent == Decimal("9")
        assert result.total_incl_vat_indicative == Decimal("1090.000")

    def test_without_standard_rate(self) -> None:
        """No standard rate → both indicative fields are None."""
        req = EstimateCalculationRequest(
            lines=[_line("A", unit_cost=Decimal("1000"))],
        )
        result = compute_estimate(req, standard_vat_percent=None)
        assert result.total_incl_vat_indicative is None
        assert result.indicative_vat_rate_percent is None

    def test_zero_total_indicative(self) -> None:
        """Zero total_excl → indicative = 0.000."""
        req = EstimateCalculationRequest(lines=[])
        result = compute_estimate(req, standard_vat_percent=Decimal("21"))
        assert result.total_incl_vat_indicative == Decimal("0.000")

    def test_indicative_rounding(self) -> None:
        """total_excl = 99.999 * 1.21 → 120.999 → quantize = 120.999."""
        req = EstimateCalculationRequest(
            lines=[_line("A", unit_cost=Decimal("99.999"))],
        )
        result = compute_estimate(req, standard_vat_percent=Decimal("21"))
        # 99.999 * 1.21 = 120.99879 → quantize = 120.999
        assert result.total_incl_vat_indicative == Decimal("120.999")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty lines, empty groups, single line, etc."""

    def test_empty_lines_and_groups(self) -> None:
        """No lines, no groups → all zeros."""
        req = EstimateCalculationRequest()
        result = compute_estimate(req, standard_vat_percent=Decimal("21"))
        assert result.lines == []
        assert result.groups == []
        assert result.total_margin == Decimal("0.000")
        assert result.total_excl_vat == Decimal("0.000")
        assert result.total_incl_vat_indicative == Decimal("0.000")

    def test_empty_lines_no_indicative(self) -> None:
        """No lines, no standard rate → indicative is None."""
        req = EstimateCalculationRequest()
        result = compute_estimate(req)
        assert result.total_incl_vat_indicative is None

    def test_fractional_quantity(self) -> None:
        """Fractional quantity: 2.5 * 10.000 → total=25.000."""
        req = EstimateCalculationRequest(
            lines=[_line("X", unit_cost=Decimal("10"), quantity=Decimal("2.5"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].line_total == Decimal("25.000")

    def test_small_cost_large_quantity(self) -> None:
        """0.001 * 10000 → total=10.000."""
        req = EstimateCalculationRequest(
            lines=[_line("Tiny", unit_cost=Decimal("0.001"), quantity=Decimal("10000"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].line_total == Decimal("10.000")

    def test_large_margin_rate(self) -> None:
        """Margin rate = 1.0 (100% markup): cost=100 → margin=100, sell=200."""
        req = EstimateCalculationRequest(
            lines=[_line("Big Margin", unit_cost=Decimal("100"), margin_rate=Decimal("1.0"))],
        )
        result = compute_estimate(req)
        assert result.lines[0].margin_amount == Decimal("100.000")
        assert result.lines[0].line_sell_excl_vat == Decimal("200.000")

    def test_group_ref_preserved_in_line_result(self) -> None:
        """group_ref is echoed back in the line calculation result."""
        req = EstimateCalculationRequest(
            groups=[_group("G1")],
            lines=[_line("X", group_ref="G1")],
        )
        result = compute_estimate(req)
        assert result.lines[0].group_ref == "G1"


# ---------------------------------------------------------------------------
# Finding 1: group ref uniqueness & integrity
# ---------------------------------------------------------------------------


class TestGroupRefValidation:
    """Schema-level validation for group refs."""

    def test_duplicate_group_ref_rejected(self) -> None:
        """Two groups with the same ref → ValidationError."""
        with pytest.raises(ValidationError, match="Duplicate group ref"):
            EstimateCalculationRequest(
                groups=[_group("G"), _group("G")],
                lines=[],
            )

    def test_line_group_ref_must_match_group(self) -> None:
        """Line group_ref not matching any group → ValidationError."""
        with pytest.raises(ValidationError, match="does not match any group"):
            EstimateCalculationRequest(
                groups=[_group("A")],
                lines=[_line("X", group_ref="B")],
            )

    def test_null_group_ref_is_valid(self) -> None:
        """Line with group_ref=None is valid (ungrouped)."""
        req = EstimateCalculationRequest(
            groups=[_group("G1")],
            lines=[_line("Free", group_ref=None)],
        )
        result = compute_estimate(req)
        assert result.lines[0].group_ref is None


# ---------------------------------------------------------------------------
# Finding 2: non-string values in text fields → graceful 422
# ---------------------------------------------------------------------------


class TestNonStringFieldValidation:
    """Non-string values for name/ref/public_description → ValidationError."""

    def test_name_integer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateLineInput(
                name=123,  # type: ignore[arg-type]
                unit_cost_excl_vat=Decimal("10"),
                quantity=Decimal("1"),
            )

    def test_ref_integer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateGroupInput(ref=123, public_description="desc")  # type: ignore[arg-type]

    def test_public_description_integer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateGroupInput(ref="G1", public_description=123)  # type: ignore[arg-type]

    def test_group_ref_integer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateLineInput(
                name="X",
                unit_cost_excl_vat=Decimal("10"),
                quantity=Decimal("1"),
                group_ref=123,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Finding 3: blank strings → rejected (min_length=1 after strip)
# ---------------------------------------------------------------------------


class TestBlankStringValidation:
    """Whitespace-only or empty strings → ValidationError."""

    def test_name_blank_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateLineInput(
                name="   ",
                unit_cost_excl_vat=Decimal("10"),
                quantity=Decimal("1"),
            )

    def test_name_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateLineInput(
                name="",
                unit_cost_excl_vat=Decimal("10"),
                quantity=Decimal("1"),
            )

    def test_ref_blank_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateGroupInput(ref="   ", public_description="desc")

    def test_public_description_blank_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EstimateGroupInput(ref="G1", public_description="   ")


# ---------------------------------------------------------------------------
# Finding 4: empty group serialises as "0.000" not "0"
# ---------------------------------------------------------------------------


class TestGroupSellSerialization:
    """Empty group amount serialises with 3 decimal places."""

    def test_empty_group_sell_is_0_000(self) -> None:
        req = EstimateCalculationRequest(
            groups=[_group("G1")],
            lines=[],
        )
        result = compute_estimate(req)
        val = result.groups[0].group_sell_excl_vat
        # Must be Decimal("0.000") — serialises as "0.000", not "0"
        assert val == Decimal("0.000")
        assert str(val) == "0.000"
