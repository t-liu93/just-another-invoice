"""Focused M12 Step 7 settlement-unit coverage (no database required)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from httpx import AsyncClient

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.main import app
from jai.models._enums import InvoiceStatus, PaymentDirection
from jai.models.invoice import Invoice
from jai.models.payment import Payment
from jai.services.payment import _settle_refund_chain


async def test_refund_and_payment_validation_errors_have_typed_contracts(
    client: AsyncClient,
) -> None:
    """Step 7 routes never leak FastAPI's list-shaped validation detail."""
    async def owner_user() -> object:
        return SimpleNamespace(id=uuid.uuid4(), company_id=uuid.uuid4(), role="owner")

    app.dependency_overrides[current_mfa_user] = owner_user

    async def no_database_session() -> object:
        yield SimpleNamespace()

    app.dependency_overrides[get_session] = no_database_session
    try:
        valid_id = str(uuid.uuid4())
        invalid_body = {"payment_date": "not-a-date", "amount": "0"}
        checks = [
            (
                await client.get("/api/v1/credit-notes/not-a-uuid/refunds"),
                "REFUND_INVALID_INPUT",
            ),
            (
                await client.post(
                    "/api/v1/credit-notes/not-a-uuid/refunds", json=invalid_body
                ),
                "REFUND_INVALID_INPUT",
            ),
            (
                await client.post(
                    f"/api/v1/credit-notes/{valid_id}/refunds", json=invalid_body
                ),
                "REFUND_INVALID_INPUT",
            ),
            (
                await client.post(
                    f"/api/v1/credit-notes/{valid_id}/refunds",
                    json={
                        "payment_date": "2026-02-04",
                        "amount": "1",
                        "payment_method_id": "not-a-uuid",
                    },
                ),
                "REFUND_INVALID_INPUT",
            ),
            (
                await client.put("/api/v1/payments/not-a-uuid", json=invalid_body),
                "PAYMENT_INVALID_INPUT",
            ),
            # The generic route is also the existing Incoming-payment edit API.
            (
                await client.put(f"/api/v1/payments/{valid_id}", json=invalid_body),
                "PAYMENT_INVALID_INPUT",
            ),
            (
                await client.put(
                    f"/api/v1/payments/{valid_id}",
                    json={
                        "payment_date": "2026-02-04",
                        "amount": "1",
                        "payment_method_id": "not-a-uuid",
                    },
                ),
                "PAYMENT_INVALID_INPUT",
            ),
            (
                await client.delete("/api/v1/payments/not-a-uuid"),
                "PAYMENT_INVALID_INPUT",
            ),
        ]
        for response, code in checks:
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == {
                "code": code,
                "message": "The payment input is invalid.",
            }

        # The narrow mapping must not change unrelated route validation.
        unrelated = await client.get("/api/v1/invoices/not-a-uuid")
        assert unrelated.status_code == 422
        assert isinstance(unrelated.json()["detail"], list)
    finally:
        app.dependency_overrides.pop(current_mfa_user, None)
        app.dependency_overrides.pop(get_session, None)


def test_refund_and_payment_validation_openapi_matches_runtime_contract() -> None:
    """The generated client sees the same typed error envelope as callers."""
    openapi = app.openapi()
    paths = openapi["paths"]
    for path, method in (
        ("/api/v1/credit-notes/{credit_note_id}/refunds", "get"),
        ("/api/v1/credit-notes/{credit_note_id}/refunds", "post"),
        ("/api/v1/payments/{payment_id}", "put"),
        ("/api/v1/payments/{payment_id}", "delete"),
    ):
        schema = paths[path][method]["responses"]["422"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/PaymentInputErrorResponse"}
    detail = openapi["components"]["schemas"]["PaymentInputErrorResponse"]
    assert detail["properties"]["detail"]["$ref"] == "#/components/schemas/PaymentInputErrorDetail"


def _doc(
    *,
    payable: str,
    credited: str = "0",
    invoice_date: date = date(2026, 6, 1),
    issued_at: datetime = datetime(2026, 6, 1, tzinfo=UTC),
    base_payable: str | None = None,
    base_credited: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), invoice_date=invoice_date, total_incl_vat=Decimal(payable),
        payable_before_payments=Decimal(payable), credited_total=Decimal(credited),
        issued_at=issued_at,
        status=InvoiceStatus.SENT,
        base_total_incl_vat=Decimal(base_payable or payable),
        base_payable_before_payments=Decimal(base_payable or payable),
        base_credited_total=Decimal(base_credited or credited),
    )


def _cash(
    *,
    amount: str,
    direction: PaymentDirection,
    invoice_id: uuid.UUID | None = None,
    credit_id: uuid.UUID | None = None,
    base_amount: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        amount=Decimal(amount), base_amount=Decimal(base_amount or amount),
        direction=direction, invoice_id=invoice_id,
        quote_id=None, credit_note_id=credit_id,
    )


