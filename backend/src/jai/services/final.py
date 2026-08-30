"""Formal Final creation and guarded editing (M12 Step 4).

The Final persists the editable whole-project invoice separately from the
immutable Advance application rows.  Cash on an Advance is intentionally not
read here: applications are charge snapshots, never payments.
"""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jai.db import set_rls_company
from jai.models._enums import (
    DocumentChainEventType,
    InvoiceCreditStatus,
    InvoiceDocumentKind,
    InvoicePaidStatus,
    InvoiceSettlementStatus,
    InvoiceStatus,
    QuoteSettlementMode,
    QuoteStatus,
    VatTreatmentEffect,
    VatTreatmentSide,
)
from jai.models.document import (
    FinalAdvanceApplication,
    FinalAdvanceApplicationTax,
    InvoiceCorrection,
    InvoiceCorrectionLine,
    InvoiceCreditBasisLine,
)
from jai.models.invoice import Invoice, InvoiceLine, InvoiceLineTax, InvoiceTax
from jai.models.quote import Quote, QuoteLine
from jai.models.vat import VatTreatment
from jai.schemas.invoice import FinalDraftCreate, InvoiceRead, InvoiceWrite
from jai.services.advance import (
    AdvanceBucket,
    is_retryable_transaction_conflict,
    subtract_exact_advance_credits,
)
from jai.services.document_chain import ModeConflictError, append_document_chain_event
from jai.services.invoice import (
    _load_invoice_read,
    _load_vat_rates,
    _validate_line_fks,
)
from jai.services.money import quantize_to_minor_unit


class FinalConflictError(ValueError):
    code = "FINAL_CONFLICT"


class FinalValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Step 5 owns the normalized Credit Note query.  Step 4 deliberately keeps its
# dependency at this narrow, immutable source-bucket boundary: a caller can
# supply the issued Advance's exact credited net/VAT snapshots, but can never
# turn ``credited_total`` into a made-up VAT split.
ExactAdvanceCreditProvider = Callable[
    [AsyncSession, Invoice], Awaitable[list[AdvanceBucket]]
]


