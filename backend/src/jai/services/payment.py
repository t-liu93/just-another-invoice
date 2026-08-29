"""Authoritative invoice and quote payment services."""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jai.db import set_rls_company
from jai.models._enums import (
    DocumentChainEventType,
    InvoiceDocumentKind,
    InvoicePaidStatus,
    InvoiceSettlementStatus,
    InvoiceStatus,
    InvoiceTaxMode,
    PaymentDirection,
    QuoteSettlementMode,
    QuoteStatus,
)
from jai.models.customer import Customer
from jai.models.dictionary import PaymentMethod
from jai.models.invoice import Invoice, InvoiceLine, InvoiceLineTax, InvoiceTax
from jai.models.payment import Payment, PaymentTax
from jai.models.quote import Quote, QuoteLine, QuoteLineTax, QuoteTax
from jai.schemas.payment import (
    InvoicePaymentsResponse,
    PaymentInput,
    PaymentListItem,
    PaymentListResponse,
    PaymentMutationResponse,
    PaymentRead,
    PaymentTaxRead,
    QuotePaymentsResponse,
)
from jai.services.document_chain import (
    append_document_chain_event,
    lock_quote_mode,
    quote_has_converted_invoice,
)
from jai.services.money import quantize_money, quantize_to_minor_unit

_INCOMING_PAYMENT_KINDS = {
    InvoiceDocumentKind.STANDARD,
    InvoiceDocumentKind.ADVANCE,
    InvoiceDocumentKind.FINAL,
}


def _payment_charge(invoice: Invoice) -> Decimal:
    """Final invoices collect only their frozen application residual."""
    return (
        Decimal(str(invoice.payable_before_payments))
        if InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.FINAL
        else Decimal(str(invoice.total_incl_vat))
    )


def _remaining_payment_charge(invoice: Invoice) -> Decimal:
    """Cash capacity after issued Credits, without treating cash as a Credit.

    ``paid_status`` continues to describe incoming cash against the original
    charge; settlement/due are the independent M12 projection and subtract
    issued Credits exactly once.
    """
    return max(
        _payment_charge(invoice) - Decimal(str(invoice.credited_total)), Decimal("0")
    )


class _PaymentLike(Protocol):
    amount: object
    base_amount: object


@dataclass(frozen=True)
class PaymentState:
    paid_total: Decimal
    base_paid_total: Decimal
    due_amount: Decimal
    base_due_amount: Decimal
    paid_status: InvoicePaidStatus
    new_status: InvoiceStatus


def recompute_payment_state(
    total_incl_vat: Decimal,
    base_total_incl_vat: Decimal,
    payments: Sequence[_PaymentLike],
    current_status: InvoiceStatus,
) -> PaymentState:
    """Pure invoice payment-state engine."""
    paid_total = sum((Decimal(str(p.amount)) for p in payments), Decimal("0"))
    base_paid_total = sum((Decimal(str(p.base_amount)) for p in payments), Decimal("0"))
    due_amount = total_incl_vat - paid_total
    base_due_amount = base_total_incl_vat - base_paid_total
    if due_amount == Decimal("0"):
        paid_status = InvoicePaidStatus.PAID
    elif paid_total == Decimal("0"):
        paid_status = InvoicePaidStatus.UNPAID
    else:
        paid_status = InvoicePaidStatus.PARTIALLY_PAID

    new_status = current_status
    if paid_status == InvoicePaidStatus.PAID and current_status == InvoiceStatus.SENT:
        new_status = InvoiceStatus.COMPLETED
    elif paid_status != InvoicePaidStatus.PAID and current_status == InvoiceStatus.COMPLETED:
        new_status = InvoiceStatus.SENT
    return PaymentState(
        paid_total=quantize_money(paid_total),
        base_paid_total=quantize_money(base_paid_total),
        due_amount=quantize_money(due_amount),
        base_due_amount=quantize_money(base_due_amount),
        paid_status=paid_status,
        new_status=new_status,
    )


@dataclass(frozen=True)
class TaxBucketSnapshot:
    bucket_key: str
    sort_order: int
    vat_rate_id: uuid.UUID | None
    vat_rate_label: str
    vat_rate_percent: Decimal
    vat_treatment_code: str
    vat_treatment_effect: str
    vat_treatment_requires_icp: bool
    taxable_amount: Decimal
    vat_amount: Decimal


@dataclass(frozen=True)
class AllocatedTaxBucket:
    bucket: TaxBucketSnapshot
    taxable_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


def tax_bucket_key(
    vat_rate_percent: Decimal,
    treatment_code: str,
    treatment_effect: str,
    treatment_requires_icp: bool,
) -> str:
    """Return a stable snapshot identity shared by payment and invoice buckets.

    The nullable VAT-rate FK is deliberately excluded: dictionary deletion may
    set it to NULL, but must never change the identity of tax already recognised.
    """
    return f"{treatment_code}|{treatment_effect}|{int(treatment_requires_icp)}|{vat_rate_percent}"


