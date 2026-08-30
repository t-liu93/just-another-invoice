"""Authoritative invoice and quote payment services."""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

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
from jai.models.document import (
    FinalAdvanceApplication,
    InvoiceCorrection,
    InvoiceCorrectionLine,
)
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
    RefundCollectionRead,
)
from jai.services.document_chain import (
    append_document_chain_event,
    direct_document_component_ids,
    lock_quote_mode,
    quote_has_converted_invoice,
)
from jai.services.money import quantize_money, quantize_to_minor_unit

_INCOMING_PAYMENT_KINDS = {
    InvoiceDocumentKind.STANDARD,
    InvoiceDocumentKind.ADVANCE,
    InvoiceDocumentKind.FINAL,
}


class SettlementConflictError(ValueError):
    """A stale cash command that lost after acquiring the canonical chain locks."""


class RefundSettlementError(ValueError):
    """A stable, client-actionable rejection from the refund settlement domain."""

    def __init__(self, code: str, detail: str, *, status_code: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.status_code = status_code


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
        .where(Payment.invoice_id == invoice_id, Payment.deleted_at.is_(None))
        .options(selectinload(Payment.taxes))
        .order_by(Payment.payment_date, Payment.created_at, Payment.id)
    )
    return list(result.scalars().all())


async def _load_payments_for_quote(
    session: AsyncSession, quote_id: uuid.UUID, *, lock: bool = False
) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.quote_id == quote_id, Payment.deleted_at.is_(None))
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


async def _credit_number_map(
    session: AsyncSession, payments: list[Payment]
) -> dict[uuid.UUID, str | None]:
    credit_ids = {p.credit_note_id for p in payments if p.credit_note_id is not None}
    if not credit_ids:
        return {}
    result = await session.execute(
        select(Invoice.id, Invoice.invoice_number).where(Invoice.id.in_(credit_ids))
    )
    return {row.id: row.invoice_number for row in result.all()}


