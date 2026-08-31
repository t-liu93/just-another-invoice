"""Pure action projection parity guards for M12 Step 10."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from jai.models._enums import InvoiceDocumentKind, InvoiceStatus, QuoteSettlementMode
from jai.services.document_actions import credit_note_eligibility, followup_eligibility


def _invoice(
    kind: InvoiceDocumentKind, status: InvoiceStatus, *, credited: str = "0", payable: str = "100"
) -> SimpleNamespace:
    return SimpleNamespace(
        document_kind=kind,
        status=status,
        credited_total=Decimal(credited),
        payable_before_payments=Decimal(payable),
    )


@pytest.mark.parametrize(
    ("kind", "status", "credited", "final_draft", "available", "reason"),
    [
        (InvoiceDocumentKind.STANDARD, InvoiceStatus.SENT, "0", False, True, None),
        (
            InvoiceDocumentKind.STANDARD,
            InvoiceStatus.DRAFT,
            "0",
            False,
            False,
            "CREDIT_SOURCE_NOT_ISSUED",
        ),
        (
            InvoiceDocumentKind.CREDIT_NOTE,
            InvoiceStatus.SENT,
            "0",
            False,
            False,
            "CREDIT_OF_CREDIT",
        ),
        (InvoiceDocumentKind.ADVANCE, InvoiceStatus.SENT, "0", True, False, "FINAL_DRAFT_FREEZE"),
        (
            InvoiceDocumentKind.FINAL,
            InvoiceStatus.SENT,
            "100",
            False,
            False,
            "CREDIT_NO_REMAINING_BASIS",
        ),
    ],
)
def test_credit_projection_uses_command_stable_codes(
    kind, status, credited, final_draft, available, reason
) -> None:
    result = credit_note_eligibility(
        _invoice(kind, status, credited=credited), final_draft_exists=final_draft
    )
    assert (result.available, result.reason_code) == (available, reason)


def test_draft_credit_followup_uses_locked_command_code() -> None:
    replacement, compensation = followup_eligibility(
        _invoice(InvoiceDocumentKind.CREDIT_NOTE, InvoiceStatus.DRAFT),
        _invoice(InvoiceDocumentKind.STANDARD, InvoiceStatus.SENT),
        mode=QuoteSettlementMode.DIRECT_INVOICE,
        final_exists=False,
        open_advance_draft_exists=False,
        existing_followup=False,
    )
    assert (replacement.reason_code, compensation.reason_code) == (
        "CREDIT_NOT_ISSUED",
        "CREDIT_NOT_ISSUED",
    )