def _minor_units(value: Decimal) -> int:
    return int(quantize_to_minor_unit(value) * Decimal("100"))


def _from_minor_units(value: int) -> Decimal:
    return quantize_to_minor_unit(Decimal(value) / Decimal("100"))


def allocate_quote_payment_taxes(
    buckets: list[TaxBucketSnapshot],
    payment_amounts: list[Decimal],
) -> list[list[AllocatedTaxBucket]]:
    """Allocate ordered payments over remaining taxable/VAT cents.

    Deterministic largest remainder makes every payment exact and makes a fully
    paid quote reproduce its persisted tax buckets without cent drift.
    """
    components: list[int] = []
    for bucket in buckets:
        taxable_units = _minor_units(bucket.taxable_amount)
        vat_units = _minor_units(bucket.vat_amount)
        if taxable_units < 0 or vat_units < 0:
            raise ValueError("Quote payment VAT buckets cannot be negative.")
        components.extend((taxable_units, vat_units))

    remaining = components.copy()
    results: list[list[AllocatedTaxBucket]] = []
    for raw_amount in payment_amounts:
        payment_units = _minor_units(raw_amount)
        if payment_units <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        remaining_total = sum(remaining)
        if payment_units > remaining_total:
            raise ValueError(
                "Payment exceeds the outstanding amount "
                "(cumulative payments would exceed the quote total)."
            )

        shares: list[int] = []
        remainders: list[tuple[int, int]] = []
        for index, component in enumerate(remaining):
            base, remainder = divmod(payment_units * component, remaining_total)
            shares.append(base)
            remainders.append((remainder, index))
        leftover = payment_units - sum(shares)
        for _, index in sorted(remainders, key=lambda item: (-item[0], item[1]))[:leftover]:
            shares[index] += 1

        rows: list[AllocatedTaxBucket] = []
        for bucket_index, bucket in enumerate(buckets):
            taxable_units = shares[bucket_index * 2]
            vat_units = shares[bucket_index * 2 + 1]
            rows.append(
                AllocatedTaxBucket(
                    bucket=bucket,
                    taxable_amount=_from_minor_units(taxable_units),
                    vat_amount=_from_minor_units(vat_units),
                    gross_amount=_from_minor_units(taxable_units + vat_units),
                )
            )
        for index, share in enumerate(shares):
            remaining[index] -= share
            if remaining[index] < 0:
                raise AssertionError("Payment tax allocation exceeded a component.")
        if sum((row.gross_amount for row in rows), Decimal("0")) != (
            _from_minor_units(payment_units)
        ):
            raise AssertionError("Payment tax allocation did not balance.")
        results.append(rows)
    return results


def aggregate_quote_tax_buckets(quote: Quote) -> list[TaxBucketSnapshot]:
    """Aggregate a quote's persisted tax rows by immutable snapshot identity."""
    Key = tuple[uuid.UUID | None, str, Decimal, str, str, bool]
    aggregates: dict[Key, tuple[Decimal, Decimal]] = {}
    rows: list[QuoteTax | QuoteLineTax] = []
    if InvoiceTaxMode(quote.tax_mode) == InvoiceTaxMode.DOCUMENT:
        rows.extend(quote.taxes)
    else:
        for line in quote.lines:
            rows.extend(line.line_taxes)

    for row in rows:
        key: Key = (
            row.vat_rate_id,
            str(row.vat_rate_label),
            Decimal(str(row.vat_rate_percent)),
            quote.vat_treatment_code,
            quote.vat_treatment_effect,
            quote.vat_treatment_requires_icp,
        )
        current_taxable, current_vat = aggregates.get(key, (Decimal("0"), Decimal("0")))
        aggregates[key] = (
            current_taxable + Decimal(str(row.taxable_amount)),
            current_vat + Decimal(str(row.tax_amount)),
        )

    buckets: list[TaxBucketSnapshot] = []
    ordered = sorted(
        aggregates.items(),
        key=lambda item: (item[0][2], item[0][1], str(item[0][0] or "")),
    )
    for sort_order, (key, amounts) in enumerate(ordered):
        rate_id, label, percent, treatment_code, treatment_effect, requires_icp = key
        bucket_key = tax_bucket_key(
            percent,
            treatment_code,
            treatment_effect,
            requires_icp,
        )
        buckets.append(
            TaxBucketSnapshot(
                bucket_key=bucket_key,
                sort_order=sort_order,
                vat_rate_id=rate_id,
                vat_rate_label=label,
                vat_rate_percent=percent,
                vat_treatment_code=treatment_code,
                vat_treatment_effect=treatment_effect,
                vat_treatment_requires_icp=requires_icp,
                taxable_amount=quantize_to_minor_unit(amounts[0]),
                vat_amount=quantize_to_minor_unit(amounts[1]),
            )
        )
    return buckets


async def _load_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Invoice:
    stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == company_id)
    if lock:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise LookupError("Invoice not found.")
    return invoice