async def _clone_quote_snapshot_to_final(
    session: AsyncSession,
    *,
    quote: Quote,
    body: FinalDraftCreate,
    creator_id: uuid.UUID | None,
) -> Invoice:
    """Copy the accepted Quote's persisted document, line and tax snapshots verbatim."""
    final = Invoice(
        company_id=quote.company_id, customer_id=quote.customer_id,
        reference_number=body.reference_number or quote.reference_number,
        invoice_date=body.invoice_date, due_date=body.due_date,
        supply_or_advance_date=body.supply_or_advance_date,
        document_kind=InvoiceDocumentKind.FINAL, quote_id=quote.id,
        status=InvoiceStatus.DRAFT, paid_status=InvoicePaidStatus.UNPAID,
        currency=quote.currency, exchange_rate=quote.exchange_rate,
        tax_mode=quote.tax_mode, amounts_include_vat=quote.amounts_include_vat,
        vat_treatment_id=quote.vat_treatment_id, document_vat_rate_id=quote.document_vat_rate_id,
        vat_treatment_code=quote.vat_treatment_code, vat_treatment_label=quote.vat_treatment_label,
        vat_treatment_effect=quote.vat_treatment_effect,
        vat_treatment_requires_icp=quote.vat_treatment_requires_icp,
        discount_type=quote.discount_type, discount_value=quote.discount_value,
        document_discount_amount=quote.document_discount_amount,
        subtotal_excl_vat=quote.subtotal_excl_vat, line_discount_total=quote.line_discount_total,
        taxable_amount=quote.taxable_amount, vat_total=quote.vat_total,
        total_incl_vat=quote.total_incl_vat, due_amount=quote.total_incl_vat,
        payable_before_payments=quote.total_incl_vat, incoming_payment_total=Decimal("0"),
        credited_total=Decimal("0"), refunded_total=Decimal("0"), refund_due_amount=Decimal("0"),
        settlement_status=InvoiceSettlementStatus.OPEN,
        credit_status=InvoiceCreditStatus.NOT_CREDITED,
        base_subtotal_excl_vat=quote.base_subtotal_excl_vat,
        base_line_discount_total=quote.base_line_discount_total,
        base_taxable_amount=quote.base_taxable_amount, base_vat_total=quote.base_vat_total,
        base_total_incl_vat=quote.base_total_incl_vat, base_due_amount=quote.base_total_incl_vat,
        base_payable_before_payments=quote.base_total_incl_vat,
        base_incoming_payment_total=Decimal("0"), base_credited_total=Decimal("0"),
        base_refunded_total=Decimal("0"), base_refund_due_amount=Decimal("0"),
        notes=quote.notes, warranty_text=quote.warranty_text, terms_text=quote.terms_text,
        bank_text=quote.bank_text, payment_terms_text=quote.payment_terms_text,
        creator_id=creator_id, final_original_taxable_amount=quote.taxable_amount,
        final_original_vat_amount=quote.vat_total, final_original_gross_amount=quote.total_incl_vat,
    )
    session.add(final)
    await session.flush()
    for quote_line in quote.lines:
        line = InvoiceLine(
            invoice_id=final.id, sort_order=quote_line.sort_order, product_id=quote_line.product_id,
            name=quote_line.name, description=quote_line.description, quantity=quote_line.quantity,
            unit_id=quote_line.unit_id, unit_name=quote_line.unit_name, unit_price=quote_line.unit_price,
            discount_type=quote_line.discount_type, discount_value=quote_line.discount_value,
            vat_rate_id=quote_line.vat_rate_id, vat_rate_label=quote_line.vat_rate_label,
            vat_rate_percent=quote_line.vat_rate_percent,
            subtotal_excl_vat=quote_line.subtotal_excl_vat,
            subtotal_incl_vat=quote_line.subtotal_incl_vat,
            line_discount_amount=quote_line.line_discount_amount,
            document_discount_share=quote_line.document_discount_share,
            taxable_amount=quote_line.taxable_amount, vat_total=quote_line.vat_total,
            total_incl_vat=quote_line.total_incl_vat,
        )
        session.add(line)
        await session.flush()
        for quote_tax in quote_line.line_taxes:
            session.add(InvoiceLineTax(
                invoice_line_id=line.id, vat_rate_id=quote_tax.vat_rate_id,
                vat_rate_label=quote_tax.vat_rate_label, vat_rate_percent=quote_tax.vat_rate_percent,
                effective_vat_percent=quote_tax.effective_vat_percent,
                taxable_amount=quote_tax.taxable_amount, tax_amount=quote_tax.tax_amount,
            ))
    for document_quote_tax in quote.taxes:
        session.add(InvoiceTax(
            invoice_id=final.id, vat_rate_id=document_quote_tax.vat_rate_id,
            vat_rate_label=document_quote_tax.vat_rate_label,
            vat_rate_percent=document_quote_tax.vat_rate_percent,
            effective_vat_percent=document_quote_tax.effective_vat_percent,
            taxable_amount=document_quote_tax.taxable_amount,
            tax_amount=document_quote_tax.tax_amount,
        ))
    await session.flush()
    return final