def _payment_to_read(
    payment: Payment,
    *,
    invoice_number: str | None,
    quote_number: str | None,
    credit_note_number: str | None = None,
) -> PaymentRead:
    return PaymentRead(
        id=payment.id,
        origin_type=(
            "CREDIT_NOTE"
            if payment.credit_note_id is not None
            else ("QUOTE" if payment.quote_id is not None else "INVOICE")
        ),
        invoice_id=payment.invoice_id,
        invoice_number=invoice_number,
        quote_id=payment.quote_id,
        quote_number=quote_number,
        direction=PaymentDirection(payment.direction),
        credit_note_id=payment.credit_note_id,
        credit_note_number=credit_note_number,
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
    credit_map = await _credit_number_map(session, payments)
    return [
        _payment_to_read(
            payment,
            invoice_number=(
                invoice_map.get(payment.invoice_id) if payment.invoice_id is not None else None
            ),
            quote_number=(
                quote_map.get(payment.quote_id) if payment.quote_id is not None else None
            ),
            credit_note_number=(
                credit_map.get(payment.credit_note_id)
                if payment.credit_note_id is not None
                else None
            ),
        )
        for payment in payments
    ]


async def _build_invoice_response(
    session: AsyncSession,
    invoice: Invoice,
    payments: list[Payment],
) -> InvoicePaymentsResponse:
    # M12 cache columns are maintained in the same locked transaction as cash
    # mutations.  Serialize them directly so a credited/refunded source never
    # falls back to the legacy ``gross - incoming`` equation in its response.
    return InvoicePaymentsResponse(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        total_incl_vat=Decimal(str(invoice.total_incl_vat)),
        base_total_incl_vat=Decimal(str(invoice.base_total_incl_vat)),
        paid_total=Decimal(str(invoice.incoming_payment_total)),
        base_paid_total=Decimal(str(invoice.base_incoming_payment_total)),
        due_amount=Decimal(str(invoice.due_amount)),
        base_due_amount=Decimal(str(invoice.base_due_amount)),
        paid_status=InvoicePaidStatus(invoice.paid_status),
        status=InvoiceStatus(invoice.status),
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


async def _refund_payment_method_name(
    session: AsyncSession,
    company_id: uuid.UUID,
    payment_method_id: uuid.UUID | None,
) -> str | None:
    """Map Refund method ownership failures to the Step 7 error contract."""
    try:
        return await _payment_method_name(session, company_id, payment_method_id)
    except ValueError as exc:
        raise RefundSettlementError(
            "REFUND_PAYMENT_METHOD_INVALID",
            "The selected payment method is unavailable.",
        ) from exc


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


@dataclass
class LockedSettlementChain:
    """One canonical Quote/direct component held under the global lock order."""

    anchor: Invoice
    quote: Quote | None
    documents: list[Invoice]
    chain_sources: list[Invoice]
    credits: list[Invoice]
    corrections: list[InvoiceCorrection]
    credit_source_ids: dict[uuid.UUID, uuid.UUID]
    payments: list[Payment]


@dataclass(frozen=True)
class _LockedSettlementPrefix:
    """Canonical Quote/source/Credit prefix, held before cash locks.

    This deliberately small boundary is shared by mutation and output paths.
    Besides documenting the global lock order, it lets the concurrency suite
    place a real-PG barrier exactly between Credit and Payment locks.
    """

    anchor: Invoice
    quote: Quote | None
    positive_documents: list[Invoice]
    credits: list[Invoice]


@dataclass(frozen=True)
class RefundConfirmationProjection:
    """One locked, renderer-ready Refund Confirmation settlement snapshot.

    The unlocked payment lookup is deliberately only a seed for the canonical
    Quote/source/Credit/cash lock chain.  Callers must use the returned refund
    rather than the seed: a concurrent mutation may have changed or tombstoned
    it while the chain locks were being acquired.
    """

    refund: Payment
    credit: Invoice
    correction: InvoiceCorrection
    source: Invoice
    collection: RefundCollectionRead


async def _lock_settlement_chain_prefix(
    session: AsyncSession,
    *,
    anchor_invoice_id: uuid.UUID,
    company_id: uuid.UUID,
) -> _LockedSettlementPrefix:
    """Lock Quote -> positive documents -> Credits, before Payment/Refund."""
    probe = (
        await session.execute(
            select(Invoice.quote_id).where(
                Invoice.id == anchor_invoice_id, Invoice.company_id == company_id
            )
        )
    ).one_or_none()
    if probe is None:
        raise LookupError("Invoice not found.")
    quote_id = probe.quote_id
    quote = (
        await _load_quote(session, quote_id, company_id, lock=True)
        if quote_id is not None
        else None
    )
    if quote is not None:
        component_ids = list(
            (
                await session.execute(
                    select(Invoice.id)
                    .where(Invoice.company_id == company_id, Invoice.quote_id == quote.id)
                    .order_by(Invoice.id)
                )
            ).scalars()
        )
    else:
        component_ids = await direct_document_component_ids(
            session, company_id=company_id, invoice_id=anchor_invoice_id
        )
    positive_documents = list(
        (
            await session.execute(
                select(Invoice)
                .where(
                    Invoice.company_id == company_id,
                    Invoice.id.in_(component_ids),
                    Invoice.document_kind != InvoiceDocumentKind.CREDIT_NOTE,
                )
                .order_by(Invoice.id)
                .with_for_update()
            )
        ).scalars()
    )
    credits = list(
        (
            await session.execute(
                select(Invoice)
                .where(
                    Invoice.company_id == company_id,
                    Invoice.id.in_(component_ids),
                    Invoice.document_kind == InvoiceDocumentKind.CREDIT_NOTE,
                )
                .order_by(Invoice.id)
                .with_for_update()
            )
        ).scalars()
    )
    documents = [*positive_documents, *credits]
    anchor = next((item for item in documents if item.id == anchor_invoice_id), None)
    if anchor is None:
        raise LookupError("Invoice not found.")
    return _LockedSettlementPrefix(
        anchor=anchor,
        quote=quote,
        positive_documents=positive_documents,
        credits=credits,
    )


async def lock_settlement_chain(
    session: AsyncSession,
    *,
    anchor_invoice_id: uuid.UUID,
    company_id: uuid.UUID,
) -> LockedSettlementChain:
    """Lock Quote -> positive documents -> Credits -> cash -> snapshots.

    DRAFT positive documents are locked because they belong to the component,
    but only issued positive documents are returned as settlement sources.
    Direct chains reuse the exact Step 6 connected-component definition used
    by the document-chain projection.
    """
    prefix = await _lock_settlement_chain_prefix(
        session, anchor_invoice_id=anchor_invoice_id, company_id=company_id
    )
    anchor = prefix.anchor
    quote = prefix.quote
    positive_documents = prefix.positive_documents
    credits = prefix.credits
    documents = [*positive_documents, *credits]
    positive_ids = [item.id for item in positive_documents]
    credit_ids = [item.id for item in credits]
    cash_clause = or_(
        Payment.invoice_id.in_(positive_ids or [uuid.uuid4()]),
        Payment.credit_note_id.in_(credit_ids or [uuid.uuid4()]),
    )
    if quote is not None:
        cash_clause = or_(cash_clause, Payment.quote_id == quote.id)
    payments = list(
        (
            await session.execute(
                select(Payment)
                .where(Payment.company_id == company_id, Payment.deleted_at.is_(None), cash_clause)
                .options(selectinload(Payment.taxes))
                .order_by(Payment.payment_date, Payment.created_at, Payment.id)
                .with_for_update()
            )
        ).scalars()
    )
    await session.execute(
        select(FinalAdvanceApplication.id)
        .where(
            FinalAdvanceApplication.company_id == company_id,
            or_(
                FinalAdvanceApplication.final_invoice_id.in_(positive_ids or [uuid.uuid4()]),
                FinalAdvanceApplication.advance_invoice_id.in_(
                    positive_ids or [uuid.uuid4()]
                ),
            ),
        )
        .order_by(FinalAdvanceApplication.id)
        .with_for_update()
    )
    corrections = list(
        (
            await session.execute(
                select(InvoiceCorrection)
                .where(
                    InvoiceCorrection.company_id == company_id,
                    InvoiceCorrection.credit_note_id.in_(credit_ids or [uuid.uuid4()]),
                )
                .order_by(InvoiceCorrection.id)
                .with_for_update()
            )
        ).scalars()
    )
    correction_ids = [item.id for item in corrections]
    await session.execute(
        select(InvoiceCorrectionLine.id)
        .where(
            InvoiceCorrectionLine.correction_id.in_(correction_ids or [uuid.uuid4()])
        )
        .order_by(InvoiceCorrectionLine.id)
        .with_for_update()
    )
    return LockedSettlementChain(
        anchor=anchor,
        quote=quote,
        documents=documents,
        chain_sources=[
            item
            for item in positive_documents
            if InvoiceStatus(item.status) in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
        ],
        credits=credits,
        corrections=corrections,
        credit_source_ids={
            item.credit_note_id: item.source_invoice_id for item in corrections
        },
        payments=payments,
    )


async def _locked_refund_chain(
    session: AsyncSession, *, credit_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[Invoice, InvoiceCorrection, Invoice, LockedSettlementChain]:
    seed = (
        await session.execute(
            select(InvoiceCorrection.source_invoice_id).where(
                InvoiceCorrection.credit_note_id == credit_id,
                InvoiceCorrection.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if seed is None:
        raise LookupError("Credit Note not found.")
    context = await lock_settlement_chain(
        session, anchor_invoice_id=seed, company_id=company_id
    )
    credit = next((item for item in context.credits if item.id == credit_id), None)
    correction = next(
        (item for item in context.corrections if item.credit_note_id == credit_id), None
    )
    source = next((item for item in context.chain_sources if item.id == seed), None)
    if credit is None or correction is None or source is None:
        raise LookupError("Issued Credit Note not found.")
    if (
        InvoiceStatus(credit.status) not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
        or credit.issued_at is None
        or correction.issued_gross_amount is None
        or correction.issued_base_gross_amount is None
    ):
        raise RefundSettlementError(
            "REFUND_CREDIT_NOT_ISSUED",
            "Refund requires an issued Credit Note with its frozen aggregate.",
            status_code=409,
        )
    return credit, correction, source, context


@dataclass(frozen=True)
class SettlementResult:
    available_by_credit: dict[uuid.UUID, Decimal]
    base_available_by_credit: dict[uuid.UUID, Decimal]
    chain_refund_due_amount: Decimal
    base_chain_refund_due_amount: Decimal

    def __getitem__(self, credit_id: uuid.UUID) -> Decimal:
        return self.available_by_credit[credit_id]


def _raise_missing_credit_issued_at(credit: Invoice) -> datetime:
    """Enforce the issued-Credit ordering invariant instead of guessing."""
    raise RefundSettlementError(
        "REFUND_CREDIT_NOT_ISSUED",
        f"Issued Credit Note {credit.id} has no issued_at timestamp.",
        status_code=409,
    )


def _settle_refund_chain(
    *,
    source: Invoice,
    chain_sources: list[Invoice],
    credits: list[Invoice],
    credit_source_ids: dict[uuid.UUID, uuid.UUID],
    payments: list[Payment],
    quote: Quote | None,
) -> SettlementResult:
    """The sole refund settlement equation and issue-order entitlement allocator."""
    zero = Decimal("0")
    incoming = sum(
        (
            Decimal(str(p.amount))
            for p in payments
            if getattr(p, "deleted_at", None) is None
            and PaymentDirection(p.direction) == PaymentDirection.INCOMING
        ),
        zero,
    )
    base_incoming = sum(
        (
            Decimal(str(p.base_amount))
            for p in payments
            if getattr(p, "deleted_at", None) is None
            and PaymentDirection(p.direction) == PaymentDirection.INCOMING
        ),
        zero,
    )
    issued_credits = sorted(
        (
            credit
            for credit in credits
            if InvoiceStatus(credit.status)
            in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
        ),
        key=lambda item: (
            item.issued_at
            if item.issued_at is not None
            else (_raise_missing_credit_issued_at(item)),
            item.id,
        ),
    )
    issued_credit_ids = {credit.id for credit in issued_credits}
    refunds_by_credit: dict[uuid.UUID, Decimal] = {
        credit.id: zero for credit in issued_credits
    }
    base_refunds_by_credit: dict[uuid.UUID, Decimal] = {
        credit.id: zero for credit in issued_credits
    }
    for payment in payments:
        if (
            getattr(payment, "deleted_at", None) is not None
            or PaymentDirection(payment.direction) != PaymentDirection.REFUND
        ):
            continue
        if payment.credit_note_id not in issued_credit_ids:
            raise RefundSettlementError(
                "REFUND_CREDIT_NOT_ISSUED",
                "Every Refund must be linked to an issued Credit Note.",
                status_code=409,
            )
        assert payment.credit_note_id is not None
        refunds_by_credit[payment.credit_note_id] += Decimal(str(payment.amount))
        base_refunds_by_credit[payment.credit_note_id] += Decimal(
            str(payment.base_amount)
        )
    total_refunds = sum(refunds_by_credit.values(), zero)
    base_total_refunds = sum(base_refunds_by_credit.values(), zero)
    chain_charge = sum(
        (
            Decimal(str(item.payable_before_payments)) - Decimal(str(item.credited_total))
            for item in chain_sources
        ),
        zero,
    )
    base_chain_charge = sum(
        (
            Decimal(str(item.base_payable_before_payments))
            - Decimal(str(item.base_credited_total))
            for item in chain_sources
        ),
        zero,
    )
    chain_capacity_before_refunds = max(incoming - chain_charge, zero)
    base_chain_capacity_before_refunds = max(
        base_incoming - base_chain_charge, zero
    )
    source_credit_ids: dict[uuid.UUID, list[uuid.UUID]] = {
        item.id: [] for item in chain_sources
    }
    for credit_id, source_id in credit_source_ids.items():
        source_credit_ids.setdefault(source_id, []).append(credit_id)
    incoming_by_source: dict[uuid.UUID, Decimal] = {item.id: zero for item in chain_sources}
    base_incoming_by_source: dict[uuid.UUID, Decimal] = {
        item.id: zero for item in chain_sources
    }
    for payment in payments:
        if (
            getattr(payment, "deleted_at", None) is not None
            or PaymentDirection(payment.direction) != PaymentDirection.INCOMING
        ):
            continue
        payment_source_id = payment.invoice_id
        if payment_source_id is None and quote is not None:
            payment_source_id = quote.converted_invoice_id
        if payment_source_id in incoming_by_source:
            incoming_by_source[payment_source_id] += Decimal(str(payment.amount))
            base_incoming_by_source[payment_source_id] += Decimal(
                str(payment.base_amount)
            )
    refunds_by_source = {
        source_id: sum(
            (refunds_by_credit.get(credit_id, zero) for credit_id in credit_ids), zero
        )
        for source_id, credit_ids in source_credit_ids.items()
    }
    base_refunds_by_source = {
        source_id: sum(
            (
                base_refunds_by_credit.get(credit_id, zero)
                for credit_id in credit_ids
            ),
            zero,
        )
        for source_id, credit_ids in source_credit_ids.items()
    }
    for item in chain_sources:
        charge = Decimal(str(item.payable_before_payments)) - Decimal(str(item.credited_total))
        base_charge = Decimal(str(item.base_payable_before_payments)) - Decimal(
            str(item.base_credited_total)
        )
        source_incoming = incoming_by_source[item.id]
        base_source_incoming = base_incoming_by_source[item.id]
        source_refunds = refunds_by_source.get(item.id, zero)
        base_source_refunds = base_refunds_by_source.get(item.id, zero)
        item.incoming_payment_total = quantize_money(source_incoming)
        item.base_incoming_payment_total = quantize_money(base_source_incoming)
        item.refunded_total = quantize_money(source_refunds)
        item.base_refunded_total = quantize_money(base_source_refunds)
        item.due_amount = quantize_money(
            max(charge - source_incoming + source_refunds, zero)
        )
        item.base_due_amount = quantize_money(
            max(base_charge - base_source_incoming + base_source_refunds, zero)
        )
        item.refund_due_amount = quantize_money(
            max(source_incoming - source_refunds - charge, zero)
        )
        item.base_refund_due_amount = quantize_money(
            max(base_source_incoming - base_source_refunds - base_charge, zero)
        )
        item.settlement_status = (
            InvoiceSettlementStatus.REFUND_DUE
            if item.refund_due_amount > zero
            else InvoiceSettlementStatus.SETTLED
            if item.due_amount == zero
            else InvoiceSettlementStatus.PARTIALLY_SETTLED
            if source_incoming > zero
            else InvoiceSettlementStatus.OPEN
        )
    # Entitlement is assigned once in issue order, before *any* existing
    # Refund is subtracted.  This makes prior Refund rows subject to the same
    # source-local and global allocation as a new command: request order can
    # never move capacity from an earlier Credit to a later one.
    available: dict[uuid.UUID, Decimal] = {}
    base_available: dict[uuid.UUID, Decimal] = {}
    source_remaining = {
        item.id: max(
            incoming_by_source[item.id]
            - (
                Decimal(str(item.payable_before_payments))
                - Decimal(str(item.credited_total))
            ),
            zero,
        )
        for item in chain_sources
    }
    base_source_remaining = {
        item.id: max(
            base_incoming_by_source[item.id]
            - (
                Decimal(str(item.base_payable_before_payments))
                - Decimal(str(item.base_credited_total))
            ),
            zero,
        )
        for item in chain_sources
    }
    chain_remaining = chain_capacity_before_refunds
    base_chain_remaining = base_chain_capacity_before_refunds
    for credit in issued_credits:
        credit_source_id = credit_source_ids.get(credit.id)
        if credit_source_id not in source_remaining:
            raise RefundSettlementError(
                "REFUND_INVALID_RELATIONSHIP",
                "Issued Credit Note does not belong to a refundable charge source.",
            )
        assert credit_source_id is not None
        entitlement = Decimal(str(credit.total_incl_vat))
        base_entitlement = Decimal(str(credit.base_total_incl_vat))
        refunded = refunds_by_credit[credit.id]
        base_refunded = base_refunds_by_credit[credit.id]
        if refunded > entitlement:
            raise RefundSettlementError(
                "REFUND_ENTITLEMENT_EXCEEDED",
                "Refund exceeds the Credit Note's issued entitlement.",
            )
        if base_refunded > base_entitlement:
            raise RefundSettlementError(
                "REFUND_ENTITLEMENT_EXCEEDED",
                "Refund exceeds the Credit Note's issued base entitlement.",
            )
        allocated = min(
            entitlement,
            source_remaining[credit_source_id],
            chain_remaining,
        )
        base_allocated = min(
            base_entitlement,
            base_source_remaining[credit_source_id],
            base_chain_remaining,
        )
        if refunded > allocated or base_refunded > base_allocated:
            raise RefundSettlementError(
                "REFUND_COVERAGE_EXCEEDED",
                "Refund exceeds its issued-order source-local and chain cash coverage.",
            )
        available[credit.id] = quantize_money(allocated - refunded)
        base_available[credit.id] = quantize_money(base_allocated - base_refunded)
        source_remaining[credit_source_id] -= allocated
        chain_remaining -= allocated
        base_source_remaining[credit_source_id] -= base_allocated
        base_chain_remaining -= base_allocated
        credit.refunded_total = quantize_money(refunded)
        credit.base_refunded_total = quantize_money(base_refunded)
        credit.incoming_payment_total = quantize_money(zero)
        credit.base_incoming_payment_total = quantize_money(zero)
        credit.due_amount = quantize_money(zero)
        credit.base_due_amount = quantize_money(zero)
        credit.refund_due_amount = quantize_money(allocated - refunded)
        credit.base_refund_due_amount = quantize_money(base_allocated - base_refunded)
        credit.paid_status = InvoicePaidStatus.NOT_APPLICABLE
        credit.settlement_status = (
            InvoiceSettlementStatus.REFUND_DUE
            if available[credit.id] > zero or base_available[credit.id] > zero
            else InvoiceSettlementStatus.SETTLED
        )
    return SettlementResult(
        available_by_credit=available,
        base_available_by_credit=base_available,
        chain_refund_due_amount=quantize_money(
            chain_capacity_before_refunds - total_refunds
        ),
        base_chain_refund_due_amount=quantize_money(
            base_chain_capacity_before_refunds - base_total_refunds
        ),
    )


async def _refund_collection(
    session: AsyncSession,
    credit: Invoice,
    correction: InvoiceCorrection,
    source: Invoice,
    payments: list[Payment],
    settlement: SettlementResult,
) -> RefundCollectionRead:
    refunds = [p for p in payments if p.deleted_at is None and p.credit_note_id == credit.id]
    refunded = sum((Decimal(str(p.amount)) for p in refunds), Decimal("0"))
    base_refunded = sum(
        (Decimal(str(p.base_amount)) for p in refunds), Decimal("0")
    )
    if (
        InvoiceStatus(credit.status) not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
        or credit.issued_at is None
        or correction.issued_gross_amount is None
        or correction.issued_base_gross_amount is None
    ):
        raise RefundSettlementError(
            "REFUND_CREDIT_NOT_ISSUED",
            "Refund requires an issued Credit Note with its frozen aggregate.",
            status_code=409,
        )
    entitlement = Decimal(str(correction.issued_gross_amount))
    base_entitlement = Decimal(str(correction.issued_base_gross_amount))
    return RefundCollectionRead(
        credit_note_id=credit.id,
        credit_note_number=credit.invoice_number,
        source_invoice_id=source.id,
        currency=credit.currency,
        issued_entitlement=entitlement,
        base_issued_entitlement=base_entitlement,
        refunded_total=quantize_money(refunded),
        base_refunded_total=quantize_money(base_refunded),
        remaining_entitlement=quantize_money(
            max(entitlement - refunded, Decimal("0"))
        ),
        base_remaining_entitlement=quantize_money(
            max(base_entitlement - base_refunded, Decimal("0"))
        ),
        chain_refund_due_amount=settlement.chain_refund_due_amount,
        base_chain_refund_due_amount=settlement.base_chain_refund_due_amount,
        items=await _payment_reads(session, refunds),
    )


async def record_refund(
    session: AsyncSession, *, credit_note_id: uuid.UUID, company_id: uuid.UUID, body: PaymentInput, creator_id: uuid.UUID | None
) -> RefundCollectionRead:
    await set_rls_company(session, company_id)
    pre_available = await session.scalar(
        select(Invoice.refund_due_amount).where(
            Invoice.id == credit_note_id,
            Invoice.company_id == company_id,
            Invoice.document_kind == InvoiceDocumentKind.CREDIT_NOTE,
        )
    )
    credit, correction, source, context = await _locked_refund_chain(
        session, credit_id=credit_note_id, company_id=company_id
    )
    if body.payment_date < credit.invoice_date:
        raise RefundSettlementError(
            "REFUND_DATE_BEFORE_CREDIT",
            "Refund date cannot precede the Credit Note date.",
        )
    amount = quantize_money(body.amount)
    # Validate against the pre-mutation allocation, then recalculate after add.
    available = _settle_refund_chain(
        source=source,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    if amount > available[credit.id] or amount > available.base_available_by_credit[credit.id]:
        if pre_available is not None and Decimal(str(pre_available)) >= amount:
            raise SettlementConflictError(
                "Refund entitlement changed while the command was waiting for chain locks."
            )
        raise RefundSettlementError(
            "REFUND_COVERAGE_EXCEEDED",
            "Refund exceeds the available Credit Note entitlement or chain refund due.",
        )
    refund = Payment(
        company_id=company_id, credit_note_id=credit.id, direction=PaymentDirection.REFUND,
        payment_date=body.payment_date, amount=amount, base_amount=amount, currency=credit.currency,
        exchange_rate=Decimal("1"), payment_method_id=body.payment_method_id,
        payment_method_name=await _refund_payment_method_name(
            session, company_id, body.payment_method_id
        ),
        reference=body.reference, note=body.note, creator_id=creator_id, taxes=[],
    )
    session.add(refund)
    await session.flush()
    context.payments.append(refund)
    settlement = _settle_refund_chain(
        source=source,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    await append_document_chain_event(
        session, company_id=company_id,
        quote_id=context.quote.id if context.quote else None, invoice_id=credit.id,
        actor_user_id=creator_id, event_type=DocumentChainEventType.REFUND_CREATED,
        metadata={"payment_id": str(refund.id), "amount": amount},
    )
    # Both timestamps are server-generated.  Materialize them, then construct
    # the complete response while the canonical chain locks still protect the
    # settlement snapshot.  The commit happens only after no ORM access is
    # needed for the response.
    await session.refresh(refund, attribute_names=["created_at", "updated_at"])
    response = await _refund_collection(
        session, credit, correction, source, context.payments, settlement
    )
    await session.commit()
    return response


async def list_credit_refunds(session: AsyncSession, credit_note_id: uuid.UUID, company_id: uuid.UUID) -> RefundCollectionRead:
    await set_rls_company(session, company_id)
    credit, correction, source, context = await _locked_refund_chain(
        session, credit_id=credit_note_id, company_id=company_id
    )
    settlement = _settle_refund_chain(
        source=source,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    return await _refund_collection(
        session, credit, correction, source, context.payments, settlement
    )


async def refund_confirmation_remaining_entitlement(
    session: AsyncSession, *, credit_note_id: uuid.UUID, company_id: uuid.UUID,
) -> Decimal:
    """Return the Step 7 post-refund entitlement projection for output.

    This deliberately reuses the same locked collection projection as the
    public refunds endpoint.  PDF rendering receives a ready-to-display
    value; it never substitutes cash coverage or derives an amount itself.
    """
    collection = await list_credit_refunds(session, credit_note_id, company_id)
    return collection.remaining_entitlement


async def refund_confirmation_projection(
    session: AsyncSession, *, payment_id: uuid.UUID, company_id: uuid.UUID,
) -> RefundConfirmationProjection:
    """Load a live Refund Confirmation under the canonical settlement locks.

    Do not lock the Refund before ``_locked_refund_chain``: Refund mutations
    lock Quote/source/Credit before cash, and taking the opposite prefix here
    can deadlock preview/download/send against PUT or DELETE.  The preliminary
    lookup is intentionally non-locking and never supplies presentation data.
    """
    await set_rls_company(session, company_id)
    credit_seed = await session.scalar(
        select(Payment.credit_note_id).where(
            Payment.id == payment_id,
            Payment.company_id == company_id,
            Payment.direction == PaymentDirection.REFUND,
            Payment.deleted_at.is_(None),
        )
    )
    if credit_seed is None:
        raise LookupError("Payment not found.")
    credit, correction, source, context = await _locked_refund_chain(
        session, credit_id=credit_seed, company_id=company_id
    )
    refund = next((item for item in context.payments if item.id == payment_id), None)
    if (
        refund is None
        or PaymentDirection(refund.direction) != PaymentDirection.REFUND
        or refund.credit_note_id != credit.id
    ):
        # A DELETE or relink could have won while we waited for the prefix.
        raise LookupError("Payment not found.")
    settlement = _settle_refund_chain(
        source=source,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    collection = await _refund_collection(
        session, credit, correction, source, context.payments, settlement
    )
    return RefundConfirmationProjection(
        refund=refund,
        credit=credit,
        correction=correction,
        source=source,
        collection=collection,
    )


async def _mutate_refund(
    session: AsyncSession, *, payment_id: uuid.UUID, company_id: uuid.UUID,
    body: PaymentInput | None, deleted: bool, actor_user_id: uuid.UUID | None,
) -> PaymentMutationResponse:
    seed = (
        await session.execute(
            select(Payment.credit_note_id).where(
                Payment.id == payment_id, Payment.company_id == company_id,
                Payment.direction == PaymentDirection.REFUND,
                Payment.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if seed is None:
        raise LookupError("Payment not found.")
    credit, correction, source, context = await _locked_refund_chain(
        session, credit_id=seed, company_id=company_id
    )
    refund = next((p for p in context.payments if p.id == payment_id), None)
    if refund is None:
        raise LookupError("Payment not found.")
    if deleted:
        amount = Decimal(str(refund.amount))
        refund.deleted_at = datetime.now(UTC)
        context.payments.remove(refund)
        event_type = DocumentChainEventType.REFUND_DELETED
    else:
        assert body is not None
        if body.payment_date < credit.invoice_date:
            raise RefundSettlementError(
                "REFUND_DATE_BEFORE_CREDIT",
                "Refund date cannot precede the Credit Note date.",
            )
        amount = quantize_money(body.amount)
        payment_method_name = await _refund_payment_method_name(
            session, company_id, body.payment_method_id
        )
        refund.payment_date = body.payment_date
        refund.amount = amount
        refund.base_amount = amount
        refund.payment_method_id = body.payment_method_id
        refund.payment_method_name = payment_method_name
        refund.reference = body.reference
        refund.note = body.note
        event_type = DocumentChainEventType.REFUND_UPDATED
    await session.flush()
    # Recalculate only after the attempted mutation has joined the locked set:
    # an invalid edit/delete raises before commit and rolls every cache/row back.
    settlement = _settle_refund_chain(
        source=source,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    # All refunds on every issued Credit have just been checked by the engine.
    await append_document_chain_event(
        session, company_id=company_id,
        quote_id=context.quote.id if context.quote else None, invoice_id=credit.id,
        actor_user_id=actor_user_id, event_type=event_type,
        metadata={"payment_id": str(payment_id), "amount": amount},
    )
    if not deleted:
        await session.refresh(refund, attribute_names=["updated_at"])
    source_payments = _incoming_for_source(
        context.payments, source=source, quote=context.quote
    )
    invoice_response = await _build_invoice_response(
        session, source, source_payments
    )
    refund_response = await _refund_collection(
        session, credit, correction, source, context.payments, settlement
    )
    response = PaymentMutationResponse(
        payment_id=payment_id,
        deleted=deleted,
        invoice=invoice_response,
        refund=refund_response,
    )
    await session.commit()
    return response


def _incoming_for_source(
    payments: list[Payment], *, source: Invoice, quote: Quote | None
) -> list[Payment]:
    return [
        payment
        for payment in payments
        if PaymentDirection(payment.direction) == PaymentDirection.INCOMING
        and (
            payment.invoice_id == source.id
            or (
                payment.invoice_id is None
                and payment.quote_id is not None
                and quote is not None
                and quote.converted_invoice_id == source.id
            )
        )
    ]


def _refunds_for_source(
    payments: list[Payment], *, source_id: uuid.UUID, context: LockedSettlementChain
) -> list[Payment]:
    source_credit_ids = {
        credit_id
        for credit_id, correction_source_id in context.credit_source_ids.items()
        if correction_source_id == source_id
    }
    return [payment for payment in payments if payment.credit_note_id in source_credit_ids]


async def _mutate_chain_incoming(
    session: AsyncSession, *, payment_id: uuid.UUID, company_id: uuid.UUID,
    body: PaymentInput | None, deleted: bool, actor_user_id: uuid.UUID | None,
) -> PaymentMutationResponse | None:
    """Mutate invoice-bound incoming cash under the complete chain lock.

    A still-unconverted Quote payment has no Invoice anchor and stays on its
    established Quote-only path.  Every invoice-bound mutation, with or
    without a Credit on that exact source, uses the unified D15 engine.
    """
    seed = (
        await session.execute(
            select(Payment.invoice_id, Payment.quote_id)
            .where(Payment.id == payment_id, Payment.company_id == company_id,
                   Payment.direction == PaymentDirection.INCOMING,
                   Payment.deleted_at.is_(None))
        )
    ).one_or_none()
    if seed is None:
        return None
    # Quote provenance is permanent.  Lock that parent first, then read its
    # current conversion pointer: concurrent DRAFT deletion either finishes
    # first and returns this mutation to Quote-only continuation, or waits
    # until the complete converted chain mutation finishes.
    if seed.quote_id is not None:
        locked_quote = await _load_quote(session, seed.quote_id, company_id, lock=True)
        source_id = locked_quote.converted_invoice_id
    else:
        source_id = seed.invoice_id
    if source_id is None:
        return None
    context = await lock_settlement_chain(
        session, anchor_invoice_id=source_id, company_id=company_id
    )
    source = next((item for item in context.chain_sources if item.id == source_id), None)
    if source is None:
        # A converted receipt-only Quote may still point at a DRAFT Final.  It
        # has no issued charge/credit/refund settlement yet, so retain the
        # established Quote tax-allocation path.  The canonical component has
        # already been locked before returning to that path.
        if context.anchor.id == source_id and InvoiceStatus(context.anchor.status) == InvoiceStatus.DRAFT:
            return None
        raise ValueError("Incoming payment source must be an issued charge invoice.")
    payment = next((item for item in context.payments if item.id == payment_id), None)
    if payment is None:
        raise LookupError("Payment not found.")
    prior_amount = Decimal(str(payment.amount))
    prior_source_incoming = sum(
        (
            Decimal(str(item.amount))
            for item in _incoming_for_source(
                context.payments, source=source, quote=context.quote
            )
        ),
        Decimal("0"),
    )
    prior_base_source_incoming = sum(
        (
            Decimal(str(item.base_amount))
            for item in _incoming_for_source(
                context.payments, source=source, quote=context.quote
            )
        ),
        Decimal("0"),
    )
    if deleted:
        amount = prior_amount
        await session.delete(payment)
        context.payments.remove(payment)
    else:
        assert body is not None
        amount = quantize_to_minor_unit(body.amount) if payment.quote_id is not None else quantize_money(body.amount)
        if payment.quote_id is not None and body.payment_date > source.invoice_date:
            raise ValueError(
                "A quote-origin payment date cannot be later than the final invoice date."
            )
        # Current Payment input is intentionally single-base-currency.  Use
        # the same persisted rule as every existing incoming payment.
        payment.payment_date = body.payment_date
        payment.amount = amount
        payment.base_amount = amount
        payment.payment_method_id = body.payment_method_id
        payment.payment_method_name = await _payment_method_name(session, company_id, body.payment_method_id)
        payment.reference = body.reference
        payment.note = body.note
    await session.flush()
    incoming_rows = _incoming_for_source(
        context.payments, source=source, quote=context.quote
    )
    incoming = sum(
        (Decimal(str(item.amount)) for item in incoming_rows),
        Decimal("0"),
    )
    base_incoming = sum(
        (Decimal(str(item.base_amount)) for item in incoming_rows), Decimal("0")
    )
    charge = Decimal(str(source.payable_before_payments)) - Decimal(str(source.credited_total))
    base_charge = Decimal(str(source.base_payable_before_payments)) - Decimal(
        str(source.base_credited_total)
    )
    source_refunds = _refunds_for_source(
        context.payments, source_id=source.id, context=context
    )
    refund_total = sum(
        (Decimal(str(item.amount)) for item in source_refunds), Decimal("0")
    )
    base_refund_total = sum(
        (Decimal(str(item.base_amount)) for item in source_refunds), Decimal("0")
    )
    if incoming > max(charge + refund_total, prior_source_incoming):
        raise ValueError("Payment exceeds the outstanding amount after this edit.")
    if base_incoming > max(base_charge + base_refund_total, prior_base_source_incoming):
        raise ValueError("Payment exceeds the outstanding base amount after this edit.")
    state = recompute_payment_state(
        _payment_charge(source),
        Decimal(str(source.base_payable_before_payments)),
        incoming_rows,
        InvoiceStatus(source.status),
    )
    source.paid_status = state.paid_status
    source.status = state.new_status
    _settle_refund_chain(
        source=source,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    if context.quote is not None:
        quote_rows = [
            item for item in context.payments if item.quote_id == context.quote.id
        ]
        quote_rows.sort(key=lambda item: (item.payment_date, item.created_at, item.id))
        await recompute_quote_payment_taxes(session, context.quote, quote_rows)
        await validate_invoice_tax_coverage(session, source)
    event_type = (
        DocumentChainEventType.QUOTE_PAYMENT_DELETED if deleted and payment.quote_id is not None else
        DocumentChainEventType.INVOICE_PAYMENT_DELETED if deleted else
        DocumentChainEventType.QUOTE_PAYMENT_UPDATED if payment.quote_id is not None else
        DocumentChainEventType.INVOICE_PAYMENT_UPDATED
    )
    await append_document_chain_event(
        session, company_id=company_id,
        quote_id=context.quote.id if context.quote else None, invoice_id=source.id,
        actor_user_id=actor_user_id, event_type=event_type,
        metadata={"payment_id": str(payment_id), "amount": amount},
    )
    if not deleted:
        await session.refresh(payment, attribute_names=["updated_at"])
    quote_response = (
        await _build_quote_response(session, context.quote, quote_rows)
        if context.quote is not None
        else None
    )
    invoice_response = await _build_invoice_response(session, source, incoming_rows)
    response = PaymentMutationResponse(
        payment_id=payment_id,
        deleted=deleted,
        quote=quote_response,
        invoice=invoice_response,
    )
    await session.commit()
    return response


async def record_payment(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    body: PaymentInput,
    creator_id: uuid.UUID | None,
) -> InvoicePaymentsResponse:
    await set_rls_company(session, company_id)
    context = await lock_settlement_chain(
        session, anchor_invoice_id=invoice_id, company_id=company_id
    )
    invoice = context.anchor
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
        taxes=[],
    )
    session.add(payment)
    await session.flush()
    context.payments.append(payment)
    payments = _incoming_for_source(
        context.payments, source=invoice, quote=context.quote
    )
    state = recompute_payment_state(
        _payment_charge(invoice),
        Decimal(str(invoice.base_payable_before_payments)),
        payments,
        InvoiceStatus(invoice.status),
    )
    refunds = _refunds_for_source(
        context.payments, source_id=invoice.id, context=context
    )
    refund_total = sum(
        (Decimal(str(item.amount)) for item in refunds), Decimal("0")
    )
    base_refund_total = sum(
        (Decimal(str(item.base_amount)) for item in refunds), Decimal("0")
    )
    if state.paid_total > _remaining_payment_charge(invoice) + refund_total:
        raise ValueError(
            "Payment exceeds the outstanding amount "
            "(cumulative payments would exceed the invoice total)."
        )
    if state.base_paid_total > (
        Decimal(str(invoice.base_payable_before_payments))
        - Decimal(str(invoice.base_credited_total))
        + base_refund_total
    ):
        raise ValueError(
            "Payment exceeds the outstanding base amount "
            "(cumulative payments would exceed the invoice total)."
        )
    invoice.paid_status = state.paid_status
    invoice.status = state.new_status
    _settle_refund_chain(
        source=invoice,
        chain_sources=context.chain_sources,
        credits=context.credits,
        credit_source_ids=context.credit_source_ids,
        payments=context.payments,
        quote=context.quote,
    )
    await append_document_chain_event(
        session,
        company_id=company_id,
        invoice_id=invoice.id,
        actor_user_id=creator_id,
        event_type=DocumentChainEventType.INVOICE_PAYMENT_CREATED,
        metadata={"payment_id": str(payment.id), "amount": Decimal(str(payment.amount))},
    )
    await session.refresh(payment, attribute_names=["created_at", "updated_at"])
    response = await _build_invoice_response(session, invoice, payments)
    await session.commit()
    return response


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
        taxes=[],
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
    await session.refresh(payment, attribute_names=["created_at", "updated_at"])
    response = await _build_quote_response(session, quote, payments)
    await session.commit()
    return response


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
        .where(Payment.id == payment_id, Payment.company_id == company_id, Payment.deleted_at.is_(None))
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
            Payment.id == payment_id, Payment.company_id == company_id, Payment.deleted_at.is_(None)
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
        .where(Payment.id == payment_id, Payment.company_id == company_id, Payment.deleted_at.is_(None))
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
    direction = await session.scalar(
        select(Payment.direction).where(Payment.id == payment_id, Payment.company_id == company_id, Payment.deleted_at.is_(None))
    )
    if direction is None:
        raise LookupError("Payment not found.")
    if PaymentDirection(direction) == PaymentDirection.REFUND:
        return await _mutate_refund(
            session, payment_id=payment_id, company_id=company_id, body=body,
            deleted=False, actor_user_id=actor_user_id,
        )
    credit_safe = await _mutate_chain_incoming(
        session, payment_id=payment_id, company_id=company_id, body=body,
        deleted=False, actor_user_id=actor_user_id,
    )
    if credit_safe is not None:
        return credit_safe
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
    await session.refresh(payment, attribute_names=["updated_at"])
    response = PaymentMutationResponse(
        payment_id=payment_id,
        deleted=False,
        quote=(
            await _build_quote_response(session, quote, quote_payments or [])
            if quote is not None
            else None
        ),
        invoice=(
            await _build_invoice_response(session, invoice, invoice_payments or [])
            if invoice is not None
            else None
        ),
    )
    await session.commit()
    return response


async def delete_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> PaymentMutationResponse:
    await set_rls_company(session, company_id)
    direction = await session.scalar(
        select(Payment.direction).where(Payment.id == payment_id, Payment.company_id == company_id, Payment.deleted_at.is_(None))
    )
    if direction is None:
        raise LookupError("Payment not found.")
    if PaymentDirection(direction) == PaymentDirection.REFUND:
        return await _mutate_refund(
            session, payment_id=payment_id, company_id=company_id, body=None,
            deleted=True, actor_user_id=actor_user_id,
        )
    credit_safe = await _mutate_chain_incoming(
        session, payment_id=payment_id, company_id=company_id, body=None,
        deleted=True, actor_user_id=actor_user_id,
    )
    if credit_safe is not None:
        return credit_safe
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
    response = PaymentMutationResponse(
        payment_id=payment_id,
        deleted=True,
        quote=(
            await _build_quote_response(session, quote, quote_payments or [])
            if quote is not None
            else None
        ),
        invoice=(
            await _build_invoice_response(session, invoice, invoice_payments or [])
            if invoice is not None
            else None
        ),
    )
    await session.commit()
    return response


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
    linked_invoice = aliased(Invoice)
    linked_invoice_id = func.coalesce(Payment.invoice_id, Payment.credit_note_id)
    customer_link = func.coalesce(linked_invoice.customer_id, Quote.customer_id)
    base = (
        select(Payment)
        .outerjoin(linked_invoice, linked_invoice_id == linked_invoice.id)
        .outerjoin(Quote, Payment.quote_id == Quote.id)
        .join(Customer, Customer.id == customer_link)
        .where(Payment.company_id == company_id, Payment.deleted_at.is_(None))
    )
    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                linked_invoice.invoice_number.ilike(like),
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
        base = base.where(linked_invoice.document_kind == document_kind)
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
    credit_map = await _credit_number_map(session, payments)
    invoice_ids = {
        invoice_id
        for payment in payments
        for invoice_id in (payment.invoice_id, payment.credit_note_id)
        if invoice_id is not None
    }
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
        ) or (
            customer_by_invoice.get(payment.credit_note_id)
            if payment.credit_note_id is not None
            else None
        ) or (customer_by_quote.get(payment.quote_id) if payment.quote_id is not None else None)
        if linked_customer_id is None:
            continue
        items.append(
            PaymentListItem(
                id=payment.id,
                origin_type=(
                    "CREDIT_NOTE"
                    if payment.credit_note_id is not None
                    else ("QUOTE" if payment.quote_id is not None else "INVOICE")
                ),
                invoice_id=payment.invoice_id,
                invoice_number=(
                    invoice_map.get(payment.invoice_id) if payment.invoice_id is not None else None
                ),
                quote_id=payment.quote_id,
                quote_number=(
                    quote_map.get(payment.quote_id) if payment.quote_id is not None else None
                ),
                direction=PaymentDirection(payment.direction),
                credit_note_id=payment.credit_note_id,
                credit_note_number=(
                    credit_map.get(payment.credit_note_id)
                    if payment.credit_note_id is not None
                    else None
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
