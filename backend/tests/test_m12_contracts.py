"""Pure M12 Step 1 command-contract guards."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from jai.schemas.invoice import (
    AdvanceCalculationRequest,
    CreditCalculationLineInput,
    CreditCalculationRequest,
    InvoiceStatusWrite,
    InvoiceWrite,
)
from jai.schemas.payment import PaymentInput
from jai.schemas.setting import CreditNumberingConfig, InvoiceNumberingConfig


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


@pytest.mark.parametrize("config", [InvoiceNumberingConfig, CreditNumberingConfig])
def test_numbering_settings_reject_postgres_bigint_overflow(
    config: type[InvoiceNumberingConfig],
) -> None:
    with pytest.raises(ValidationError):
        config(sequence_start=9_223_372_036_854_775_808)
