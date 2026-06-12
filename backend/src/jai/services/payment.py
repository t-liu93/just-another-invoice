"""Payment service – record, list, read, update, and delete payments (M7).

Public API
----------
- ``recompute_payment_state``  – pure function: compute paid/due totals + lifecycle
                                  status from a list of payment amounts.
- ``record_payment``           – validate guards, persist payment, recompute + write-back.
- ``list_invoice_payments``    – return InvoicePaymentsResponse for all payments on an invoice.
- ``get_payment``              – fetch a single payment by id + company_id (404 guard).
- ``update_payment``           – edit a payment (amount/date/method/reference/note), recompute.
- ``delete_payment``           – delete a payment and recompute the invoice state.

Red-line compliance
-------------------
1. All money logic is in ``recompute_payment_state`` (pure function, D9).
2. company_id is always injected by the service; front-end never provides it.
3. No manual cascade deletes – DB ON DELETE CASCADE handles payment rows.
   FK constraints prevent orphaned payments.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import InvoicePaidStatus, InvoiceStatus
from jai.models.dictionary import PaymentMethod
from jai.models.invoice import Invoice
from jai.models.payment import Payment
from jai.schemas.payment import InvoicePaymentsResponse, PaymentInput, PaymentRead
from jai.services.money import quantize_money

# ---------------------------------------------------------------------------
# Pure recomputation engine (no DB access – independently unit-testable)
# ---------------------------------------------------------------------------


class _PaymentLike(Protocol):
    """Structural protocol: anything with an ``amount`` and ``base_amount``."""

    amount: object  # will be coerced to Decimal
    base_amount: object


@dataclass
class PaymentState:
    """Result of ``recompute_payment_state``."""

    paid_total: Decimal
    base_paid_total: Decimal
    due_amount: Decimal
    base_due_amount: Decimal
    paid_status: InvoicePaidStatus
    new_status: InvoiceStatus


def recompute_payment_state(
    total_incl_vat: Decimal,
    base_total_incl_vat: Decimal,
    payments: list[_PaymentLike],
    current_status: InvoiceStatus,
) -> PaymentState:
    """Pure function: recompute payment state from a list of payments.

    Rules (M7 §算钱与状态规则, D9):
    - paid_total = Σ payment.amount (each already quantised to 3 dp; sum is
      *not* re-quantised – D9 / no double-rounding).
    - due_amount = total_incl_vat − paid_total (same, no second quantise).
    - paid_status: PAID if due==0; UNPAID if paid==0; else PARTIALLY_PAID.
    - Lifecycle (D3):
        paid_status==PAID  and current_status==SENT  → COMPLETED
        paid_status!=PAID  and current_status==COMPLETED → SENT
        DRAFT / CANCELLED: untouched.

    This function is pure (no DB, no side-effects) and must remain so.
    """
    paid_total = sum(
        (Decimal(str(p.amount)) for p in payments),
        Decimal("0"),
    )
    base_paid_total = sum(
        (Decimal(str(p.base_amount)) for p in payments),
        Decimal("0"),
    )

    due_amount = total_incl_vat - paid_total
    base_due_amount = base_total_incl_vat - base_paid_total

    # Determine paid_status
    if due_amount == Decimal("0"):
        paid_status = InvoicePaidStatus.PAID
    elif paid_total == Decimal("0"):
        paid_status = InvoicePaidStatus.UNPAID
    else:
        paid_status = InvoicePaidStatus.PARTIALLY_PAID

    # Lifecycle transition (D3): only touch SENT/COMPLETED, never DRAFT/CANCELLED
    new_status = current_status
    if paid_status == InvoicePaidStatus.PAID and current_status == InvoiceStatus.SENT:
        new_status = InvoiceStatus.COMPLETED
    elif (
        paid_status != InvoicePaidStatus.PAID
        and current_status == InvoiceStatus.COMPLETED
    ):
        new_status = InvoiceStatus.SENT

    # Normalise scale to 3 dp on all four totals.  Each per-payment amount is
    # already quantised at input; their sum inherits that scale (≥ 1 payment).
    # The only case that produces scale=0 is an empty list (Decimal("0")); this
    # quantize call is a no-op for non-zero sums and restores "0.000" for the
    # zero case – consistent with the NUMERIC(18,3) columns in invoice.py (D9:
    # sum-level normalisation, never re-rounding individual payments).
    return PaymentState(
        paid_total=quantize_money(paid_total),
        base_paid_total=quantize_money(base_paid_total),
        due_amount=quantize_money(due_amount),
        base_due_amount=quantize_money(base_due_amount),
        paid_status=paid_status,
        new_status=new_status,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Invoice:
    """Load an invoice scoped to the company; raises ValueError (→ 404) if absent."""
    stmt = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.company_id == company_id,
    )
    result = await session.execute(stmt)
    inv = result.scalar_one_or_none()
    if inv is None:
        raise LookupError("Invoice not found.")
    return inv


async def _load_payments_for_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
) -> list[Payment]:
    """Load all payments for an invoice ordered by (payment_date, created_at)."""
    stmt = (
        select(Payment)
        .where(Payment.invoice_id == invoice_id)
        .order_by(Payment.payment_date, Payment.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _payment_to_read(p: Payment, invoice_number: str) -> PaymentRead:
    return PaymentRead(
        id=p.id,
        invoice_id=p.invoice_id,
        invoice_number=invoice_number,
        payment_date=p.payment_date,
        amount=Decimal(str(p.amount)),
        base_amount=Decimal(str(p.base_amount)),
        currency=p.currency,
        payment_method_id=p.payment_method_id,
        payment_method_name=p.payment_method_name,
        reference=p.reference,
        note=p.note,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _build_response(
    inv: Invoice,
    payments: list[Payment],
    state: PaymentState,
) -> InvoicePaymentsResponse:
    return InvoicePaymentsResponse(
        invoice_id=inv.id,
        invoice_number=inv.invoice_number,
        total_incl_vat=Decimal(str(inv.total_incl_vat)),
        base_total_incl_vat=Decimal(str(inv.base_total_incl_vat)),
        paid_total=state.paid_total,
        base_paid_total=state.base_paid_total,
        due_amount=state.due_amount,
        base_due_amount=state.base_due_amount,
        paid_status=state.paid_status,
        status=state.new_status,
        items=[_payment_to_read(p, inv.invoice_number) for p in payments],
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def record_payment(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    body: PaymentInput,
    creator_id: uuid.UUID | None,
) -> InvoicePaymentsResponse:
    """Record a new payment on an invoice, recompute state, and write-back.

    Guards:
    - D7 (前置守卫): invoice.status must be in {SENT, COMPLETED}; otherwise 422.
    - D6 (超额守卫): paid_total after recording must not exceed total_incl_vat; 422.
    - D2 (单一本位币): base_amount = amount, currency = invoice.currency,
      exchange_rate = 1.
    - D8 (name 快照): payment_method_name is snapshotted from the dictionary row.
    """
    inv = await _load_invoice(session, invoice_id, company_id)

    # D7: Pre-condition guard – invoice must have been sent
    if inv.status not in (InvoiceStatus.SENT, InvoiceStatus.COMPLETED):
        raise ValueError(
            "请先发出发票再登记收款（发票必须处于 SENT 或 COMPLETED 状态）。"
        )

    # Snapshot payment method name (D8)
    pm_name: str | None = None
    if body.payment_method_id is not None:
        pm_stmt = select(PaymentMethod).where(
            PaymentMethod.id == body.payment_method_id,
            PaymentMethod.company_id == company_id,
        )
        pm_result = await session.execute(pm_stmt)
        pm = pm_result.scalar_one_or_none()
        if pm is None:
            raise ValueError("Payment method not found or does not belong to this company.")
        pm_name = pm.name

    # D9: quantise the input amount to 3 dp (ROUND_HALF_UP) before any DB write.
    # This ensures that the in-memory object, the DB value, and every downstream
    # computation (recompute_payment_state, write-back, response) all use the
    # *same* Decimal.  Without this, NUMERIC(18,3) would silently truncate the
    # stored value while the Python side keeps the un-rounded number, causing
    # paid_total / due_amount / paid_status inconsistencies and potentially
    # leaving a fully-paid invoice stuck in SENT/PARTIALLY_PAID forever.
    amt = quantize_money(body.amount)

    # Build the new payment (D2: base_amount = amount, currency = invoice currency)
    new_payment = Payment()
    new_payment.company_id = company_id
    new_payment.invoice_id = inv.id
    new_payment.payment_date = body.payment_date
    new_payment.amount = amt
    new_payment.base_amount = amt  # D2: exchange_rate = 1
    new_payment.currency = inv.currency
    new_payment.exchange_rate = Decimal("1")
    new_payment.payment_method_id = body.payment_method_id
    new_payment.payment_method_name = pm_name
    new_payment.reference = body.reference
    new_payment.note = body.note
    new_payment.creator_id = creator_id

    session.add(new_payment)
    await session.flush()  # get new_payment.id; also makes it visible for sum

    # Load all payments including the newly flushed one
    all_payments = await _load_payments_for_invoice(session, inv.id)

    # D6: Overpayment guard – paid total must not exceed invoice total
    total_incl_vat = Decimal(str(inv.total_incl_vat))
    base_total_incl_vat = Decimal(str(inv.base_total_incl_vat))

    state = recompute_payment_state(
        total_incl_vat,
        base_total_incl_vat,
        all_payments,  # type: ignore[arg-type]
        InvoiceStatus(inv.status),
    )

    if state.paid_total > total_incl_vat:
        raise ValueError(
            "欠款不足以容纳本次收款（录入后累计收款将超过发票含税合计）。"
        )

    # Write back computed fields to invoice
    inv.due_amount = state.due_amount
    inv.base_due_amount = state.base_due_amount
    inv.paid_status = state.paid_status
    inv.status = state.new_status

    await session.commit()
    await session.refresh(inv)
    # Reload payments to get accurate created_at/updated_at after commit
    all_payments = await _load_payments_for_invoice(session, inv.id)

    return _build_response(inv, all_payments, state)


async def list_invoice_payments(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
) -> InvoicePaymentsResponse:
    """Return aggregate payment state for all payments on an invoice.

    Raises LookupError (→ 404) if the invoice is not found or belongs to
    a different company.
    """
    inv = await _load_invoice(session, invoice_id, company_id)
    payments = await _load_payments_for_invoice(session, inv.id)

    total_incl_vat = Decimal(str(inv.total_incl_vat))
    base_total_incl_vat = Decimal(str(inv.base_total_incl_vat))

    state = recompute_payment_state(
        total_incl_vat,
        base_total_incl_vat,
        payments,  # type: ignore[arg-type]
        InvoiceStatus(inv.status),
    )

    return _build_response(inv, payments, state)


async def get_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
) -> PaymentRead | None:
    """Fetch a single payment by id, scoped to company_id.

    Returns None if not found or belongs to a different company.
    """
    stmt = select(Payment).where(
        Payment.id == payment_id,
        Payment.company_id == company_id,
    )
    result = await session.execute(stmt)
    p = result.scalar_one_or_none()
    if p is None:
        return None

    # Load the invoice number for the response
    inv_stmt = select(Invoice.invoice_number).where(Invoice.id == p.invoice_id)
    inv_result = await session.execute(inv_stmt)
    invoice_number = inv_result.scalar_one()

    return _payment_to_read(p, invoice_number)


async def _load_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Payment:
    """Load a payment scoped to the company; raises LookupError (→ 404) if absent."""
    stmt = select(Payment).where(
        Payment.id == payment_id,
        Payment.company_id == company_id,
    )
    result = await session.execute(stmt)
    p = result.scalar_one_or_none()
    if p is None:
        raise LookupError("Payment not found.")
    return p


async def update_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
    body: PaymentInput,
) -> InvoicePaymentsResponse:
    """Edit a payment and recompute invoice payment state.

    Guards:
    - Cross-company payment → 404 (LookupError).
    - D6 (超额守卫): paid_total after edit must not exceed total_incl_vat; 422.
    - D9: amount is quantised with quantize_money() before write (same as record_payment).
    - D8: if payment_method_id changes, snapshot payment_method_name again.
    - Lifecycle rollback (D3): if edit reduces paid_total so invoice is no longer
      fully paid, COMPLETED is rolled back to SENT.  DRAFT/CANCELLED are never touched.
    """
    p = await _load_payment(session, payment_id, company_id)
    inv = await _load_invoice(session, p.invoice_id, company_id)

    # Snapshot payment method name if method changed or provided (D8)
    pm_name: str | None = p.payment_method_name  # default: keep existing snapshot
    if body.payment_method_id is not None:
        pm_stmt = select(PaymentMethod).where(
            PaymentMethod.id == body.payment_method_id,
            PaymentMethod.company_id == company_id,
        )
        pm_result = await session.execute(pm_stmt)
        pm = pm_result.scalar_one_or_none()
        if pm is None:
            raise ValueError("Payment method not found or does not belong to this company.")
        pm_name = pm.name
    elif body.payment_method_id is None:
        # Explicit null clears the method
        pm_name = None

    # D9: quantise input amount – same contract as record_payment
    amt = quantize_money(body.amount)

    # Apply field edits
    p.payment_date = body.payment_date
    p.amount = amt
    p.base_amount = amt  # D2: exchange_rate = 1
    p.payment_method_id = body.payment_method_id
    p.payment_method_name = pm_name
    p.reference = body.reference
    p.note = body.note

    await session.flush()  # write updated values before loading all payments

    # Load all payments (including the edited one) to recompute
    all_payments = await _load_payments_for_invoice(session, inv.id)

    total_incl_vat = Decimal(str(inv.total_incl_vat))
    base_total_incl_vat = Decimal(str(inv.base_total_incl_vat))

    state = recompute_payment_state(
        total_incl_vat,
        base_total_incl_vat,
        all_payments,  # type: ignore[arg-type]
        InvoiceStatus(inv.status),
    )

    # D6: overpayment guard – after edit, paid_total must not exceed invoice total
    if state.paid_total > total_incl_vat:
        raise ValueError(
            "欠款不足以容纳本次收款（修改后累计收款将超过发票含税合计）。"
        )

    # Write back computed fields to invoice
    inv.due_amount = state.due_amount
    inv.base_due_amount = state.base_due_amount
    inv.paid_status = state.paid_status
    inv.status = state.new_status

    await session.commit()
    await session.refresh(inv)
    all_payments = await _load_payments_for_invoice(session, inv.id)

    return _build_response(inv, all_payments, state)


async def delete_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
) -> InvoicePaymentsResponse:
    """Delete a payment and recompute invoice payment state.

    Returns the updated InvoicePaymentsResponse (200 with aggregate body,
    not 204 – see M7 contract note: front-end gets the new due/paid_status/status
    in a single response).

    Guards:
    - Cross-company payment → 404 (LookupError).
    - Lifecycle rollback (D3): deletion may reduce paid_total below full-payment
      threshold; COMPLETED is rolled back to SENT as needed.
    """
    p = await _load_payment(session, payment_id, company_id)
    inv = await _load_invoice(session, p.invoice_id, company_id)

    await session.delete(p)
    await session.flush()  # remove the payment before recomputing

    # Load remaining payments (deleted one is gone after flush)
    remaining_payments = await _load_payments_for_invoice(session, inv.id)

    total_incl_vat = Decimal(str(inv.total_incl_vat))
    base_total_incl_vat = Decimal(str(inv.base_total_incl_vat))

    state = recompute_payment_state(
        total_incl_vat,
        base_total_incl_vat,
        remaining_payments,  # type: ignore[arg-type]
        InvoiceStatus(inv.status),
    )

    # Write back computed fields to invoice
    inv.due_amount = state.due_amount
    inv.base_due_amount = state.base_due_amount
    inv.paid_status = state.paid_status
    inv.status = state.new_status

    await session.commit()
    await session.refresh(inv)
    remaining_payments = await _load_payments_for_invoice(session, inv.id)

    return _build_response(inv, remaining_payments, state)