async def _locked_quote(session: AsyncSession, company_id: uuid.UUID, quote_id: uuid.UUID) -> Quote:
    quote = (
        await session.execute(
            select(Quote)
            .where(Quote.id == quote_id, Quote.company_id == company_id)
            .options(
                selectinload(Quote.lines).selectinload(QuoteLine.line_taxes),
                selectinload(Quote.taxes),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if quote is None:
        raise LookupError("Quote not found.")
    if QuoteSettlementMode(quote.settlement_mode) in {
        QuoteSettlementMode.DIRECT_INVOICE,
        QuoteSettlementMode.RECEIPT_ONLY,
    }:
        raise ModeConflictError("Quote settlement mode does not permit a Formal Final.")
    if QuoteSettlementMode(quote.settlement_mode) != QuoteSettlementMode.FORMAL_ADVANCE:
        raise FinalConflictError("Quote is not locked to FORMAL_ADVANCE.")
    if QuoteStatus(quote.status) != QuoteStatus.ACCEPTED:
        raise FinalValidationError("FINAL_INVALID_QUOTE_STATUS", "Quote must be ACCEPTED.")
    if quote.vat_treatment_code != "NL_DOMESTIC":
        raise FinalValidationError(
            "FINAL_UNSUPPORTED_VAT_TREATMENT", "Final invoices require NL_DOMESTIC."
        )
    return quote


async def lock_formal_charge_invoices(
    session: AsyncSession, quote: Quote
) -> list[Invoice]:
    """Lock the formal charge prefix in the one chain-wide canonical order.

    This is intentionally broader than the rows immediately needed by one
    command: a Final and every Advance in the Quote are part of the same
    charge/source prefix.  Future supplemental Standard sources can extend
    this helper without changing the Quote -> Invoice ordering contract.
    """
    stmt = (
        select(Invoice)
        .where(
            Invoice.quote_id == quote.id,
            Invoice.document_kind.in_([
                InvoiceDocumentKind.ADVANCE,
                InvoiceDocumentKind.FINAL,
            ]),
        )
        .order_by(Invoice.id)
        .with_for_update()
    )
    return list((await session.execute(stmt)).scalars())


def _issued_advances(locked_invoices: Sequence[Invoice]) -> list[Invoice]:
    """Read issued Advances from the already canonically locked chain prefix."""
    return sorted(
        (
            invoice
            for invoice in locked_invoices
            if InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.ADVANCE
            and InvoiceStatus(invoice.status) in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
        ),
        key=lambda invoice: (invoice.invoice_date, invoice.invoice_number or "", invoice.id),
    )


def _has_open_advance(locked_invoices: Sequence[Invoice]) -> bool:
    return any(
        InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.ADVANCE
        and InvoiceStatus(invoice.status) == InvoiceStatus.DRAFT
        for invoice in locked_invoices
    )


def _final_exists(locked_invoices: Sequence[Invoice]) -> bool:
    return any(
        InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.FINAL
        for invoice in locked_invoices
    )


async def _advance_tax_rows(
    session: AsyncSession,
    advance: Invoice,
    *,
    exact_credit_provider: ExactAdvanceCreditProvider | None = None,
) -> list[tuple[uuid.UUID, str, Decimal, Decimal, Decimal]]:
    """Return issued buckets less immutable pre-Final Credit snapshots."""
    rows: list[InvoiceLineTax] = list(
        (
            await session.execute(
                select(InvoiceLineTax)
                .join(InvoiceLine)
                .where(InvoiceLine.invoice_id == advance.id)
                .order_by(InvoiceLine.sort_order, InvoiceLineTax.id)
            )
        ).scalars()
    )
    issued = [
        (
            r.vat_rate_id,
            r.vat_rate_label,
            Decimal(str(r.vat_rate_percent)),
            Decimal(str(r.taxable_amount)),
            Decimal(str(r.tax_amount)),
        )
        for r in rows
    ]
    net = net_advance_application_buckets(
        [AdvanceBucket(*row) for row in issued],
        await (exact_credit_provider or _exact_pre_final_credit_buckets)(session, advance),
    )
    return [
        (
            row.vat_rate_id,
            row.vat_rate_label,
            row.vat_rate_percent,
            row.taxable_amount,
            row.vat_amount,
        )
        for row in net
    ]


async def _exact_pre_final_credit_buckets(
    session: AsyncSession, advance: Invoice
) -> list[AdvanceBucket]:
    """Return an Advance's exact issued Credit buckets before Final creation."""
    credit = Invoice.__table__.alias("final_credit_invoice")
    rows = (
        await session.execute(
            select(InvoiceCreditBasisLine, InvoiceCorrectionLine)
            .join(
                InvoiceCorrectionLine,
                InvoiceCorrectionLine.source_basis_line_id == InvoiceCreditBasisLine.id,
            )
            .join(InvoiceCorrection, InvoiceCorrection.id == InvoiceCorrectionLine.correction_id)
            .join(credit, credit.c.id == InvoiceCorrection.credit_note_id)
            .where(
                InvoiceCorrection.source_invoice_id == advance.id,
                credit.c.status.in_([InvoiceStatus.SENT, InvoiceStatus.COMPLETED]),
            )
        )
    ).all()
    grouped: dict[tuple[uuid.UUID, str, Decimal], list[Decimal]] = {}
    for basis, line in rows:
        if basis.vat_rate_id is None or basis.vat_rate_label is None or basis.vat_rate_percent is None:
            raise FinalValidationError(
                "FINAL_CREDIT_BUCKET_INVALID", "Issued Advance Credit lacks a frozen VAT bucket."
            )
        key = (basis.vat_rate_id, basis.vat_rate_label, Decimal(str(basis.vat_rate_percent)))
        amounts = grouped.setdefault(key, [Decimal("0"), Decimal("0")])
        amounts[0] += Decimal(str(line.net_amount))
        amounts[1] += Decimal(str(line.vat_amount))
    return [
        AdvanceBucket(key[0], key[1], key[2], amounts[0], amounts[1])
        for key, amounts in sorted(grouped.items(), key=lambda item: (item[0][2], item[0][1], item[0][0]))
    ]


def net_advance_application_buckets(
    issued: list[AdvanceBucket], exact_pre_final_credits: list[AdvanceBucket]
) -> list[AdvanceBucket]:
    """Pure immutable source-bucket netting used by Final applications."""
    return subtract_exact_advance_credits(issued, exact_pre_final_credits)


async def _persist_applications(
    session: AsyncSession,
    final: Invoice,
    advances: list[Invoice],
    *,
    exact_credit_provider: ExactAdvanceCreditProvider | None = None,
) -> Decimal:
    application_total = Decimal("0")
    for order, advance in enumerate(advances):
        buckets = await _advance_tax_rows(
            session, advance, exact_credit_provider=exact_credit_provider
        )
        if not buckets:
            continue
        net = sum((row[3] for row in buckets), Decimal("0"))
        vat = sum((row[4] for row in buckets), Decimal("0"))
        gross = net + vat
        if gross == 0:
            continue
        app = FinalAdvanceApplication(
            company_id=final.company_id,
            final_invoice_id=final.id,
            advance_invoice_id=advance.id,
            sort_order=order,
            advance_invoice_date=advance.invoice_date,
            advance_invoice_number=advance.invoice_number or "",
            taxable_amount=net,
            vat_amount=vat,
            gross_amount=gross,
            base_taxable_amount=net,
            base_vat_amount=vat,
            base_gross_amount=gross,
        )
        session.add(app)
        await session.flush()
        for bucket_order, (rate_id, label, percent, taxable, tax) in enumerate(buckets):
            if taxable + tax == 0:
                continue
            session.add(
                FinalAdvanceApplicationTax(
                    company_id=final.company_id,
                    application_id=app.id,
                    sort_order=bucket_order,
                    source_vat_rate_id=rate_id,
                    source_vat_rate_label=label,
                    source_vat_rate_percent=percent,
                    vat_treatment_code=final.vat_treatment_code,
                    vat_treatment_effect=final.vat_treatment_effect,
                    vat_treatment_requires_icp=final.vat_treatment_requires_icp,
                    taxable_amount=taxable,
                    vat_amount=tax,
                    gross_amount=taxable + tax,
                    base_taxable_amount=taxable,
                    base_vat_amount=tax,
                    base_gross_amount=taxable + tax,
                )
            )
        application_total += gross
    await session.flush()
    return quantize_to_minor_unit(application_total)


async def _application_buckets(
    session: AsyncSession, final_id: uuid.UUID
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    rows: list[FinalAdvanceApplicationTax] = list(
        (
            await session.execute(
                select(FinalAdvanceApplicationTax)
                .join(FinalAdvanceApplication)
                .where(FinalAdvanceApplication.final_invoice_id == final_id)
                .order_by(
                    FinalAdvanceApplication.sort_order,
                    FinalAdvanceApplicationTax.sort_order,
                    FinalAdvanceApplicationTax.id,
                )
            )
        ).scalars()
    )
    result: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for row in rows:
        net, vat = result.get(row.source_vat_rate_id, (Decimal("0"), Decimal("0")))
        result[row.source_vat_rate_id] = (
            net + Decimal(str(row.taxable_amount)),
            vat + Decimal(str(row.vat_amount)),
        )
    return result


async def _final_buckets(
    session: AsyncSession, final: Invoice
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    if final.tax_mode.value == "DOCUMENT":
        rows = list(
            (
                await session.execute(select(InvoiceTax).where(InvoiceTax.invoice_id == final.id))
            ).scalars()
        )
        return {
            r.vat_rate_id: (Decimal(str(r.taxable_amount)), Decimal(str(r.tax_amount)))
            for r in rows
        }
    line_rows: list[InvoiceLineTax] = list(
        (
            await session.execute(
                select(InvoiceLineTax).join(InvoiceLine).where(InvoiceLine.invoice_id == final.id)
            )
        ).scalars()
    )
    aggregate: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for row in line_rows:
        net, vat = aggregate.get(row.vat_rate_id, (Decimal("0"), Decimal("0")))
        aggregate[row.vat_rate_id] = (
            net + Decimal(str(row.taxable_amount)),
            vat + Decimal(str(row.tax_amount)),
        )
    return aggregate


async def validate_final_draft(session: AsyncSession, *, quote: Quote, final: Invoice) -> Decimal:
    if final.customer_id != quote.customer_id or final.currency != quote.currency:
        raise FinalValidationError(
            "FINAL_IMMUTABLE_CONTEXT", "Final customer and currency are fixed by its Quote."
        )
    if (
        final.vat_treatment_code != "NL_DOMESTIC"
        or final.vat_treatment_id != quote.vat_treatment_id
    ):
        raise FinalValidationError(
            "FINAL_IMMUTABLE_TREATMENT", "Final VAT treatment is fixed by its Quote."
        )
    applications = await _application_buckets(session, final.id)
    buckets = await _final_buckets(session, final)
    for rate_id, (app_net, app_vat) in applications.items():
        target = buckets.get(rate_id)
        if target is None:
            raise FinalValidationError(
                "FINAL_UNCOVERED_VAT_BUCKET", "Edited Final must cover every applied VAT bucket."
            )
        if target[0] < app_net or target[1] < app_vat:
            raise FinalValidationError(
                "FINAL_NEGATIVE_BUCKET_RESIDUAL",
                "Edited Final cannot leave a negative VAT bucket residual.",
            )
    application_total = sum((net + vat for net, vat in applications.values()), Decimal("0"))
    residual = quantize_to_minor_unit(Decimal(str(final.total_incl_vat)) - application_total)
    if residual < 0:
        raise FinalValidationError(
            "FINAL_NEGATIVE_RESIDUAL", "Final gross total cannot be lower than frozen applications."
        )
    app_dates: list[date] = list(
        (
            await session.execute(
                select(FinalAdvanceApplication.advance_invoice_date).where(
                    FinalAdvanceApplication.final_invoice_id == final.id
                )
            )
        ).scalars()
    )
    if app_dates and final.invoice_date < max(app_dates):
        raise FinalValidationError(
            "FINAL_INVALID_DATE", "Final date cannot precede an applied Advance."
        )
    return residual


def _set_residual(final: Invoice, residual: Decimal) -> None:
    final.payable_before_payments = residual
    final.base_payable_before_payments = residual
    final.due_amount = residual
    final.base_due_amount = residual
    final.incoming_payment_total = Decimal("0")
    final.base_incoming_payment_total = Decimal("0")
    final.paid_status = InvoicePaidStatus.UNPAID if residual else InvoicePaidStatus.PAID
    final.settlement_status = (
        InvoiceSettlementStatus.OPEN if residual else InvoiceSettlementStatus.SETTLED
    )


def _quote_treatment_snapshot(quote: Quote) -> VatTreatment:
    """Build the immutable treatment input used while repricing a Final.

    A Final is a guarded whole-project edit of an accepted Quote.  Its VAT
    treatment is therefore a persisted Quote snapshot, rather than a mutable
    dictionary row whose label/effect/active state may have changed since
    acceptance.  The detached ORM instance is deliberately not added to the
    session: pricing needs only the frozen snapshot fields.
    """
    return VatTreatment(
        id=quote.vat_treatment_id,
        company_id=quote.company_id,
        code=quote.vat_treatment_code,
        label=quote.vat_treatment_label,
        side=VatTreatmentSide.SALES,
        effect=VatTreatmentEffect(quote.vat_treatment_effect),
        requires_icp=quote.vat_treatment_requires_icp,
        active=True,
    )


async def create_final_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    quote_id: uuid.UUID,
    body: FinalDraftCreate,
    creator_id: uuid.UUID | None,
    exact_credit_provider: ExactAdvanceCreditProvider | None = None,
) -> InvoiceRead:
    await set_rls_company(session, company_id)
    try:
        quote = await _locked_quote(session, company_id, quote_id)
        locked_invoices = await lock_formal_charge_invoices(session, quote)
        if _has_open_advance(locked_invoices):
            raise FinalConflictError(
                "Open Advance DRAFT must be issued or deleted before Final creation."
            )
        advances = _issued_advances(locked_invoices)
        if not advances:
            raise FinalValidationError(
                "FINAL_REQUIRES_ISSUED_ADVANCE", "Final requires at least one issued Advance."
            )
        if _final_exists(locked_invoices):
            raise FinalConflictError("Only one Final DRAFT or issued Final is allowed per Quote.")
        # Creation preserves accepted Quote history exactly.  Do not resolve
        # mutable VAT/product/unit/customer masters or rerun pricing here.
        final = await _clone_quote_snapshot_to_final(
            session, quote=quote, body=body, creator_id=creator_id
        )
        application_total = await _persist_applications(
            session, final, advances, exact_credit_provider=exact_credit_provider
        )
        residual = quantize_to_minor_unit(Decimal(str(final.total_incl_vat)) - application_total)
        if residual < 0:
            raise FinalValidationError(
                "FINAL_NEGATIVE_RESIDUAL", "Quote total cannot be lower than applied Advances."
            )
        _set_residual(final, residual)
        await validate_final_draft(session, quote=quote, final=final)
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id,
            invoice_id=final.id,
            actor_user_id=creator_id,
            event_type=DocumentChainEventType.INVOICE_CREATED,
            metadata={"document_kind": "FINAL"},
        )
        await session.flush()
        result = await _load_invoice_read(session, final)
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise FinalConflictError(
            "Only one Final DRAFT or issued Final is allowed per Quote."
        ) from exc
    except DBAPIError as exc:
        await session.rollback()
        if is_retryable_transaction_conflict(exc):
            raise FinalConflictError("Concurrent Final creation; retry the command.") from exc
        raise
    except Exception:
        await session.rollback()
        raise


async def update_final_draft(
    session: AsyncSession,
    *,
    final: Invoice,
    quote: Quote,
    body: InvoiceWrite,
    company_id: uuid.UUID,
    company_currency: str,
) -> Invoice:
    if (
        body.customer_id != quote.customer_id
        or (body.currency or company_currency) != quote.currency
    ):
        raise FinalValidationError(
            "FINAL_IMMUTABLE_CONTEXT", "Final customer and currency are fixed by its Quote."
        )
    if body.vat_treatment_id != quote.vat_treatment_id:
        raise FinalValidationError(
            "FINAL_IMMUTABLE_TREATMENT", "Final VAT treatment is fixed by its Quote."
        )
    from jai.models.customer import Customer

    customer = (
        await session.execute(select(Customer).where(Customer.id == quote.customer_id))
    ).scalar_one()
    # Keep the accepted Quote's treatment semantics through every legal Final
    # edit.  In particular, an inactive or reconfigured live master must not
    # make rate/price/line edits fail or silently alter immutable applications
    # and the eventual residual credit basis.
    treatment = _quote_treatment_snapshot(quote)
    rates = await _load_vat_rates(session, company_id, body)
    await _validate_line_fks(session, company_id, body)
    from jai.services.invoice import _build_and_persist_invoice

    updated = await _build_and_persist_invoice(
        session,
        company_id=company_id,
        company_currency=company_currency,
        creator_id=final.creator_id,
        body=body,
        customer=customer,
        treatment=treatment,
        vat_rates=rates,
        existing_invoice=final,
    )
    updated.document_kind = InvoiceDocumentKind.FINAL
    updated.quote_id = quote.id
    residual = await validate_final_draft(session, quote=quote, final=updated)
    _set_residual(updated, residual)
    return updated


async def validate_final_issue(
    session: AsyncSession,
    *,
    quote: Quote,
    final: Invoice,
    locked_invoices: Sequence[Invoice],
) -> None:
    """Validate issue using the chain prefix locked by the lifecycle service."""
    if _has_open_advance(locked_invoices):
        raise FinalConflictError("Open Advance DRAFT blocks Final issue.")
    if not any(invoice.id == final.id for invoice in locked_invoices):
        raise FinalConflictError("Final changed while acquiring the formal charge lock prefix.")
    await validate_final_draft(session, quote=quote, final=final)