def test_entitlement_is_source_local_but_never_exceeds_chain_cash() -> None:
    source_a = _doc(payable="100", credited="100")
    source_b = _doc(payable="100", credited="100")
    credit_a_early = _doc(payable="60", invoice_date=date(2026, 6, 2))
    credit_a_late = _doc(payable="40", invoice_date=date(2026, 6, 3))
    credit_b = _doc(payable="100", invoice_date=date(2026, 6, 2))
    payments = [
        _cash(amount="100", direction=PaymentDirection.INCOMING, invoice_id=source_a.id),
        _cash(amount="100", direction=PaymentDirection.INCOMING, invoice_id=source_b.id),
    ]

    available = _settle_refund_chain(
        source=cast(Invoice, source_a),
        chain_sources=cast(list[Invoice], [source_a, source_b]),
        credits=cast(list[Invoice], [credit_a_late, credit_b, credit_a_early]),
        credit_source_ids={
            credit_a_early.id: source_a.id,
            credit_a_late.id: source_a.id,
            credit_b.id: source_b.id,
        },
        payments=cast(list[Payment], payments),
        quote=None,
    )

    assert available[credit_a_early.id] == Decimal("60")
    assert available[credit_a_late.id] == Decimal("40")
    assert available[credit_b.id] == Decimal("100")
    assert source_a.refund_due_amount == Decimal("100")
    assert source_b.refund_due_amount == Decimal("100")


def test_refund_larger_than_full_chain_cash_is_rejected() -> None:
    source = _doc(payable="100", credited="100")
    credit = _doc(payable="100")
    with pytest.raises(ValueError, match="entitlement"):
        _settle_refund_chain(
            source=cast(Invoice, source),
            chain_sources=cast(list[Invoice], [source]),
            credits=cast(list[Invoice], [credit]),
            credit_source_ids={credit.id: source.id},
            payments=cast(list[Payment], [
                _cash(amount="100", direction=PaymentDirection.INCOMING, invoice_id=source.id),
                _cash(amount="101", direction=PaymentDirection.REFUND, credit_id=credit.id),
            ]),
            quote=None,
        )


def test_existing_refund_cannot_be_backed_by_another_source_cash() -> None:
    source_without_cash = _doc(
        payable="100", credited="100", base_payable="125", base_credited="125"
    )
    source_with_cash = _doc(
        payable="100", credited="100", base_payable="125", base_credited="125"
    )
    credit_without_cash = _doc(
        payable="100", base_payable="125", issued_at=datetime(2026, 6, 2, tzinfo=UTC)
    )

    with pytest.raises(ValueError, match="source-local"):
        _settle_refund_chain(
            source=cast(Invoice, source_without_cash),
            chain_sources=cast(list[Invoice], [source_without_cash, source_with_cash]),
            credits=cast(list[Invoice], [credit_without_cash]),
            credit_source_ids={credit_without_cash.id: source_without_cash.id},
            payments=cast(
                list[Payment],
                [
                    _cash(
                        amount="100", base_amount="125",
                        direction=PaymentDirection.INCOMING,
                        invoice_id=source_with_cash.id,
                    ),
                    _cash(
                        amount="100", base_amount="125",
                        direction=PaymentDirection.REFUND,
                        credit_id=credit_without_cash.id,
                    ),
                ],
            ),
            quote=None,
        )


def test_existing_refunds_remain_bound_to_issued_credit_order() -> None:
    source = _doc(payable="100", credited="100")
    first = _doc(payable="50", issued_at=datetime(2026, 6, 2, tzinfo=UTC))
    second = _doc(payable="50", issued_at=datetime(2026, 6, 3, tzinfo=UTC))

    with pytest.raises(ValueError, match="issued-order"):
        _settle_refund_chain(
            source=cast(Invoice, source),
            chain_sources=cast(list[Invoice], [source]),
            credits=cast(list[Invoice], [second, first]),
            credit_source_ids={first.id: source.id, second.id: source.id},
            payments=cast(
                list[Payment],
                [
                    _cash(
                        amount="60", direction=PaymentDirection.INCOMING,
                        invoice_id=source.id,
                    ),
                    _cash(
                        amount="30", direction=PaymentDirection.REFUND,
                        credit_id=first.id,
                    ),
                    _cash(
                        amount="30", direction=PaymentDirection.REFUND,
                        credit_id=second.id,
                    ),
                ],
            ),
            quote=None,
        )


def test_credit_entitlement_order_uses_issue_timestamp_then_id_not_document_date() -> None:
    source = _doc(payable="100", credited="100")
    issued_first = _doc(
        payable="60",
        invoice_date=date(2026, 6, 30),
        issued_at=datetime(2026, 6, 2, 8, tzinfo=UTC),
    )
    issued_second = _doc(
        payable="60",
        invoice_date=date(2026, 6, 2),
        issued_at=datetime(2026, 6, 3, 8, tzinfo=UTC),
    )

    available = _settle_refund_chain(
        source=cast(Invoice, source),
        chain_sources=cast(list[Invoice], [source]),
        credits=cast(list[Invoice], [issued_second, issued_first]),
        credit_source_ids={issued_first.id: source.id, issued_second.id: source.id},
        payments=cast(
            list[Payment],
            [_cash(amount="60", direction=PaymentDirection.INCOMING, invoice_id=source.id)],
        ),
        quote=None,
    )

    assert available[issued_first.id] == Decimal("60")
    assert available[issued_second.id] == Decimal("0")


