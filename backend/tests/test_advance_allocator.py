"""Unit tests for the M12 Formal Advance integer-minor-unit allocator."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from jai.models._enums import AdvanceInputMode
from jai.schemas.invoice import AdvanceCalculationRequest
from jai.services.advance import (
    AdvanceBucket,
    AdvanceValidationError,
    _open_draft,
    _read_calculation,
    allocate_advance_gross,
    is_advance_draft_conflict,
    is_invoice_number_conflict,
    requested_advance_gross,
    subtract_exact_advance_credits,
)


def _bucket(net: str, vat: str) -> AdvanceBucket:
    return AdvanceBucket(uuid4(), "VAT", Decimal("21"), Decimal(net), Decimal(vat))


def test_percentage_is_applied_to_original_gross_and_rounded_once() -> None:
    request = AdvanceCalculationRequest(
        input_mode=AdvanceInputMode.PERCENTAGE, percentage=Decimal("20")
    )
    assert requested_advance_gross(request, Decimal("100.03")) == Decimal("20.01")


@pytest.mark.parametrize(
    "percentage, expected", [("20", "20.00"), ("50", "50.00"), ("30", "30.00")]
)
def test_percentages_use_original_total_not_remaining(percentage: str, expected: str) -> None:
    request = AdvanceCalculationRequest(
        input_mode=AdvanceInputMode.PERCENTAGE, percentage=Decimal(percentage)
    )
    assert requested_advance_gross(request, Decimal("100.00")) == Decimal(expected)


def test_mixed_vat_allocation_balances_each_bucket_and_document() -> None:
    buckets = [_bucket("21.00", "4.41"), _bucket("9.00", "0.81"), _bucket("10.00", "0.00")]
    allocated = allocate_advance_gross(buckets, Decimal("22.61"))
    assert sum((row.gross_amount for row in allocated), Decimal("0")) == Decimal("22.61")
    assert all(row.taxable_amount + row.vat_amount == row.gross_amount for row in allocated)


def test_cent_tail_uses_stable_largest_remainder() -> None:
    buckets = [_bucket("0.01", "0.00"), _bucket("0.01", "0.00"), _bucket("0.01", "0.00")]
    allocated = allocate_advance_gross(buckets, Decimal("0.02"))
    assert [row.gross_amount for row in allocated] == [
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.00"),
    ]


def test_cent_tail_uses_one_flat_taxable_vat_component_pass() -> None:
    """The immutable Advance VAT snapshot matches M11.5 component ordering."""
    vat_9 = AdvanceBucket(uuid4(), "Reduced", Decimal("9"), Decimal("0.01"), Decimal("0"))
    vat_21 = AdvanceBucket(uuid4(), "Standard", Decimal("21"), Decimal("0.04"), Decimal("0.01"))

    allocated = allocate_advance_gross([vat_9, vat_21], Decimal("0.04"))

    # [1, 0, 4, 1] cents with one flat largest-remainder pass is [1, 0, 3, 0].
    assert [(row.taxable_amount, row.vat_amount) for row in allocated] == [
        (Decimal("0.01"), Decimal("0.00")),
        (Decimal("0.03"), Decimal("0.00")),
    ]
    assert sum((row.vat_amount for row in allocated), Decimal("0")) == Decimal("0.00")


def test_mixed_21_9_0_components_close_exactly_after_20_50_30() -> None:
    """Repeated percentage-sized allocations consume every original component exactly."""
    original = [
        AdvanceBucket(uuid4(), "Zero", Decimal("0"), Decimal("0.05"), Decimal("0")),
        AdvanceBucket(uuid4(), "Reduced", Decimal("9"), Decimal("0.01"), Decimal("0")),
        AdvanceBucket(uuid4(), "Standard", Decimal("21"), Decimal("0.04"), Decimal("0.01")),
    ]
    remaining = original
    allocated: list[list[AdvanceBucket]] = []
    for gross in (Decimal("0.02"), Decimal("0.06"), Decimal("0.03")):
        part = allocate_advance_gross(remaining, gross)
        allocated.append(part)
        remaining = [
            AdvanceBucket(
                source.vat_rate_id,
                source.vat_rate_label,
                source.vat_rate_percent,
                source.taxable_amount - used.taxable_amount,
                source.vat_amount - used.vat_amount,
            )
            for source, used in zip(remaining, part, strict=True)
        ]

    assert all(
        bucket.taxable_amount == bucket.vat_amount == Decimal("0") for bucket in remaining
    )
    for index, source in enumerate(original):
        assert sum((part[index].taxable_amount for part in allocated), Decimal("0")) == (
            source.taxable_amount
        )
        assert sum((part[index].vat_amount for part in allocated), Decimal("0")) == (
            source.vat_amount
        )


class _PostgresError(Exception):
    def __init__(self, sqlstate: str, constraint_name: str) -> None:
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity_error(sqlstate: str, constraint_name: str) -> IntegrityError:
    return IntegrityError("statement", {}, _PostgresError(sqlstate, constraint_name))


def test_constraint_classification_is_exact_and_preserves_other_integrity_errors() -> None:
    partial_unique = _integrity_error("23505", "uq_invoice_advance_quote_draft")
    check = _integrity_error("23514", "ck_unrelated")
    foreign_key = _integrity_error("23503", "fk_unrelated")
    invoice_number = _integrity_error("23505", "uq_invoice_company_number")

    assert is_advance_draft_conflict(partial_unique)
    assert not is_invoice_number_conflict(partial_unique)
    assert not is_advance_draft_conflict(check)
    assert not is_advance_draft_conflict(foreign_key)
    assert not is_advance_draft_conflict(invoice_number)
    assert is_invoice_number_conflict(invoice_number)


def test_allocator_rejects_capacity_overrun() -> None:
    with pytest.raises(ValueError, match="exceeds remaining Quote capacity"):
        allocate_advance_gross([_bucket("10.00", "2.10")], Decimal("12.11"))


def test_minor_unit_rounded_zero_is_rejected_by_shared_calculation_gate() -> None:
    request = AdvanceCalculationRequest(
        input_mode=AdvanceInputMode.GROSS_AMOUNT, gross_amount=Decimal("0.001")
    )
    with pytest.raises(AdvanceValidationError) as error:
        _read_calculation(request, Decimal("100.00"), [_bucket("100.00", "0.00")])
    assert error.value.code == "ADVANCE_AMOUNT_TOO_SMALL"


def test_exact_credit_reopens_only_its_real_source_vat_bucket() -> None:
    vat_21 = _bucket("100.00", "21.00")
    vat_9 = AdvanceBucket(
        uuid4(), "Reduced", Decimal("9"), Decimal("100.00"), Decimal("9.00")
    )
    credit_9 = AdvanceBucket(
        vat_9.vat_rate_id, "Reduced", Decimal("9"), Decimal("20.00"), Decimal("1.80")
    )
    remaining = subtract_exact_advance_credits([vat_21, vat_9], [credit_9])
    assert remaining[0] == vat_21
    assert remaining[1].taxable_amount == Decimal("80.00")
    assert remaining[1].vat_amount == Decimal("7.20")


def test_open_advance_draft_query_locks_only_for_the_create_path() -> None:
    class _Result:
        def scalar_one_or_none(self) -> None:
            return None

    class _Session:
        statement: str

        async def execute(self, statement: object) -> _Result:
            self.statement = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
            return _Result()

    read_session = _Session()
    asyncio.run(_open_draft(read_session, uuid4(), lock=False))
    assert "FOR UPDATE" not in read_session.statement

    create_session = _Session()
    asyncio.run(_open_draft(create_session, uuid4(), lock=True))
    assert "FOR UPDATE" in create_session.statement
