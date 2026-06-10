"""Unit tests for the pricing engine (M5 step 1).

Tests ``services.pricing.compute_pricing`` (pure calculation, no DB) and
helper functions.  All amounts are verified against hand-calculated values
using ``ROUND_HALF_UP`` / 3 decimal places.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from jai.models._enums import DiscountType, InvoiceTaxMode, VatTreatmentEffect
from jai.schemas.invoice import DiscountInput, InvoiceLineInput
from jai.services.money import quantize_money
from jai.services.pricing import (
    _calc_discount_amount,
    _distribute_fixed_discount,
    compute_pricing,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATE_21 = Decimal("21.000")
_RATE_9 = Decimal("9.000")
_RATE_0 = Decimal("0.000")


def _make_line(
    name: str = "Item",
    quantity: Decimal = Decimal("1"),
    unit_price: Decimal = Decimal("100.000"),
    discount: DiscountInput | None = None,
    vat_rate_id: uuid.UUID | None = None,
) -> InvoiceLineInput:
    return InvoiceLineInput(
        name=name,
        quantity=quantity,
        unit_price=unit_price,
        discount=discount or DiscountInput(),
        vat_rate_id=vat_rate_id,
    )


_RATE_ID_21 = uuid.uuid4()
_RATE_ID_9 = uuid.uuid4()
_RATE_ID_0 = uuid.uuid4()

_VAT_RATES: dict[uuid.UUID, tuple[str, Decimal]] = {
    _RATE_ID_21: ("21%", _RATE_21),
    _RATE_ID_9: ("9%", _RATE_9),
    _RATE_ID_0: ("0%", _RATE_0),
}


# ---------------------------------------------------------------------------
# _calc_discount_amount
# ---------------------------------------------------------------------------


class TestCalcDiscountAmount:

    def _disc(self, dtype: DiscountType, value: str = "0") -> DiscountInput:
        return DiscountInput(type=dtype, value=Decimal(value))

    def test_none_type_returns_zero(self) -> None:
        assert _calc_discount_amount(Decimal("100"), self._disc(DiscountType.NONE)) == Decimal("0")

    def test_percentage_discount(self) -> None:
        result = _calc_discount_amount(Decimal("200"), self._disc(DiscountType.PERCENTAGE, "10"))
        assert result == Decimal("20.000")

    def test_percentage_discount_rounding(self) -> None:
        """33.33% of 100 → 33.330 (ROUND_HALF_UP)."""
        result = _calc_discount_amount(Decimal("100"), self._disc(DiscountType.PERCENTAGE, "33.33"))
        assert result == Decimal("33.330")

    def test_percentage_100_percent(self) -> None:
        result = _calc_discount_amount(Decimal("100"), self._disc(DiscountType.PERCENTAGE, "100"))
        assert result == Decimal("100.000")

    def test_percentage_exceeds_base_raises(self) -> None:
        with pytest.raises(ValueError, match="exceeds base"):
            _calc_discount_amount(Decimal("100"), self._disc(DiscountType.PERCENTAGE, "150"))

    def test_fixed_discount(self) -> None:
        result = _calc_discount_amount(Decimal("100"), self._disc(DiscountType.FIXED, "25"))
        assert result == Decimal("25.000")

    def test_fixed_discount_exceeds_base_raises(self) -> None:
        with pytest.raises(ValueError, match="exceeds base"):
            _calc_discount_amount(Decimal("10"), self._disc(DiscountType.FIXED, "20"))

    def test_fixed_negative_raises(self) -> None:
        """Pydantic ge=0 on DiscountInput.value catches negative before service."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _calc_discount_amount(Decimal("100"), self._disc(DiscountType.FIXED, "-5"))

    def test_percentage_negative_raises(self) -> None:
        """Pydantic ge=0 on DiscountInput.value catches negative before service."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _calc_discount_amount(Decimal("100"), self._disc(DiscountType.PERCENTAGE, "-10"))


# ---------------------------------------------------------------------------
# _distribute_fixed_discount
# ---------------------------------------------------------------------------


class TestDistributeFixedDiscount:

    def test_single_line_absorbs_all(self) -> None:
        shares = _distribute_fixed_discount([Decimal("100")], Decimal("10"))
        assert shares == [Decimal("10.000")]

    def test_equal_bases_equal_shares(self) -> None:
        shares = _distribute_fixed_discount([Decimal("100"), Decimal("100")], Decimal("10"))
        assert len(shares) == 2
        assert shares[0] == Decimal("5.000")
        assert shares[1] == Decimal("5.000")
        assert sum(shares) == Decimal("10.000")

    def test_proportional_distribution(self) -> None:
        shares = _distribute_fixed_discount([Decimal("300"), Decimal("100")], Decimal("10"))
        assert shares[0] == Decimal("7.500")
        assert shares[1] == Decimal("2.500")
        assert sum(shares) == Decimal("10.000")

    def test_rounding_remainder_absorbed_by_last_nonzero(self) -> None:
        """3 lines of 100 each, discount of 10 → 3.333 + 3.333 + 3.334."""
        shares = _distribute_fixed_discount(
            [Decimal("100"), Decimal("100"), Decimal("100")],
            Decimal("10"),
        )
        assert sum(shares) == Decimal("10.000")
        # Last line absorbs the 0.001 remainder
        assert shares[2] == Decimal("3.334")

    def test_zero_base_gets_zero_share(self) -> None:
        shares = _distribute_fixed_discount([Decimal("0"), Decimal("100")], Decimal("10"))
        assert shares[0] == Decimal("0")
        assert shares[1] == Decimal("10.000")

    def test_all_zero_bases_get_zero(self) -> None:
        shares = _distribute_fixed_discount([Decimal("0"), Decimal("0")], Decimal("10"))
        assert all(s == Decimal("0") for s in shares)

    def test_zero_discount(self) -> None:
        shares = _distribute_fixed_discount([Decimal("100"), Decimal("100")], Decimal("0"))
        assert all(s == Decimal("0") for s in shares)

    def test_sum_equals_discount_exactly(self) -> None:
        """Fuzz: 7 lines with odd values, discount of 1.234."""
        bases = [Decimal(str(v)) for v in [100, 234, 56.7, 999, 12.345, 0.001, 88.88]]
        discount = Decimal("1.234")
        shares = _distribute_fixed_discount(bases, discount)
        assert sum(shares) == discount

    def test_tiny_discount_many_lines_no_negative_shares(self) -> None:
        """10 equal lines, discount 0.005 → no negative shares.

        0.005 / 10 = 0.0005 rounds up to 0.001 per line; old algorithm
        produced -0.004 on the last line.  Cap-to-remaining prevents this.
        """
        shares = _distribute_fixed_discount([Decimal("100")] * 10, Decimal("0.005"))
        assert sum(shares) == Decimal("0.005")
        assert all(s >= Decimal("0") for s in shares)

    def test_all_shares_non_negative_property(self) -> None:
        """Property: all shares are always >= 0 regardless of input."""
        bases = [Decimal(str(v)) for v in [100, 234, 56.7, 0.001, 88.88]]
        discount = Decimal("0.003")
        shares = _distribute_fixed_discount(bases, discount)
        assert sum(shares) == discount
        assert all(s >= Decimal("0") for s in shares)


# ---------------------------------------------------------------------------
# compute_pricing – LINE mode, excl VAT
# ---------------------------------------------------------------------------


class TestComputePricingLineExclVAT:

    def test_single_line_21pct(self) -> None:
        """1 line: 2 × €100, 21% VAT, no discounts."""
        lines = [
            _make_line(
                quantity=Decimal("2"),
                unit_price=Decimal("100"),
                vat_rate_id=_RATE_ID_21,
            )
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.subtotal_excl_vat == Decimal("200.000")
        assert result.line_discount_total == Decimal("0")
        assert result.document_discount_amount == Decimal("0")
        assert result.taxable_amount == Decimal("200.000")
        assert result.vat_total == Decimal("42.000")  # 200 × 21%
        assert result.total_incl_vat == Decimal("242.000")
        assert len(result.lines) == 1
        assert len(result.line_taxes) == 1

    def test_multi_rate_lines(self) -> None:
        """2 lines: 1×€100 @ 21% + 1×€100 @ 9%."""
        lines = [
            _make_line(name="A", unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21),
            _make_line(name="B", unit_price=Decimal("100"), vat_rate_id=_RATE_ID_9),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.subtotal_excl_vat == Decimal("200.000")
        assert result.vat_total == Decimal("30.000")  # 21 + 9
        assert result.total_incl_vat == Decimal("230.000")

    def test_zero_rate_line(self) -> None:
        """1 line @ 0% VAT."""
        lines = [_make_line(unit_price=Decimal("50"), vat_rate_id=_RATE_ID_0)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.vat_total == Decimal("0")
        assert result.total_incl_vat == Decimal("50.000")

    def test_line_percentage_discount(self) -> None:
        """1 line: €200 with 10% line discount, 21% VAT."""
        lines = [_make_line(
            unit_price=Decimal("200"),
            discount=DiscountInput(type=DiscountType.PERCENTAGE, value=Decimal("10")),
            vat_rate_id=_RATE_ID_21,
        )]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        # Line discount: 200 × 10% = 20
        # Taxable: 180, VAT: 37.800
        assert result.line_discount_total == Decimal("20.000")
        assert result.taxable_amount == Decimal("180.000")
        assert result.vat_total == Decimal("37.800")
        assert result.total_incl_vat == Decimal("217.800")

    def test_line_fixed_discount(self) -> None:
        """1 line: €100 with €15 fixed discount, 21% VAT."""
        lines = [_make_line(
            unit_price=Decimal("100"),
            discount=DiscountInput(type=DiscountType.FIXED, value=Decimal("15")),
            vat_rate_id=_RATE_ID_21,
        )]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.line_discount_total == Decimal("15.000")
        assert result.taxable_amount == Decimal("85.000")
        assert result.vat_total == Decimal("17.850")

    def test_document_percentage_discount(self) -> None:
        """2 lines of €100 each, 10% document discount, 21% VAT."""
        lines = [
            _make_line(name="A", unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21),
            _make_line(name="B", unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.PERCENTAGE, value=Decimal("10")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        # Subtotal: 200, doc discount: 20, taxable: 180, VAT: 37.800
        assert result.document_discount_amount == Decimal("20.000")
        assert result.taxable_amount == Decimal("180.000")
        assert result.vat_total == Decimal("37.800")

    def test_document_fixed_discount_proportional(self) -> None:
        """3 lines of 300/200/100, €30 fixed document discount, 21% VAT."""
        lines = [
            _make_line(name="A", unit_price=Decimal("300"), vat_rate_id=_RATE_ID_21),
            _make_line(name="B", unit_price=Decimal("200"), vat_rate_id=_RATE_ID_21),
            _make_line(name="C", unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.FIXED, value=Decimal("30")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.document_discount_amount == Decimal("30.000")
        # Total taxable = 600 - 30 = 570
        assert result.taxable_amount == Decimal("570.000")
        # VAT = 570 × 21% = 119.700
        assert result.vat_total == Decimal("119.700")
        # Verify per-line shares
        line_shares = [line.document_discount_share for line in result.lines]
        assert sum(line_shares) == Decimal("30.000")

    def test_line_and_document_discounts_combined(self) -> None:
        """1 line: €100, 10% line disc + €5 fixed doc disc, 21% VAT."""
        lines = [_make_line(
            unit_price=Decimal("100"),
            discount=DiscountInput(type=DiscountType.PERCENTAGE, value=Decimal("10")),
            vat_rate_id=_RATE_ID_21,
        )]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.FIXED, value=Decimal("5")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        # Line disc: 10, after: 90. Doc disc: 5. Taxable: 85.
        assert result.line_discount_total == Decimal("10.000")
        assert result.document_discount_amount == Decimal("5.000")
        assert result.taxable_amount == Decimal("85.000")
        assert result.vat_total == Decimal("17.850")

    def test_missing_vat_rate_id_raises(self) -> None:
        """LINE mode requires vat_rate_id on every line."""
        lines = [_make_line(unit_price=Decimal("100"))]
        with pytest.raises(ValueError, match="vat_rate_id is required"):
            compute_pricing(
                lines,
                tax_mode=InvoiceTaxMode.LINE,
                amounts_include_vat=False,
                discount=DiscountInput(),
                treatment_effect=VatTreatmentEffect.APPLY_RATE,
                vat_rates=_VAT_RATES,
                document_vat_rate=None,
            )

    def test_unknown_vat_rate_id_raises(self) -> None:
        """LINE mode: unknown vat_rate_id should raise."""
        lines = [_make_line(unit_price=Decimal("100"), vat_rate_id=uuid.uuid4())]
        with pytest.raises(ValueError, match="not found"):
            compute_pricing(
                lines,
                tax_mode=InvoiceTaxMode.LINE,
                amounts_include_vat=False,
                discount=DiscountInput(),
                treatment_effect=VatTreatmentEffect.APPLY_RATE,
                vat_rates=_VAT_RATES,
                document_vat_rate=None,
            )


# ---------------------------------------------------------------------------
# compute_pricing – LINE mode, incl VAT (reverse)
# ---------------------------------------------------------------------------


class TestComputePricingLineInclVAT:

    def test_single_line_21pct_reverse(self) -> None:
        """1 line: 1 × €121 (incl 21%), reverse → €100 excl + €21 VAT."""
        lines = [_make_line(unit_price=Decimal("121"), vat_rate_id=_RATE_ID_21)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=True,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.total_incl_vat == Decimal("121.000")
        assert result.taxable_amount == Decimal("100.000")
        assert result.vat_total == Decimal("21.000")
        assert result.lines[0].subtotal_incl_vat == Decimal("121.000")
        assert result.lines[0].subtotal_excl_vat == Decimal("100.000")

    def test_incl_vat_with_line_discount(self) -> None:
        """1 line: €121 incl, 10% line disc, 21% VAT → after disc = 108.9."""
        lines = [_make_line(
            unit_price=Decimal("121"),
            discount=DiscountInput(type=DiscountType.PERCENTAGE, value=Decimal("10")),
            vat_rate_id=_RATE_ID_21,
        )]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=True,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        # Line disc: 12.100, after: 108.900
        assert result.line_discount_total == Decimal("12.100")
        # Reverse: 108.900 / 1.21 = 90.000
        assert result.taxable_amount == Decimal("90.000")
        assert result.vat_total == Decimal("18.900")
        assert result.total_incl_vat == Decimal("108.900")

    def test_incl_vat_with_doc_discount(self) -> None:
        """2 lines of €121 each, 10% doc disc, 21% VAT."""
        lines = [
            _make_line(name="A", unit_price=Decimal("121"), vat_rate_id=_RATE_ID_21),
            _make_line(name="B", unit_price=Decimal("121"), vat_rate_id=_RATE_ID_21),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=True,
            discount=DiscountInput(type=DiscountType.PERCENTAGE, value=Decimal("10")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        # Total incl: 242, doc disc: 24.200, after: 217.800
        assert result.document_discount_amount == Decimal("24.200")
        assert result.total_incl_vat == Decimal("217.800")
        # Reverse: 217.800 / 1.21 = 180.000
        assert result.taxable_amount == Decimal("180.000")
        assert result.vat_total == Decimal("37.800")


# ---------------------------------------------------------------------------
# compute_pricing – DOCUMENT mode
# ---------------------------------------------------------------------------


class TestComputePricingDocumentMode:

    def test_single_rate_excl(self) -> None:
        """2 lines, DOCUMENT mode, 21% rate, excl VAT."""
        lines = [
            _make_line(name="A", unit_price=Decimal("100")),
            _make_line(name="B", unit_price=Decimal("200")),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.DOCUMENT,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=("21%", _RATE_21),
        )
        assert result.subtotal_excl_vat == Decimal("300.000")
        assert result.taxable_amount == Decimal("300.000")
        assert result.vat_total == Decimal("63.000")  # 300 × 21%
        assert result.total_incl_vat == Decimal("363.000")
        assert len(result.document_taxes) == 1
        assert len(result.line_taxes) == 0

    def test_document_mode_with_discount(self) -> None:
        """DOCUMENT mode, €10 doc disc on €300, 21%."""
        lines = [
            _make_line(name="A", unit_price=Decimal("100")),
            _make_line(name="B", unit_price=Decimal("200")),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.DOCUMENT,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.FIXED, value=Decimal("10")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=("21%", _RATE_21),
        )
        assert result.document_discount_amount == Decimal("10.000")
        assert result.taxable_amount == Decimal("290.000")
        assert result.vat_total == Decimal("60.900")  # 290 × 21%

    def test_document_mode_incl_vat(self) -> None:
        """DOCUMENT mode, incl VAT input, 21% → reverse."""
        lines = [
            _make_line(name="A", unit_price=Decimal("121")),
            _make_line(name="B", unit_price=Decimal("242")),
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.DOCUMENT,
            amounts_include_vat=True,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=("21%", _RATE_21),
        )
        # Total incl: 363, reverse: 363 / 1.21 = 300
        assert result.total_incl_vat == Decimal("363.000")
        assert result.taxable_amount == Decimal("300.000")
        assert result.vat_total == Decimal("63.000")

    def test_document_mode_missing_rate_raises(self) -> None:
        """DOCUMENT mode requires document_vat_rate."""
        lines = [_make_line(unit_price=Decimal("100"))]
        with pytest.raises(ValueError, match="document_vat_rate_id is required"):
            compute_pricing(
                lines,
                tax_mode=InvoiceTaxMode.DOCUMENT,
                amounts_include_vat=False,
                discount=DiscountInput(),
                treatment_effect=VatTreatmentEffect.APPLY_RATE,
                vat_rates=_VAT_RATES,
                document_vat_rate=None,
            )

    def test_document_mode_100pct_discount_returns_tax_entry(self) -> None:
        """100% document discount → taxable=0, VAT=0, but tax entry still present."""
        lines = [_make_line(unit_price=Decimal("100"))]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.DOCUMENT,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.PERCENTAGE, value=Decimal("100")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=("21%", _RATE_21),
        )
        assert len(result.document_taxes) == 1
        assert result.document_taxes[0].taxable_amount == Decimal("0.000")
        assert result.document_taxes[0].tax_amount == Decimal("0.000")
        assert result.document_taxes[0].vat_rate_percent == _RATE_21
        assert result.document_taxes[0].effective_vat_percent == _RATE_21


# ---------------------------------------------------------------------------
# compute_pricing – VAT treatment effects
# ---------------------------------------------------------------------------


class TestVatTreatmentEffects:

    def test_zero_reverse_effect(self) -> None:
        """ZERO_REVERSE: effective rate = 0, VAT = 0."""
        lines = [_make_line(unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.ZERO_REVERSE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.vat_total == Decimal("0")
        assert result.total_incl_vat == Decimal("100.000")
        assert result.taxable_amount == Decimal("100.000")
        # Line tax still records the original rate for reporting
        assert result.line_taxes[0].vat_rate_percent == _RATE_21
        assert result.line_taxes[0].effective_vat_percent == Decimal("0")

    def test_exempt_effect(self) -> None:
        lines = [_make_line(unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.EXEMPT,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.vat_total == Decimal("0")
        assert result.total_incl_vat == Decimal("100.000")

    def test_zero_export_effect(self) -> None:
        lines = [_make_line(unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.ZERO_EXPORT,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.vat_total == Decimal("0")

    def test_zero_treatment_incl_vat(self) -> None:
        """Incl VAT + zero treatment: no reverse, total = taxable."""
        lines = [_make_line(unit_price=Decimal("100"), vat_rate_id=_RATE_ID_21)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=True,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.ZERO_REVERSE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.taxable_amount == Decimal("100.000")
        assert result.vat_total == Decimal("0")
        assert result.total_incl_vat == Decimal("100.000")
        assert result.lines[0].subtotal_excl_vat == Decimal("100.000")

    def test_document_mode_zero_treatment(self) -> None:
        """DOCUMENT mode + zero treatment: VAT = 0 at doc level."""
        lines = [_make_line(unit_price=Decimal("100"))]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.DOCUMENT,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.ZERO_REVERSE,
            vat_rates=_VAT_RATES,
            document_vat_rate=("21%", _RATE_21),
        )
        assert result.vat_total == Decimal("0")
        assert result.total_incl_vat == Decimal("100.000")


# ---------------------------------------------------------------------------
# Rounding edge cases
# ---------------------------------------------------------------------------


class TestRoundingEdgeCases:

    def test_quantity_fractional(self) -> None:
        """1.5 × €66.667 = 100.001 → quantized to 100.001."""
        lines = [
            _make_line(
                quantity=Decimal("1.5"),
                unit_price=Decimal("66.667"),
                vat_rate_id=_RATE_ID_21,
            )
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.subtotal_excl_vat == Decimal("100.001")
        expected_vat = quantize_money(
            Decimal("100.001") * Decimal("21") / Decimal("100")
        )
        assert result.vat_total == expected_vat

    def test_small_amounts_rounding(self) -> None:
        """0.001 × €0.001 = 0.000001 → quantized to 0.000."""
        lines = [
            _make_line(
                quantity=Decimal("0.001"),
                unit_price=Decimal("0.001"),
                vat_rate_id=_RATE_ID_21,
            )
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.subtotal_excl_vat == Decimal("0.000")
        assert result.vat_total == Decimal("0.000")

    def test_many_lines_sum_consistency(self) -> None:
        """100 lines of €1.009 each. Each line VAT quantised individually then summed."""
        lines = [_make_line(unit_price=Decimal("1.009"), vat_rate_id=_RATE_ID_21)] * 100
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        # Each line: 1 × 1.009 = 1.009. 100 lines: 100.900
        assert result.subtotal_excl_vat == Decimal("100.900")
        assert result.taxable_amount == Decimal("100.900")
        # Each line VAT: quantize_money(1.009 × 21/100) = 0.212
        # Sum of 100 lines: 21.200 (not 21.189, because per-line quantise)
        per_line_vat = quantize_money(Decimal("1.009") * Decimal("21") / Decimal("100"))
        assert result.vat_total == per_line_vat * 100

    def test_doc_discount_distribution_remainder(self) -> None:
        """5 lines of €1 each, €1 doc discount → each gets 0.200."""
        lines = [
            _make_line(name=f"Item {i}", unit_price=Decimal("1"), vat_rate_id=_RATE_ID_21)
            for i in range(5)
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.FIXED, value=Decimal("1")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.document_discount_amount == Decimal("1.000")
        assert result.taxable_amount == Decimal("4.000")

    def test_incl_vat_odd_number_reverse(self) -> None:
        """€100 incl at 9% → excl = 91.743, VAT = 8.257."""
        lines = [_make_line(unit_price=Decimal("100"), vat_rate_id=_RATE_ID_9)]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=True,
            discount=DiscountInput(),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        excl = quantize_money(Decimal("100") / (1 + Decimal("9") / Decimal("100")))
        vat = Decimal("100") - excl
        assert result.taxable_amount == excl
        assert result.vat_total == vat
        assert result.total_incl_vat == Decimal("100.000")

    def test_tiny_doc_discount_multi_rate_no_negative_shares(self) -> None:
        """10 lines (alternating 21% / 9%), €0.005 fixed doc discount.

        Ensures no line gets a negative document_discount_share and each
        line's taxable_amount stays non-negative.
        """
        lines = [
            _make_line(
                name=f"Item {i}",
                unit_price=Decimal("100"),
                vat_rate_id=_RATE_ID_21 if i % 2 == 0 else _RATE_ID_9,
            )
            for i in range(10)
        ]
        result = compute_pricing(
            lines,
            tax_mode=InvoiceTaxMode.LINE,
            amounts_include_vat=False,
            discount=DiscountInput(type=DiscountType.FIXED, value=Decimal("0.005")),
            treatment_effect=VatTreatmentEffect.APPLY_RATE,
            vat_rates=_VAT_RATES,
            document_vat_rate=None,
        )
        assert result.document_discount_amount == Decimal("0.005")
        for line in result.lines:
            assert line.document_discount_share >= Decimal("0")
            assert line.taxable_amount >= Decimal("0")
