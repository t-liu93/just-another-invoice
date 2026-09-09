"""Unit tests for the payment recomputation engine (M7 step 1).

Tests ``services.payment.recompute_payment_state`` (pure function, no DB).
All amounts are verified against hand-calculated values.

Coverage:
- UNPAID / PARTIALLY_PAID / PAID three-tier boundary
- Fully-paid SENT invoice → COMPLETED lifecycle
- paid_total == 0 → UNPAID
- Multi-payment sum without double-rounding (D9)
- due_amount is always >= 0 (overpayment guard is tested at service level)
- DRAFT / CANCELLED are never touched by the engine
- quantize_money input-quantisation contract (D9)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jai.models._enums import InvoicePaidStatus, InvoiceStatus, PaymentDirection
from jai.services.money import quantize_money
from jai.services.payment import (
    TaxBucketSnapshot,
    _payment_to_list_item,
    _payment_to_read,
    allocate_quote_payment_taxes,
    recompute_payment_state,
    tax_bucket_key,
)

# ---------------------------------------------------------------------------
# Stub helper
# ---------------------------------------------------------------------------


class _P:
    """Minimal payment stub for pure-function tests."""

    def __init__(self, amount: str, base_amount: str | None = None) -> None:
        self.amount = Decimal(amount)
        self.base_amount = Decimal(base_amount or amount)


# ---------------------------------------------------------------------------
# UNPAID – no payments at all
# ---------------------------------------------------------------------------


def test_unpaid_no_payments() -> None:
    state = recompute_payment_state(
        Decimal("121.000"),
        Decimal("121.000"),
        [],
        InvoiceStatus.SENT,
    )
    assert state.paid_status == InvoicePaidStatus.UNPAID
    assert state.paid_total == Decimal("0")
    assert state.due_amount == Decimal("121.000")
    assert state.new_status == InvoiceStatus.SENT


def test_unpaid_stays_draft_untouched() -> None:
    """DRAFT lifecycle is never changed by the engine (D3)."""
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [],
        InvoiceStatus.DRAFT,
    )
    assert state.paid_status == InvoicePaidStatus.UNPAID
    assert state.new_status == InvoiceStatus.DRAFT  # untouched


# ---------------------------------------------------------------------------
# PARTIALLY_PAID
# ---------------------------------------------------------------------------


def test_partially_paid_single_payment() -> None:
    state = recompute_payment_state(
        Decimal("121.000"),
        Decimal("121.000"),
        [_P("50.000")],
        InvoiceStatus.SENT,
    )
    assert state.paid_status == InvoicePaidStatus.PARTIALLY_PAID
    assert state.paid_total == Decimal("50.000")
    assert state.due_amount == Decimal("71.000")
    assert state.new_status == InvoiceStatus.SENT


def test_partially_paid_does_not_change_completed_to_sent_when_paid() -> None:
    """COMPLETED is only reversed if paid_status != PAID (partially paid case)."""
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [_P("60.000")],
        InvoiceStatus.COMPLETED,
    )
    assert state.paid_status == InvoicePaidStatus.PARTIALLY_PAID
    assert state.new_status == InvoiceStatus.SENT  # retrograde: COMPLETED → SENT


# ---------------------------------------------------------------------------
# PAID
# ---------------------------------------------------------------------------


def test_paid_single_payment_exact() -> None:
    state = recompute_payment_state(
        Decimal("121.000"),
        Decimal("121.000"),
        [_P("121.000")],
        InvoiceStatus.SENT,
    )
    assert state.paid_status == InvoicePaidStatus.PAID
    assert state.due_amount == Decimal("0")
    assert state.new_status == InvoiceStatus.COMPLETED  # SENT → COMPLETED


def test_paid_multi_payment_sums_to_exact() -> None:
    state = recompute_payment_state(
        Decimal("121.000"),
        Decimal("121.000"),
        [_P("60.000"), _P("61.000")],
        InvoiceStatus.SENT,
    )
    assert state.paid_status == InvoicePaidStatus.PAID
    assert state.paid_total == Decimal("121.000")
    assert state.due_amount == Decimal("0")
    assert state.new_status == InvoiceStatus.COMPLETED


# ---------------------------------------------------------------------------
# No double-rounding (D9)
# ---------------------------------------------------------------------------


def test_no_double_rounding_three_payments() -> None:
    """Three payments that individually round to 3 dp should not be re-rounded.

    Each payment of 33.333 rounds to 33.333.
    Σ = 33.333 + 33.333 + 33.333 = 99.999 (not 100.000 – that would require
    double-rounding).  Due amount = 100.000 − 99.999 = 0.001.
    """
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [_P("33.333"), _P("33.333"), _P("33.333")],
        InvoiceStatus.SENT,
    )
    assert state.paid_total == Decimal("99.999")
    assert state.due_amount == Decimal("0.001")
    assert state.paid_status == InvoicePaidStatus.PARTIALLY_PAID
    # Not PAID because due_amount != 0 (this is correct D9 behaviour)


def test_no_double_rounding_two_payments() -> None:
    """0.333 + 0.334 = 0.667 – no intermediate rounding of the sum."""
    state = recompute_payment_state(
        Decimal("1.000"),
        Decimal("1.000"),
        [_P("0.333"), _P("0.334")],
        InvoiceStatus.SENT,
    )
    assert state.paid_total == Decimal("0.667")
    assert state.due_amount == Decimal("0.333")


# ---------------------------------------------------------------------------
# Lifecycle: CANCELLED is never touched
# ---------------------------------------------------------------------------


def test_cancelled_never_touched() -> None:
    """CANCELLED lifecycle is never changed even with payments (shouldn't happen
    in practice due to D7 guards, but engine itself must not touch it)."""
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [_P("100.000")],
        InvoiceStatus.CANCELLED,
    )
    assert state.paid_status == InvoicePaidStatus.PAID
    assert state.new_status == InvoiceStatus.CANCELLED  # untouched


# ---------------------------------------------------------------------------
# due_amount is always >= 0 (overpayment would be caught by the service guard)
# ---------------------------------------------------------------------------


def test_due_amount_is_zero_not_negative_on_exact_pay() -> None:
    state = recompute_payment_state(
        Decimal("50.000"),
        Decimal("50.000"),
        [_P("50.000")],
        InvoiceStatus.SENT,
    )
    assert state.due_amount == Decimal("0")
    assert state.due_amount >= Decimal("0")


def test_base_amounts_mirror_amounts_d2() -> None:
    """In D2 (single base currency), base_amount == amount for each payment."""
    state = recompute_payment_state(
        Decimal("200.000"),
        Decimal("200.000"),
        [_P("100.000", "100.000"), _P("50.000", "50.000")],
        InvoiceStatus.SENT,
    )
    assert state.paid_total == state.base_paid_total
    assert state.due_amount == state.base_due_amount


# ---------------------------------------------------------------------------
# COMPLETED → SENT retrograde when partially paid
# ---------------------------------------------------------------------------


def test_completed_reverts_to_sent_when_underpaid() -> None:
    """A COMPLETED invoice becomes SENT again when not fully paid."""
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [_P("90.000")],
        InvoiceStatus.COMPLETED,
    )
    assert state.paid_status == InvoicePaidStatus.PARTIALLY_PAID
    assert state.new_status == InvoiceStatus.SENT


def test_completed_stays_completed_when_fully_paid() -> None:
    """COMPLETED stays COMPLETED when already fully paid (idempotent)."""
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [_P("100.000")],
        InvoiceStatus.COMPLETED,
    )
    assert state.paid_status == InvoicePaidStatus.PAID
    assert state.new_status == InvoiceStatus.COMPLETED


def test_completed_reverts_to_sent_when_unpaid() -> None:
    """COMPLETED with zero payments reverts to SENT (edge case)."""
    state = recompute_payment_state(
        Decimal("100.000"),
        Decimal("100.000"),
        [],
        InvoiceStatus.COMPLETED,
    )
    assert state.paid_status == InvoicePaidStatus.UNPAID
    assert state.new_status == InvoiceStatus.SENT


# ---------------------------------------------------------------------------
# quantize_money input contract (D9)
# ---------------------------------------------------------------------------


def test_quantize_money_more_than_3dp_rounds_half_up() -> None:
    """quantize_money must round >3-dp inputs using ROUND_HALF_UP (D9).

    This verifies the input-quantisation contract used in record_payment:
    the amount stored in DB (NUMERIC 18,3) must equal quantize_money(raw_input).
    """
    assert quantize_money(Decimal("33.3336")) == Decimal("33.334")
    assert quantize_money(Decimal("33.3334")) == Decimal("33.333")
    assert quantize_money(Decimal("120.9996")) == Decimal("121.000")
    assert quantize_money(Decimal("120.9994")) == Decimal("120.999")


def test_quantize_money_already_3dp_is_identity() -> None:
    """A value already at 3 dp must be returned unchanged."""
    assert quantize_money(Decimal("50.000")) == Decimal("50.000")
    assert quantize_money(Decimal("121.000")) == Decimal("121.000")


def test_recompute_with_quantised_amount_detects_full_payment() -> None:
    """Simulate the F1 boundary: raw input 120.9996 → quantised 121.000.

    The engine must see PAID / due=0 when handed the quantised value, proving
    that pre-quantisation in record_payment prevents the status-stuck bug.
    """
    raw = Decimal("120.9996")
    amt = quantize_money(raw)  # → 121.000

    state = recompute_payment_state(
        Decimal("121.000"),
        Decimal("121.000"),
        [_P(str(amt))],
        InvoiceStatus.SENT,
    )
    assert state.paid_status == InvoicePaidStatus.PAID
    assert state.due_amount == Decimal("0")
    assert state.new_status == InvoiceStatus.COMPLETED


# ---------------------------------------------------------------------------
# Quote-deposit VAT allocation (M11.5)
# ---------------------------------------------------------------------------


def _tax_bucket(
    order: int,
    percent: str,
    taxable: str,
    vat: str,
) -> TaxBucketSnapshot:
    return TaxBucketSnapshot(
        bucket_key=f"bucket-{order}",
        sort_order=order,
        vat_rate_id=uuid.UUID(int=order + 1),
        vat_rate_label=f"VAT {percent}%",
        vat_rate_percent=Decimal(percent),
        vat_treatment_code="NL_DOMESTIC",
        vat_treatment_effect="APPLY_RATE",
        vat_treatment_requires_icp=False,
        taxable_amount=Decimal(taxable),
        vat_amount=Decimal(vat),
    )


def test_quote_payment_allocation_mixed_rates_20_50_30_is_exact() -> None:
    buckets = [
        _tax_bucket(0, "21", "100.00", "21.00"),
        _tax_bucket(1, "9", "100.00", "9.00"),
    ]
    allocations = allocate_quote_payment_taxes(
        buckets,
        [Decimal("46.00"), Decimal("115.00"), Decimal("69.00")],
    )

    for expected, payment_rows in zip(
        (Decimal("46.00"), Decimal("115.00"), Decimal("69.00")),
        allocations,
        strict=True,
    ):
        assert sum((row.gross_amount for row in payment_rows), Decimal("0")) == expected
        assert all(row.taxable_amount >= 0 for row in payment_rows)
        assert all(row.vat_amount >= 0 for row in payment_rows)

    for bucket_index, bucket in enumerate(buckets):
        assert sum(
            (payment[bucket_index].taxable_amount for payment in allocations),
            Decimal("0"),
        ) == bucket.taxable_amount
        assert sum(
            (payment[bucket_index].vat_amount for payment in allocations),
            Decimal("0"),
        ) == bucket.vat_amount


def test_quote_payment_allocation_cent_remainder_is_stable() -> None:
    buckets = [
        _tax_bucket(0, "21", "0.03", "0.01"),
        _tax_bucket(1, "0", "0.02", "0.00"),
    ]
    first = allocate_quote_payment_taxes(
        buckets,
        [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")],
    )
    second = allocate_quote_payment_taxes(
        buckets,
        [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")],
    )

    assert first == second
    assert [
        sum((row.gross_amount for row in payment), Decimal("0"))
        for payment in first
    ] == [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")]


def test_quote_payment_allocation_zero_rate_full_payment() -> None:
    bucket = _tax_bucket(0, "0", "42.10", "0.00")
    allocations = allocate_quote_payment_taxes([bucket], [Decimal("42.10")])
    assert allocations[0][0].taxable_amount == Decimal("42.10")
    assert allocations[0][0].vat_amount == Decimal("0.00")
    assert allocations[0][0].gross_amount == Decimal("42.10")


def test_payment_tax_bucket_key_uses_only_immutable_snapshots() -> None:
    """Deleting a VAT-rate FK cannot change the recognised bucket identity."""
    assert tax_bucket_key(
        Decimal("21.000"), "NL_DOMESTIC", "APPLY_RATE", False
    ) == "NL_DOMESTIC|APPLY_RATE|0|21.000"


def _payment_tax(percent: str, taxable: str, vat: str) -> SimpleNamespace:
    gross = Decimal(taxable) + Decimal(vat)
    return SimpleNamespace(
        vat_rate_id=None,
        vat_rate_label=f"VAT {percent}%",
        vat_rate_percent=Decimal(percent),
        taxable_amount=Decimal(taxable),
        vat_amount=Decimal(vat),
        gross_amount=gross,
        base_taxable_amount=Decimal(taxable),
        base_vat_amount=Decimal(vat),
        base_gross_amount=gross,
    )


def _payment_read_stub(
    *, quote_id: uuid.UUID | None, direction: PaymentDirection, taxes: list[SimpleNamespace]
) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_id=None,
        quote_id=quote_id,
        credit_note_id=None,
        direction=direction.value,
        payment_date=date(2026, 9, 9),
        amount=sum((tax.gross_amount for tax in taxes), Decimal("0")),
        base_amount=sum((tax.base_gross_amount for tax in taxes), Decimal("0")),
        currency="EUR",
        payment_method_id=None,
        payment_method_name=None,
        reference=None,
        note=None,
        created_at=now,
        updated_at=now,
        taxes=taxes,
    )


def test_quote_deposit_read_has_authoritative_mixed_rate_vat_totals() -> None:
    payment = _payment_read_stub(
        quote_id=uuid.uuid4(),
        direction=PaymentDirection.INCOMING,
        taxes=[_payment_tax("21", "100.00", "21.00"), _payment_tax("9", "50.00", "4.50")],
    )

    read = _payment_to_read(payment, invoice_number=None, quote_number="Q-1")

    assert read.deposit_taxable_amount == Decimal("150.00")
    assert read.deposit_vat_amount == Decimal("25.50")
    assert [(row.vat_rate_percent, row.vat_amount) for row in read.tax_breakdown] == [
        (Decimal("21"), Decimal("21.00")),
        (Decimal("9"), Decimal("4.50")),
    ]


def test_quote_deposit_read_preserves_zero_percent_vat_as_applicable() -> None:
    payment = _payment_read_stub(
        quote_id=uuid.uuid4(),
        direction=PaymentDirection.INCOMING,
        taxes=[_payment_tax("0", "42.10", "0.00")],
    )

    read = _payment_to_read(payment, invoice_number=None, quote_number="Q-1")

    assert read.deposit_taxable_amount == Decimal("42.10")
    assert read.deposit_vat_amount == Decimal("0.00")


def test_global_payment_row_uses_the_same_deposit_snapshot_totals() -> None:
    payment = _payment_read_stub(
        quote_id=uuid.uuid4(),
        direction=PaymentDirection.INCOMING,
        taxes=[_payment_tax("21", "100.00", "21.00"), _payment_tax("0", "29.00", "0.00")],
    )

    row = _payment_to_list_item(
        payment,
        invoice_number=None,
        quote_number="Q-1",
        credit_note_number=None,
        customer_id=uuid.uuid4(),
        customer_name="Customer",
    )

    assert row.deposit_taxable_amount == Decimal("129.00")
    assert row.deposit_vat_amount == Decimal("21.00")


def test_non_deposit_and_refund_reads_have_no_vat_split() -> None:
    taxes = [_payment_tax("21", "100.00", "21.00")]
    invoice_payment = _payment_read_stub(
        quote_id=None, direction=PaymentDirection.INCOMING, taxes=taxes
    )
    refund = _payment_read_stub(
        quote_id=uuid.uuid4(), direction=PaymentDirection.REFUND, taxes=taxes
    )

    for payment in (invoice_payment, refund):
        read = _payment_to_read(payment, invoice_number="INV-1", quote_number=None)
        assert read.deposit_taxable_amount is None
        assert read.deposit_vat_amount is None


def test_quote_payment_allocation_rejects_overpayment() -> None:
    bucket = _tax_bucket(0, "21", "100.00", "21.00")
    with pytest.raises(ValueError, match="exceeds the outstanding"):
        allocate_quote_payment_taxes([bucket], [Decimal("121.01")])