def test_base_settlement_uses_persisted_base_snapshots_independently() -> None:
    source = _doc(
        payable="100", credited="100", base_payable="125", base_credited="125"
    )
    credit = _doc(payable="100", base_payable="125")

    _settle_refund_chain(
        source=cast(Invoice, source),
        chain_sources=cast(list[Invoice], [source]),
        credits=cast(list[Invoice], [credit]),
        credit_source_ids={credit.id: source.id},
        payments=cast(
            list[Payment],
            [
                _cash(
                    amount="80",
                    base_amount="100",
                    direction=PaymentDirection.INCOMING,
                    invoice_id=source.id,
                )
            ],
        ),
        quote=None,
    )

    assert source.refund_due_amount == Decimal("80")
    assert source.base_refund_due_amount == Decimal("100")
    assert credit.refund_due_amount == Decimal("80")
    assert credit.base_refund_due_amount == Decimal("100")


def test_cross_source_entitlement_uses_global_issue_order_for_txn_and_base() -> None:
    source_early = _doc(
        payable="30", credited="30", base_payable="40", base_credited="40"
    )
    source_late = _doc(
        payable="30", credited="30", base_payable="40", base_credited="40"
    )
    deficit = _doc(
        payable="30", credited="0", base_payable="40", base_credited="0"
    )
    issued_early = _doc(
        payable="30",
        base_payable="40",
        issued_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    issued_late = _doc(
        payable="30",
        base_payable="40",
        issued_at=datetime(2026, 6, 3, tzinfo=UTC),
    )

    result = _settle_refund_chain(
        source=cast(Invoice, source_early),
        chain_sources=cast(list[Invoice], [source_late, deficit, source_early]),
        credits=cast(list[Invoice], [issued_late, issued_early]),
        credit_source_ids={
            issued_early.id: source_early.id,
            issued_late.id: source_late.id,
        },
        payments=cast(
            list[Payment],
            [
                _cash(
                    amount="30",
                    base_amount="40",
                    direction=PaymentDirection.INCOMING,
                    invoice_id=source_early.id,
                ),
                _cash(
                    amount="30",
                    base_amount="40",
                    direction=PaymentDirection.INCOMING,
                    invoice_id=source_late.id,
                ),
            ],
        ),
        quote=None,
    )

    assert result[issued_early.id] == Decimal("30")
    assert result[issued_late.id] == Decimal("0")
    assert result.base_available_by_credit[issued_early.id] == Decimal("40")
    assert result.base_available_by_credit[issued_late.id] == Decimal("0")


def test_global_order_skips_earlier_credit_without_source_local_cash() -> None:
    source_without_cash = _doc(payable="30", credited="30")
    source_with_cash = _doc(payable="30", credited="30")
    skipped = _doc(
        payable="30", issued_at=datetime(2026, 6, 2, tzinfo=UTC)
    )
    eligible = _doc(
        payable="30", issued_at=datetime(2026, 6, 3, tzinfo=UTC)
    )

    result = _settle_refund_chain(
        source=cast(Invoice, source_with_cash),
        chain_sources=cast(
            list[Invoice], [source_without_cash, source_with_cash]
        ),
        credits=cast(list[Invoice], [eligible, skipped]),
        credit_source_ids={
            skipped.id: source_without_cash.id,
            eligible.id: source_with_cash.id,
        },
        payments=cast(
            list[Payment],
            [
                _cash(
                    amount="30",
                    direction=PaymentDirection.INCOMING,
                    invoice_id=source_with_cash.id,
                )
            ],
        ),
        quote=None,
    )

    assert result[skipped.id] == Decimal("0")
    assert result[eligible.id] == Decimal("30")


def test_later_credit_receives_only_remainder_after_smaller_early_entitlement() -> None:
    source_early = _doc(payable="30", credited="10")
    source_late = _doc(payable="30", credited="30")
    issued_early = _doc(
        payable="10", issued_at=datetime(2026, 6, 2, tzinfo=UTC)
    )
    issued_late = _doc(
        payable="30", issued_at=datetime(2026, 6, 3, tzinfo=UTC)
    )

    result = _settle_refund_chain(
        source=cast(Invoice, source_early),
        chain_sources=cast(list[Invoice], [source_late, source_early]),
        credits=cast(list[Invoice], [issued_late, issued_early]),
        credit_source_ids={
            issued_early.id: source_early.id,
            issued_late.id: source_late.id,
        },
        payments=cast(
            list[Payment],
            [
                _cash(
                    amount="30",
                    direction=PaymentDirection.INCOMING,
                    invoice_id=source_early.id,
                ),
                _cash(
                    amount="30",
                    direction=PaymentDirection.INCOMING,
                    invoice_id=source_late.id,
                ),
            ],
        ),
        quote=None,
    )

    assert result[issued_early.id] == Decimal("10")
    assert result[issued_late.id] == Decimal("30")
