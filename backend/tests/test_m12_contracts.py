"""Pure M12 Step 1 command-contract guards."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from jai.models._enums import DocumentChainEventType
from jai.schemas.invoice import (
    AdvanceCalculationRequest,
    CreditCalculationLineInput,
    CreditCalculationRequest,
    InvoiceStatusWrite,
    InvoiceWrite,
)
from jai.schemas.payment import PaymentInput
from jai.schemas.setting import CreditNumberingConfig, InvoiceNumberingConfig
from jai.services.document_chain import _safe_metadata


def test_generic_command_shapes_forbid_m12_intent() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        InvoiceWrite.model_validate(
            {
                "customer_id": str(uuid.uuid4()),
                "invoice_date": "2026-08-28",
                "tax_mode": "LINE",
                "lines": [],
                "document_kind": "CREDIT_NOTE",
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        InvoiceStatusWrite.model_validate(
            {"status": "SENT", "source_invoice_id": str(uuid.uuid4())}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PaymentInput.model_validate(
            {"payment_date": "2026-08-28", "amount": "1", "direction": "REFUND"}
        )


def test_dedicated_command_shapes_forbid_frontend_money_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AdvanceCalculationRequest.model_validate(
            {"input_mode": "PERCENTAGE", "percentage": "20", "net_amount": "999"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CreditCalculationLineInput.model_validate(
            {
                "source_basis_line_id": str(uuid.uuid4()),
                "input_mode": "GROSS_AMOUNT",
                "gross_amount": "1",
                "vat_amount": "999",
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CreditCalculationRequest.model_validate({"full_remaining": True, "net_amount": "999"})


def test_credit_selection_is_strict_xor_and_cannot_repeat_a_basis_line() -> None:
    basis_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        CreditCalculationRequest.model_validate({"full_remaining": False, "lines": []})
    with pytest.raises(ValidationError):
        CreditCalculationRequest.model_validate(
            {
                "full_remaining": True,
                "lines": [
                    {
                        "source_basis_line_id": str(basis_id),
                        "input_mode": "QUANTITY",
                        "quantity": "1",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        CreditCalculationRequest.model_validate(
            {
                "full_remaining": False,
                "lines": [
                    {
                        "source_basis_line_id": str(basis_id),
                        "input_mode": "GROSS_AMOUNT",
                        "gross_amount": "1",
                    },
                    {
                        "source_basis_line_id": str(basis_id),
                        "input_mode": "GROSS_AMOUNT",
                        "gross_amount": "1",
                    },
                ],
            }
        )


@pytest.mark.parametrize("config", [InvoiceNumberingConfig, CreditNumberingConfig])
def test_numbering_settings_reject_postgres_bigint_overflow(
    config: type[InvoiceNumberingConfig],
) -> None:
    with pytest.raises(ValidationError):
        config(sequence_start=9_223_372_036_854_775_808)


def test_chain_event_metadata_is_closed_typed_and_non_renderable() -> None:
    payment_id = uuid.uuid4()
    assert _safe_metadata(
        DocumentChainEventType.INVOICE_PAYMENT_CREATED,
        {"payment_id": payment_id, "amount": Decimal("12.340")},
    ) == {"payment_id": str(payment_id), "amount": "12.340"}
    assert _safe_metadata(DocumentChainEventType.MODE_LOCKED, {"mode": "DIRECT_INVOICE"}) == {
        "mode": "DIRECT_INVOICE"
    }
    assert _safe_metadata(
        DocumentChainEventType.INVOICE_ISSUED,
        {"document_kind": "STANDARD", "status": "SENT"},
    ) == {"document_kind": "STANDARD", "status": "SENT"}
    for metadata in (
        {"smtp_password": "not-allowed"},
        {"payment_id": "<b>unsafe</b>", "amount": Decimal("1")},
        {"payment_id": str(uuid.uuid4()), "amount": {"nested": "no"}},  # type: ignore[dict-item]
        {"payment_id": "x" * 129, "amount": Decimal("1")},
        {"payment_id": 7, "amount": "not-a-decimal"},
        {"payment_id": str(uuid.uuid4()), "amount": Decimal("NaN")},
    ):
        with pytest.raises(ValueError):
            _safe_metadata(DocumentChainEventType.INVOICE_PAYMENT_CREATED, metadata)
    for event_type, metadata in (
        (DocumentChainEventType.MODE_LOCKED, {"mode": "NOT_A_MODE"}),
        (DocumentChainEventType.INVOICE_ISSUED, {}),
        (DocumentChainEventType.INVOICE_CREATED, {"document_kind": "STANDARD", "html": "<b>x</b>"}),
    ):
        with pytest.raises(ValueError):
            _safe_metadata(event_type, metadata)