async def _load_quote(
    session: AsyncSession,
    quote_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Quote:
    stmt = (
        select(Quote)
        .where(Quote.id == quote_id, Quote.company_id == company_id)
        .options(
            selectinload(Quote.lines).selectinload(QuoteLine.line_taxes),
            selectinload(Quote.taxes),
        )
    )
    if lock:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    quote = result.scalar_one_or_none()
    if quote is None:
        raise LookupError("Quote not found.")
    return quote


async def _load_payments_for_invoice(session: AsyncSession, invoice_id: uuid.UUID) -> list[Payment]:
    result = await session.execute(
        select(Payment)
        .where(Payment.invoice_id == invoice_id)
        .options(selectinload(Payment.taxes))
        .order_by(Payment.payment_date, Payment.created_at, Payment.id)
    )
    return list(result.scalars().all())


async def _load_payments_for_quote(
    session: AsyncSession, quote_id: uuid.UUID, *, lock: bool = False
) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.quote_id == quote_id)
        .options(selectinload(Payment.taxes))
        .order_by(Payment.payment_date, Payment.created_at, Payment.id)
    )
    if lock:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _document_number_maps(
    session: AsyncSession, payments: list[Payment]
) -> tuple[dict[uuid.UUID, str | None], dict[uuid.UUID, str]]:
    invoice_ids = {p.invoice_id for p in payments if p.invoice_id is not None}
    quote_ids = {p.quote_id for p in payments if p.quote_id is not None}
    invoice_map: dict[uuid.UUID, str | None] = {}
    quote_map: dict[uuid.UUID, str] = {}
    if invoice_ids:
        result = await session.execute(
            select(Invoice.id, Invoice.invoice_number).where(Invoice.id.in_(invoice_ids))
        )
        invoice_map = {row.id: row.invoice_number for row in result.all()}
    if quote_ids:
        result = await session.execute(
            select(Quote.id, Quote.quote_number).where(Quote.id.in_(quote_ids))
        )
        quote_map = {row.id: row.quote_number for row in result.all()}
    return invoice_map, quote_map


def _payment_to_read(
    payment: Payment,
    *,
    invoice_number: str | None,
    quote_number: str | None,
) -> PaymentRead:
    return PaymentRead(
        id=payment.id,
        origin_type="QUOTE" if payment.quote_id is not None else "INVOICE",
        invoice_id=payment.invoice_id,
        invoice_number=invoice_number,
        quote_id=payment.quote_id,
        quote_number=quote_number,
        payment_date=payment.payment_date,
        amount=Decimal(str(payment.amount)),
        base_amount=Decimal(str(payment.base_amount)),
        currency=payment.currency,
        payment_method_id=payment.payment_method_id,
        payment_method_name=payment.payment_method_name,
        reference=payment.reference,
        note=payment.note,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        tax_breakdown=[
            PaymentTaxRead(
                vat_rate_id=tax.vat_rate_id,
                vat_rate_label=tax.vat_rate_label,
                vat_rate_percent=Decimal(str(tax.vat_rate_percent)),
                taxable_amount=Decimal(str(tax.taxable_amount)),
                vat_amount=Decimal(str(tax.vat_amount)),
                gross_amount=Decimal(str(tax.gross_amount)),
                base_taxable_amount=Decimal(str(tax.base_taxable_amount)),
                base_vat_amount=Decimal(str(tax.base_vat_amount)),
                base_gross_amount=Decimal(str(tax.base_gross_amount)),
            )
            for tax in payment.taxes
        ],
    )


async def _payment_reads(session: AsyncSession, payments: list[Payment]) -> list[PaymentRead]:
    invoice_map, quote_map = await _document_number_maps(session, payments)
    return [
        _payment_to_read(
            payment,
            invoice_number=(
                invoice_map.get(payment.invoice_id) if payment.invoice_id is not None else None
            ),
            quote_number=(
                quote_map.get(payment.quote_id) if payment.quote_id is not None else None
            ),
        )
        for payment in payments
    ]


async def _build_invoice_response(
    session: AsyncSession,
    invoice: Invoice,
    payments: list[Payment],
    state: PaymentState | None = None,
) -> InvoicePaymentsResponse:
    if state is None:
        state = recompute_payment_state(
            _payment_charge(invoice),
            _payment_charge(invoice),
            payments,
            InvoiceStatus(invoice.status),
        )
    return InvoicePaymentsResponse(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        total_incl_vat=Decimal(str(invoice.total_incl_vat)),
        base_total_incl_vat=Decimal(str(invoice.base_total_incl_vat)),
        paid_total=state.paid_total,
        base_paid_total=state.base_paid_total,
        due_amount=state.due_amount,
        base_due_amount=state.base_due_amount,
        paid_status=state.paid_status,
        status=state.new_status,
        items=await _payment_reads(session, payments),
    )


async def _build_quote_response(
    session: AsyncSession, quote: Quote, payments: list[Payment]
) -> QuotePaymentsResponse:
    paid_total = quantize_money(sum((Decimal(str(p.amount)) for p in payments), Decimal("0")))
    total = Decimal(str(quote.total_incl_vat))
    return QuotePaymentsResponse(
        quote_id=quote.id,
        quote_number=quote.quote_number,
        converted_invoice_id=quote.converted_invoice_id,
        total_incl_vat=total,
        paid_total=paid_total,
        remaining_amount=quantize_money(total - paid_total),
        items=await _payment_reads(session, payments),
    )


async def _payment_method_name(
    session: AsyncSession,
    company_id: uuid.UUID,
    payment_method_id: uuid.UUID | None,
) -> str | None:
    if payment_method_id is None:
        return None
    result = await session.execute(
        select(PaymentMethod).where(
            PaymentMethod.id == payment_method_id,
            PaymentMethod.company_id == company_id,
        )
    )
    method = result.scalar_one_or_none()
    if method is None:
        raise ValueError("Payment method not found or does not belong to this company.")
    return method.name


async def recompute_quote_payment_taxes(
    session: AsyncSession, quote: Quote, payments: list[Payment]
) -> None:
    """Replace quote-origin tax children through ORM delete-orphan cascade."""
    buckets = aggregate_quote_tax_buckets(quote)
    gross_total = sum(
        (bucket.taxable_amount + bucket.vat_amount for bucket in buckets),
        Decimal("0"),
    )
    if quantize_to_minor_unit(gross_total) != quantize_to_minor_unit(
        Decimal(str(quote.total_incl_vat))
    ):
        raise ValueError("Persisted quote VAT snapshots do not balance to the quote total.")
    allocations = allocate_quote_payment_taxes(
        buckets, [Decimal(str(payment.amount)) for payment in payments]
    )
    for payment, rows in zip(payments, allocations, strict=True):
        payment.taxes.clear()
        for allocation in rows:
            tax = PaymentTax(
                vat_rate_id=allocation.bucket.vat_rate_id,
                vat_rate_label=allocation.bucket.vat_rate_label,
                vat_rate_percent=allocation.bucket.vat_rate_percent,
                vat_treatment_code=allocation.bucket.vat_treatment_code,
                vat_treatment_effect=allocation.bucket.vat_treatment_effect,
                vat_treatment_requires_icp=(allocation.bucket.vat_treatment_requires_icp),
                taxable_amount=allocation.taxable_amount,
                vat_amount=allocation.vat_amount,
                gross_amount=allocation.gross_amount,
                base_taxable_amount=allocation.taxable_amount,
                base_vat_amount=allocation.vat_amount,
                base_gross_amount=allocation.gross_amount,
                bucket_key=allocation.bucket.bucket_key,
                sort_order=allocation.bucket.sort_order,
            )
            payment.taxes.append(tax)
    await session.flush()


async def validate_invoice_tax_coverage(
    session: AsyncSession,
    invoice: Invoice,
) -> None:
    """Reject final VAT buckets that do not cover recognised advance snapshots.

    Quotes and invoices are currently restricted to the company base currency,
    so each persisted final-tax bucket is also its base-currency snapshot.  We
    still carry both dimensions here: payment tax rows deliberately keep their
    own ``base_*`` amounts for BTW reporting, and this makes the invariant
    explicit instead of accidentally checking only the transaction amounts.
    """
    recognised_result = await session.execute(
        select(PaymentTax)
        .join(Payment, PaymentTax.payment_id == Payment.id)
        .where(
            Payment.invoice_id == invoice.id,
            Payment.quote_id.is_not(None),
        )
    )
    recognised: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {}
    for row in recognised_result.scalars().all():
        taxable, vat, base_taxable, base_vat = recognised.get(
            row.bucket_key,
            (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        )
        recognised[row.bucket_key] = (
            taxable + Decimal(str(row.taxable_amount)),
            vat + Decimal(str(row.vat_amount)),
            base_taxable + Decimal(str(row.base_taxable_amount)),
            base_vat + Decimal(str(row.base_vat_amount)),
        )
    if not recognised:
        return

    final_rows: list[InvoiceTax | InvoiceLineTax]
    if InvoiceTaxMode(invoice.tax_mode) == InvoiceTaxMode.DOCUMENT:
        document_tax_result = await session.execute(
            select(InvoiceTax).where(InvoiceTax.invoice_id == invoice.id)
        )
        final_rows = list(document_tax_result.scalars().all())
    else:
        line_tax_result = await session.execute(
            select(InvoiceLineTax)
            .join(InvoiceLine, InvoiceLineTax.invoice_line_id == InvoiceLine.id)
            .where(InvoiceLine.invoice_id == invoice.id)
        )
        final_rows = list(line_tax_result.scalars().all())

    final_buckets: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {}
    for tax_row in final_rows:
        key = tax_bucket_key(
            Decimal(str(tax_row.vat_rate_percent)),
            invoice.vat_treatment_code,
            invoice.vat_treatment_effect,
            invoice.vat_treatment_requires_icp,
        )
        taxable, vat, base_taxable, base_vat = final_buckets.get(
            key,
            (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        )
        tax_amount = Decimal(str(tax_row.tax_amount))
        taxable_amount = Decimal(str(tax_row.taxable_amount))
        final_buckets[key] = (
            taxable + taxable_amount,
            vat + tax_amount,
            # M5/M6 enforce invoice.currency == company.base_currency.  There
            # is intentionally no invented FX allocation before a future
            # foreign-document design supplies persisted per-bucket base data.
            base_taxable + taxable_amount,
            base_vat + tax_amount,
        )
    for key, (
        required_taxable,
        required_vat,
        required_base_taxable,
        required_base_vat,
    ) in recognised.items():
        final_taxable, final_vat, final_base_taxable, final_base_vat = final_buckets.get(
            key,
            (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        )
        if (
            final_taxable < required_taxable
            or final_vat < required_vat
            or final_base_taxable < required_base_taxable
            or final_base_vat < required_base_vat
        ):
            raise ValueError(
                "Final invoice VAT buckets cannot be lower than VAT already "
                "recognised from quote payments."
            )


def _write_invoice_state(invoice: Invoice, state: PaymentState) -> None:
    invoice.paid_status = state.paid_status
    invoice.status = state.new_status
    # These M12 columns are persisted caches, not a second settlement path.
    # Keep them in the same transaction as the legacy due/paid state.
    # Final's payable amount is its residual charge, not the displayed full project.
    if InvoiceDocumentKind(invoice.document_kind) != InvoiceDocumentKind.FINAL:
        invoice.payable_before_payments = Decimal(str(invoice.total_incl_vat))
        invoice.base_payable_before_payments = Decimal(str(invoice.base_total_incl_vat))
    invoice.incoming_payment_total = state.paid_total
    invoice.base_incoming_payment_total = state.base_paid_total
    charge = Decimal(str(invoice.payable_before_payments)) - Decimal(str(invoice.credited_total))
    base_charge = Decimal(str(invoice.base_payable_before_payments)) - Decimal(
        str(invoice.base_credited_total)
    )
    invoice.due_amount = max(charge - state.paid_total, Decimal("0"))
    invoice.base_due_amount = max(base_charge - state.base_paid_total, Decimal("0"))
    invoice.refund_due_amount = max(state.paid_total - charge, Decimal("0"))
    invoice.base_refund_due_amount = max(state.base_paid_total - base_charge, Decimal("0"))
    invoice.settlement_status = (
        InvoiceSettlementStatus.REFUND_DUE
        if invoice.refund_due_amount > Decimal("0")
        else (
            InvoiceSettlementStatus.SETTLED
            if invoice.due_amount == Decimal("0")
            else (
                InvoiceSettlementStatus.PARTIALLY_SETTLED
                if state.paid_total > Decimal("0")
                else InvoiceSettlementStatus.OPEN
            )
        )
    )


async def record_payment(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    body: PaymentInput,
    creator_id: uuid.UUID | None,
) -> InvoicePaymentsResponse:
    await set_rls_company(session, company_id)
    invoice = await _load_invoice(session, invoice_id, company_id, lock=True)
    if InvoiceDocumentKind(invoice.document_kind) not in _INCOMING_PAYMENT_KINDS:
        raise ValueError("Incoming payments are supported only for charge invoices.")
    if invoice.invoice_number is None:
        raise ValueError(
            "Cannot record a payment on an unissued draft invoice; issue it (mark as Sent) first."
        )
    if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.COMPLETED):
        raise ValueError(
            "Invoice must be sent before recording a payment "
            "(its status must be SENT or COMPLETED)."
        )
    amount = quantize_money(body.amount)
    payment = Payment(
        company_id=company_id,
        invoice_id=invoice.id,
        quote_id=None,
        payment_date=body.payment_date,
        amount=amount,
        base_amount=amount,
        currency=invoice.currency,
        exchange_rate=Decimal("1"),
        payment_method_id=body.payment_method_id,
        payment_method_name=await _payment_method_name(session, company_id, body.payment_method_id),
        reference=body.reference,
        note=body.note,
        creator_id=creator_id,
    )
    session.add(payment)
    await session.flush()
    payments = await _load_payments_for_invoice(session, invoice.id)
    state = recompute_payment_state(
        _payment_charge(invoice),
        _payment_charge(invoice),
        payments,
        InvoiceStatus(invoice.status),
    )
    if state.paid_total > _remaining_payment_charge(invoice):
        raise ValueError(
            "Payment exceeds the outstanding amount "
            "(cumulative payments would exceed the invoice total)."
        )
    _write_invoice_state(invoice, state)
    await append_document_chain_event(
        session,
        company_id=company_id,
        invoice_id=invoice.id,
        actor_user_id=creator_id,
        event_type=DocumentChainEventType.INVOICE_PAYMENT_CREATED,
        metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
    )
    await session.commit()
    await set_rls_company(session, company_id)
    payments = await _load_payments_for_invoice(session, invoice.id)
    return await _build_invoice_response(session, invoice, payments, state)


async def record_quote_payment(
    session: AsyncSession,
    quote_id: uuid.UUID,
    company_id: uuid.UUID,
    body: PaymentInput,
    creator_id: uuid.UUID | None,
) -> QuotePaymentsResponse:
    await set_rls_company(session, company_id)
    quote = await _load_quote(session, quote_id, company_id, lock=True)
    if QuoteStatus(quote.status) != QuoteStatus.ACCEPTED:
        raise ValueError("Quote must be ACCEPTED before recording a payment.")
    await lock_quote_mode(
        session, quote, QuoteSettlementMode.RECEIPT_ONLY, actor_user_id=creator_id
    )
    if quote.vat_treatment_code != "NL_DOMESTIC":
        raise ValueError("Quote deposits are supported only for NL_DOMESTIC VAT treatment.")
    if await quote_has_converted_invoice(session, company_id=company_id, quote_id=quote.id):
        raise ValueError("Cannot record a quote payment after the quote has been converted.")
    amount = quantize_to_minor_unit(body.amount)
    existing = await _load_payments_for_quote(session, quote.id, lock=True)
    paid_before = sum((Decimal(str(p.amount)) for p in existing), Decimal("0"))
    if paid_before + amount > Decimal(str(quote.total_incl_vat)):
        raise ValueError(
            "Payment exceeds the outstanding amount "
            "(cumulative payments would exceed the quote total)."
        )
    payment = Payment(
        company_id=company_id,
        invoice_id=None,
        quote_id=quote.id,
        payment_date=body.payment_date,
        amount=amount,
        base_amount=amount,
        currency=quote.currency,
        exchange_rate=Decimal("1"),
        payment_method_id=body.payment_method_id,
        payment_method_name=await _payment_method_name(session, company_id, body.payment_method_id),
        reference=body.reference,
        note=body.note,
        creator_id=creator_id,
    )
    session.add(payment)
    await session.flush()
    await append_document_chain_event(
        session,
        company_id=company_id,
        quote_id=quote.id,
        actor_user_id=creator_id,
        event_type=DocumentChainEventType.QUOTE_PAYMENT_CREATED,
        metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
    )
    payments = await _load_payments_for_quote(session, quote.id, lock=True)
    await recompute_quote_payment_taxes(session, quote, payments)
    await session.commit()
    await set_rls_company(session, company_id)
    payments = await _load_payments_for_quote(session, quote.id)
    return await _build_quote_response(session, quote, payments)


async def list_invoice_payments(
    session: AsyncSession, invoice_id: uuid.UUID, company_id: uuid.UUID
) -> InvoicePaymentsResponse:
    await set_rls_company(session, company_id)
    invoice = await _load_invoice(session, invoice_id, company_id)
    if InvoiceDocumentKind(invoice.document_kind) not in _INCOMING_PAYMENT_KINDS:
        raise ValueError("Incoming payments are supported only for charge invoices.")
    payments = await _load_payments_for_invoice(session, invoice.id)
    return await _build_invoice_response(session, invoice, payments)


async def list_quote_payments(
    session: AsyncSession, quote_id: uuid.UUID, company_id: uuid.UUID
) -> QuotePaymentsResponse:
    await set_rls_company(session, company_id)
    quote = await _load_quote(session, quote_id, company_id)
    payments = await _load_payments_for_quote(session, quote.id)
    return await _build_quote_response(session, quote, payments)


async def get_payment(
    session: AsyncSession, payment_id: uuid.UUID, company_id: uuid.UUID
) -> PaymentRead | None:
    await set_rls_company(session, company_id)
    result = await session.execute(
        select(Payment)
        .where(Payment.id == payment_id, Payment.company_id == company_id)
        .options(selectinload(Payment.taxes))
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        return None
    return (await _payment_reads(session, [payment]))[0]


async def _lock_payment_context(
    session: AsyncSession, payment_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[Payment, Quote | None, Invoice | None]:
    await set_rls_company(session, company_id)
    seed_result = await session.execute(
        select(Payment.quote_id, Payment.invoice_id).where(
            Payment.id == payment_id, Payment.company_id == company_id
        )
    )
    seed = seed_result.one_or_none()
    if seed is None:
        raise LookupError("Payment not found.")
    quote: Quote | None = None
    invoice: Invoice | None = None
    if seed.quote_id is not None:
        quote = await _load_quote(session, seed.quote_id, company_id, lock=True)
    elif seed.invoice_id is not None:
        invoice = await _load_invoice(session, seed.invoice_id, company_id, lock=True)

    payment_result = await session.execute(
        select(Payment)
        .where(Payment.id == payment_id, Payment.company_id == company_id)
        .options(selectinload(Payment.taxes))
        .with_for_update()
    )
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise LookupError("Payment not found.")
    if payment.quote_id is not None and quote is None:
        quote = await _load_quote(session, payment.quote_id, company_id, lock=True)
    if payment.invoice_id is not None and invoice is None:
        invoice = await _load_invoice(session, payment.invoice_id, company_id, lock=True)
    return payment, quote, invoice


async def update_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
    body: PaymentInput,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> PaymentMutationResponse:
    await set_rls_company(session, company_id)
    payment, quote, invoice = await _lock_payment_context(session, payment_id, company_id)
    previous_amount = Decimal(str(payment.amount))
    amount = (
        quantize_to_minor_unit(body.amount) if quote is not None else quantize_money(body.amount)
    )
    if quote is not None and invoice is not None and body.payment_date > invoice.invoice_date:
        raise ValueError("A quote-origin payment date cannot be later than the final invoice date.")
    payment.payment_date = body.payment_date
    payment.amount = amount
    payment.base_amount = amount
    payment.payment_method_id = body.payment_method_id
    payment.payment_method_name = await _payment_method_name(
        session, company_id, body.payment_method_id
    )
    payment.reference = body.reference
    payment.note = body.note
    await session.flush()

    quote_payments: list[Payment] | None = None
    if quote is not None:
        quote_payments = await _load_payments_for_quote(session, quote.id, lock=True)
        quote_paid = sum((Decimal(str(item.amount)) for item in quote_payments), Decimal("0"))
        if quote_paid > Decimal(str(quote.total_incl_vat)):
            raise ValueError(
                "Payment exceeds the outstanding amount "
                "(cumulative payments would exceed the quote total after this edit)."
            )
        await recompute_quote_payment_taxes(session, quote, quote_payments)
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id,
            actor_user_id=actor_user_id,
            event_type=DocumentChainEventType.QUOTE_PAYMENT_UPDATED,
            metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
        )

    invoice_payments: list[Payment] | None = None
    invoice_state: PaymentState | None = None
    if invoice is not None:
        if InvoiceDocumentKind(invoice.document_kind) not in _INCOMING_PAYMENT_KINDS:
            raise ValueError("Incoming payments are supported only for charge invoices.")
        invoice_payments = await _load_payments_for_invoice(session, invoice.id)
        invoice_state = recompute_payment_state(
            _payment_charge(invoice),
            _payment_charge(invoice),
            invoice_payments,
            InvoiceStatus(invoice.status),
        )
        # A Credit issued after a lawful incoming payment can make the source
        # REFUND_DUE.  Metadata edits, reductions and deletion of that
        # historical cash remain legal; only a new net increase must fit the
        # normal outstanding-capacity guard.
        previous_paid_total = invoice_state.paid_total - amount + previous_amount
        allowed_paid_total = max(_remaining_payment_charge(invoice), previous_paid_total)
        if invoice_state.paid_total > allowed_paid_total:
            raise ValueError(
                "Payment exceeds the outstanding amount "
                "(cumulative payments would exceed the invoice total after this edit)."
            )
        if quote is not None:
            await validate_invoice_tax_coverage(session, invoice)
        _write_invoice_state(invoice, invoice_state)
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id if quote is not None else None,
            invoice_id=invoice.id,
            actor_user_id=actor_user_id,
            event_type=DocumentChainEventType.INVOICE_PAYMENT_UPDATED,
            metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
        )
    await session.commit()
    await set_rls_company(session, company_id)
    if quote is not None:
        quote_payments = await _load_payments_for_quote(session, quote.id)
    if invoice is not None:
        if InvoiceDocumentKind(invoice.document_kind) not in _INCOMING_PAYMENT_KINDS:
            raise ValueError("Incoming payments are supported only for charge invoices.")
        invoice_payments = await _load_payments_for_invoice(session, invoice.id)
    return PaymentMutationResponse(
        payment_id=payment_id,
        deleted=False,
        quote=(
            await _build_quote_response(session, quote, quote_payments or [])
            if quote is not None
            else None
        ),
        invoice=(
            await _build_invoice_response(session, invoice, invoice_payments or [], invoice_state)
            if invoice is not None
            else None
        ),
    )


async def delete_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> PaymentMutationResponse:
    await set_rls_company(session, company_id)
    payment, quote, invoice = await _lock_payment_context(session, payment_id, company_id)
    if invoice is not None and (
        InvoiceDocumentKind(invoice.document_kind) not in _INCOMING_PAYMENT_KINDS
    ):
        await session.rollback()
        raise ValueError("Incoming payments are supported only for charge invoices.")
    if quote is not None:
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id,
            actor_user_id=actor_user_id,
            event_type=DocumentChainEventType.QUOTE_PAYMENT_DELETED,
            metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
        )
    await session.delete(payment)
    await session.flush()
    quote_payments: list[Payment] | None = None
    if quote is not None:
        quote_payments = await _load_payments_for_quote(session, quote.id, lock=True)
        await recompute_quote_payment_taxes(session, quote, quote_payments)
    invoice_payments: list[Payment] | None = None
    invoice_state: PaymentState | None = None
    if invoice is not None:
        invoice_payments = await _load_payments_for_invoice(session, invoice.id)
        invoice_state = recompute_payment_state(
            _payment_charge(invoice),
            _payment_charge(invoice),
            invoice_payments,
            InvoiceStatus(invoice.status),
        )
        _write_invoice_state(invoice, invoice_state)
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id if quote is not None else None,
            invoice_id=invoice.id,
            actor_user_id=actor_user_id,
            event_type=DocumentChainEventType.INVOICE_PAYMENT_DELETED,
            metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
        )
    await session.commit()
    await set_rls_company(session, company_id)
    if quote is not None:
        quote_payments = await _load_payments_for_quote(session, quote.id)
    if invoice is not None:
        invoice_payments = await _load_payments_for_invoice(session, invoice.id)
    return PaymentMutationResponse(
        payment_id=payment_id,
        deleted=True,
        quote=(
            await _build_quote_response(session, quote, quote_payments or [])
            if quote is not None
            else None
        ),
        invoice=(
            await _build_invoice_response(session, invoice, invoice_payments or [], invoice_state)
            if invoice is not None
            else None
        ),
    )


async def list_payments(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    customer_id: uuid.UUID | None = None,
    payment_method_id: uuid.UUID | None = None,
    direction: PaymentDirection | None = None,
    document_kind: InvoiceDocumentKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "payment_date",
) -> PaymentListResponse:
    await set_rls_company(session, company_id)
    customer_link = func.coalesce(Invoice.customer_id, Quote.customer_id)
    base = (
        select(Payment)
        .outerjoin(Invoice, Payment.invoice_id == Invoice.id)
        .outerjoin(Quote, Payment.quote_id == Quote.id)
        .join(Customer, Customer.id == customer_link)
        .where(Payment.company_id == company_id)
    )
    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                Invoice.invoice_number.ilike(like),
                Quote.quote_number.ilike(like),
                Customer.name.ilike(like),
            )
        )
    if customer_id is not None:
        base = base.where(customer_link == customer_id)
    if payment_method_id is not None:
        base = base.where(Payment.payment_method_id == payment_method_id)
    if direction is not None:
        base = base.where(Payment.direction == direction)
    if document_kind is not None:
        base = base.where(Invoice.document_kind == document_kind)
    if date_from is not None:
        base = base.where(Payment.payment_date >= date_from)
    if date_to is not None:
        base = base.where(Payment.payment_date <= date_to)
    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()
    order = (
        (
            Payment.created_at.desc(),
            Payment.payment_date.desc(),
            Payment.id.desc(),
        )
        if sort_by == "created_at"
        else (
            Payment.payment_date.desc(),
            Payment.created_at.desc(),
            Payment.id.desc(),
        )
    )
    result = await session.execute(
        base.options(selectinload(Payment.taxes)).order_by(*order).limit(limit).offset(offset)
    )
    payments = list(result.scalars().all())
    if not payments:
        return PaymentListResponse(items=[], total=total)

    invoice_map, quote_map = await _document_number_maps(session, payments)
    invoice_ids = {p.invoice_id for p in payments if p.invoice_id is not None}
    quote_ids = {p.quote_id for p in payments if p.quote_id is not None}
    customer_by_invoice: dict[uuid.UUID, uuid.UUID] = {}
    customer_by_quote: dict[uuid.UUID, uuid.UUID] = {}
    if invoice_ids:
        rows = await session.execute(
            select(Invoice.id, Invoice.customer_id).where(Invoice.id.in_(invoice_ids))
        )
        customer_by_invoice = {row.id: row.customer_id for row in rows.all()}
    if quote_ids:
        rows = await session.execute(
            select(Quote.id, Quote.customer_id).where(Quote.id.in_(quote_ids))
        )
        customer_by_quote = {row.id: row.customer_id for row in rows.all()}
    customer_ids = set(customer_by_invoice.values()) | set(customer_by_quote.values())
    customer_rows = await session.execute(
        select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
    )
    customer_map = {row.id: row.name for row in customer_rows.all()}

    items: list[PaymentListItem] = []
    for payment in payments:
        linked_customer_id = (
            customer_by_invoice.get(payment.invoice_id) if payment.invoice_id is not None else None
        ) or (customer_by_quote.get(payment.quote_id) if payment.quote_id is not None else None)
        if linked_customer_id is None:
            continue
        items.append(
            PaymentListItem(
                id=payment.id,
                origin_type="QUOTE" if payment.quote_id is not None else "INVOICE",
                invoice_id=payment.invoice_id,
                invoice_number=(
                    invoice_map.get(payment.invoice_id) if payment.invoice_id is not None else None
                ),
                quote_id=payment.quote_id,
                quote_number=(
                    quote_map.get(payment.quote_id) if payment.quote_id is not None else None
                ),
                customer_id=linked_customer_id,
                customer_name=customer_map.get(linked_customer_id, ""),
                payment_date=payment.payment_date,
                amount=Decimal(str(payment.amount)),
                payment_method_name=payment.payment_method_name,
                created_at=payment.created_at,
            )
        )
    return PaymentListResponse(items=items, total=total)
