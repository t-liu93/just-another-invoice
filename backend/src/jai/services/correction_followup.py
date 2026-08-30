"""M12 guided replacement, compensation and formal cancellation commands."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jai.db import set_rls_company
from jai.models._enums import (
    AdvanceInputMode,
    DiscountType,
    DocumentChainEventType,
    InvoiceCreditStatus,
    InvoiceDocumentKind,
    InvoicePaidStatus,
    InvoiceRelationType,
    InvoiceSettlementStatus,
    InvoiceStatus,
    InvoiceTaxMode,
    QuoteSettlementMode,
)
from jai.models.document import (
    InvoiceCorrection,
    InvoiceCorrectionLine,
    InvoiceRelation,
)
from jai.models.invoice import Invoice, InvoiceLine, InvoiceLineTax, InvoiceTax
from jai.models.quote import Quote
from jai.schemas.invoice import (
    CreditCalculationRequest,
    CreditDraftCreate,
    InvoiceRead,
    ProjectCancellationCreateRequest,
    ProjectCancellationPreview,
    ProjectCancellationRequest,
    ProjectCancellationResult,
    ProjectCancellationSourceRead,
)
from jai.services.advance import (
    AdvanceBucket,
    _remaining_buckets,
    assess_advance_creation,
    is_retryable_transaction_conflict,
)
from jai.services.credit import (
    CreditConflictError,
    CreditValidationError,
    _assert_advance_not_final_frozen,
    _issued_lines_by_basis,
    _persist_credit_draft,
    _remaining_basis,
    calculate_credit,
)
from jai.services.document_chain import append_document_chain_event
from jai.services.invoice import _load_invoice_read

_ZERO = Decimal("0")


class CorrectionFollowupConflictError(ValueError):
    """A retryable/stale lifecycle conflict with a stable API code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CorrectionFollowupValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def _lock_credit_context(
    session: AsyncSession, *, company_id: uuid.UUID, credit_id: uuid.UUID
) -> tuple[Quote | None, list[Invoice], Invoice, Invoice]:
    """Acquire Quote -> charge Invoices -> Credits -> correction/relation rows."""
    probe = (
        await session.execute(
            select(
                Invoice.quote_id,
                InvoiceCorrection.source_invoice_id,
            )
            .join(InvoiceCorrection, InvoiceCorrection.credit_note_id == Invoice.id)
            .where(
                Invoice.id == credit_id,
                Invoice.company_id == company_id,
                Invoice.document_kind == InvoiceDocumentKind.CREDIT_NOTE,
            )
        )
    ).one_or_none()
    if probe is None:
        raise LookupError("Credit Note not found.")
    quote: Quote | None = None
    if probe.quote_id is not None:
        quote = (
            await session.execute(
                select(Quote)
                .where(Quote.id == probe.quote_id, Quote.company_id == company_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if quote is None:
            raise CorrectionFollowupConflictError(
                "FOLLOWUP_CHAIN_CONFLICT", "Credit Note Quote provenance no longer exists."
            )
        charge_ids_stmt = select(Invoice.id).where(
            Invoice.company_id == company_id,
            Invoice.quote_id == quote.id,
            Invoice.document_kind != InvoiceDocumentKind.CREDIT_NOTE,
        )
    else:
        charge_ids_stmt = select(Invoice.id).where(
            Invoice.company_id == company_id,
            Invoice.id == probe.source_invoice_id,
        )
    charge_ids = list((await session.execute(charge_ids_stmt)).scalars())
    charges = list(
        (
            await session.execute(
                select(Invoice)
                .where(Invoice.id.in_(charge_ids or [probe.source_invoice_id]))
                .order_by(Invoice.id)
                .with_for_update()
            )
        ).scalars()
    )
    source = next((item for item in charges if item.id == probe.source_invoice_id), None)
    if source is None:
        raise CorrectionFollowupConflictError(
            "FOLLOWUP_CHAIN_CONFLICT", "Credit source changed while acquiring chain locks."
        )
    if quote is not None:
        credit_filter = Invoice.quote_id == quote.id
    else:
        credit_filter = InvoiceCorrection.source_invoice_id == source.id
    credit_ids = list(
        (
            await session.execute(
                select(Invoice.id)
                .join(InvoiceCorrection, InvoiceCorrection.credit_note_id == Invoice.id)
                .where(Invoice.company_id == company_id, credit_filter)
                .order_by(Invoice.id)
            )
        ).scalars()
    )
    await session.execute(
        select(Invoice)
        .where(Invoice.id.in_(credit_ids or [credit_id]))
        .order_by(Invoice.id)
        .with_for_update()
    )
    await session.execute(
        select(InvoiceCorrection)
        .where(InvoiceCorrection.credit_note_id.in_(credit_ids or [credit_id]))
        .order_by(InvoiceCorrection.id)
        .with_for_update()
    )
    await session.execute(
        select(InvoiceRelation)
        .where(InvoiceRelation.related_credit_note_id.in_(credit_ids or [credit_id]))
        .order_by(InvoiceRelation.id)
    )
    credit = (
        await session.execute(
            select(Invoice)
            .where(Invoice.id == credit_id, Invoice.company_id == company_id)
            .options(
                selectinload(Invoice.lines).selectinload(InvoiceLine.line_taxes),
                selectinload(Invoice.taxes),
                selectinload(Invoice.correction),
            )
        )
    ).scalar_one()
    if InvoiceStatus(credit.status) not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}:
        raise CorrectionFollowupConflictError(
            "CREDIT_NOT_ISSUED", "A follow-up requires an issued Credit Note."
        )
    return quote, charges, source, credit


async def _existing_relation(session: AsyncSession, credit_id: uuid.UUID) -> bool:
    return (
        await session.scalar(
            select(InvoiceRelation.id)
            .where(InvoiceRelation.related_credit_note_id == credit_id)
            .limit(1)
        )
    ) is not None


def _copy_positive_snapshot(
    *,
    credit: Invoice,
    source: Invoice,
    kind: InvoiceDocumentKind,
    creator_id: uuid.UUID | None,
) -> Invoice:
    """Mirror the issued Credit magnitude without consulting live VAT dictionaries."""
    gross = Decimal(str(credit.total_incl_vat))
    taxable = Decimal(str(credit.taxable_amount))
    vat = Decimal(str(credit.vat_total))
    base_gross = Decimal(str(credit.base_total_incl_vat))
    base_taxable = Decimal(str(credit.base_taxable_amount))
    base_vat = Decimal(str(credit.base_vat_total))
    invoice = Invoice(
        company_id=credit.company_id,
        customer_id=credit.customer_id,
        invoice_number=None,
        sequence_number=None,
        customer_sequence_number=None,
        reference_number=None,
        invoice_date=credit.invoice_date,
        due_date=None,
        supply_or_advance_date=credit.supply_or_advance_date,
        document_kind=kind,
        quote_id=credit.quote_id,
        status=InvoiceStatus.DRAFT,
        paid_status=InvoicePaidStatus.UNPAID,
        currency=credit.currency,
        exchange_rate=credit.exchange_rate,
        tax_mode=credit.tax_mode,
        amounts_include_vat=credit.amounts_include_vat,
        vat_treatment_id=credit.vat_treatment_id,
        document_vat_rate_id=credit.document_vat_rate_id,
        vat_treatment_code=credit.vat_treatment_code,
        vat_treatment_label=credit.vat_treatment_label,
        vat_treatment_effect=credit.vat_treatment_effect,
        vat_treatment_requires_icp=credit.vat_treatment_requires_icp,
        discount_type=DiscountType.NONE,
        discount_value=_ZERO,
        document_discount_amount=_ZERO,
        subtotal_excl_vat=taxable,
        line_discount_total=_ZERO,
        taxable_amount=taxable,
        vat_total=vat,
        total_incl_vat=gross,
        due_amount=gross,
        payable_before_payments=gross,
        incoming_payment_total=_ZERO,
        credited_total=_ZERO,
        refunded_total=_ZERO,
        refund_due_amount=_ZERO,
        settlement_status=InvoiceSettlementStatus.OPEN,
        credit_status=InvoiceCreditStatus.NOT_CREDITED,
        base_subtotal_excl_vat=base_taxable,
        base_line_discount_total=_ZERO,
        base_taxable_amount=base_taxable,
        base_vat_total=base_vat,
        base_total_incl_vat=base_gross,
        base_due_amount=base_gross,
        base_payable_before_payments=base_gross,
        base_incoming_payment_total=_ZERO,
        base_credited_total=_ZERO,
        base_refunded_total=_ZERO,
        base_refund_due_amount=_ZERO,
        notes=source.notes,
        warranty_text=source.warranty_text,
        terms_text=source.terms_text,
        bank_text=source.bank_text,
        payment_terms_text=source.payment_terms_text,
        creator_id=creator_id,
    )
    if kind == InvoiceDocumentKind.ADVANCE:
        invoice.advance_input_mode = AdvanceInputMode.GROSS_AMOUNT
        invoice.advance_gross_amount = gross
    invoice.lines = []
    invoice.taxes = []
    for source_line in credit.lines:
        line = InvoiceLine(
            sort_order=source_line.sort_order,
            name=source_line.name,
            description=source_line.description,
            quantity=source_line.quantity,
            unit_name=source_line.unit_name,
            unit_price=source_line.unit_price,
            discount_type=DiscountType.NONE,
            discount_value=_ZERO,
            vat_rate_id=source_line.vat_rate_id,
            vat_rate_label=source_line.vat_rate_label,
            vat_rate_percent=source_line.vat_rate_percent,
            subtotal_excl_vat=source_line.subtotal_excl_vat,
            subtotal_incl_vat=source_line.subtotal_incl_vat,
            line_discount_amount=_ZERO,
            document_discount_share=_ZERO,
            taxable_amount=source_line.taxable_amount,
            vat_total=source_line.vat_total,
            total_incl_vat=source_line.total_incl_vat,
        )
        line.line_taxes = [
            InvoiceLineTax(
                vat_rate_id=tax.vat_rate_id,
                vat_rate_label=tax.vat_rate_label,
                vat_rate_percent=tax.vat_rate_percent,
                effective_vat_percent=tax.effective_vat_percent,
                taxable_amount=tax.taxable_amount,
                tax_amount=tax.tax_amount,
            )
            for tax in source_line.line_taxes
        ]
        invoice.lines.append(line)
    invoice.taxes = [
        InvoiceTax(
            vat_rate_id=tax.vat_rate_id,
            vat_rate_label=tax.vat_rate_label,
            vat_rate_percent=tax.vat_rate_percent,
            effective_vat_percent=tax.effective_vat_percent,
            taxable_amount=tax.taxable_amount,
            tax_amount=tax.tax_amount,
        )
        for tax in credit.taxes
    ]
    return invoice


def _bucket_amounts(invoice: Invoice) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    result: dict[uuid.UUID, tuple[Decimal, Decimal]] = defaultdict(lambda: (_ZERO, _ZERO))
    if InvoiceTaxMode(invoice.tax_mode) == InvoiceTaxMode.DOCUMENT:
        for tax in invoice.taxes:
            net, vat = result[tax.vat_rate_id]
            result[tax.vat_rate_id] = (
                net + Decimal(str(tax.taxable_amount)),
                vat + Decimal(str(tax.tax_amount)),
            )
        return result
    for line in invoice.lines:
        if line.vat_rate_id is None:
            continue
        net, vat = result[line.vat_rate_id]
        result[line.vat_rate_id] = (
            net + Decimal(str(line.taxable_amount)),
            vat + Decimal(str(line.vat_total)),
        )
    return result


async def _assert_advance_snapshot_capacity(
    session: AsyncSession, quote: Quote, invoice: Invoice
) -> None:
    available: dict[uuid.UUID, AdvanceBucket] = {
        bucket.vat_rate_id: bucket for bucket in await _remaining_buckets(session, quote)
    }
    for rate_id, (net, vat) in _bucket_amounts(invoice).items():
        bucket = available.get(rate_id)
        if bucket is None or net > bucket.taxable_amount or vat > bucket.vat_amount:
            raise CorrectionFollowupValidationError(
                "ADVANCE_REPLACEMENT_CAPACITY",
                "The credited VAT basis no longer fits the available Advance capacity.",
            )


async def _create_followup(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    credit_id: uuid.UUID,
    relation_type: InvoiceRelationType,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    await set_rls_company(session, company_id)
    try:
        quote, charges, source, credit = await _lock_credit_context(
            session, company_id=company_id, credit_id=credit_id
        )
        if await _existing_relation(session, credit.id):
            raise CorrectionFollowupConflictError(
                "FOLLOWUP_ALREADY_EXISTS", "This Credit Note already has a positive follow-up."
            )
        source_kind = InvoiceDocumentKind(source.document_kind)
        has_final = any(
            InvoiceDocumentKind(item.document_kind) == InvoiceDocumentKind.FINAL for item in charges
        )
        if relation_type == InvoiceRelationType.REPLACEMENT_OF:
            if source_kind == InvoiceDocumentKind.STANDARD:
                if (
                    quote is not None
                    and QuoteSettlementMode(quote.settlement_mode)
                    != QuoteSettlementMode.DIRECT_INVOICE
                ):
                    raise CorrectionFollowupValidationError(
                        "REPLACEMENT_NOT_ELIGIBLE",
                        "Only a direct Standard or safely pre-Final Advance can be replaced.",
                    )
                kind = InvoiceDocumentKind.STANDARD
            elif source_kind == InvoiceDocumentKind.ADVANCE and quote is not None:
                if has_final:
                    raise CorrectionFollowupConflictError(
                        "FINAL_DRAFT_FREEZE",
                        "A Final DRAFT or issued Final freezes Advance replacement.",
                    )
                kind = InvoiceDocumentKind.ADVANCE
            else:
                raise CorrectionFollowupValidationError(
                    "REPLACEMENT_NOT_ELIGIBLE",
                    "Only a direct Standard or safely pre-Final Advance can be replaced.",
                )
        else:
            kind = (
                InvoiceDocumentKind.ADVANCE
                if source_kind == InvoiceDocumentKind.ADVANCE
                and quote is not None
                and not has_final
                else InvoiceDocumentKind.STANDARD
            )
        if kind == InvoiceDocumentKind.ADVANCE:
            assert quote is not None
            availability = await assess_advance_creation(session, quote, lock_open_draft=True)
            if availability.has_open_draft:
                raise CorrectionFollowupConflictError(
                    "ADVANCE_DRAFT_EXISTS", "Only one open Advance DRAFT is allowed per Quote."
                )
            if has_final:
                raise CorrectionFollowupConflictError(
                    "FINAL_DRAFT_FREEZE", "A Final DRAFT or issued Final freezes Advance follow-up."
                )
        invoice = _copy_positive_snapshot(
            credit=credit, source=source, kind=kind, creator_id=creator_id
        )
        session.add(invoice)
        await session.flush()
        if kind == InvoiceDocumentKind.ADVANCE:
            assert quote is not None
            await _assert_advance_snapshot_capacity(session, quote, invoice)
        relation = InvoiceRelation(
            company_id=company_id,
            invoice_id=invoice.id,
            related_credit_note_id=credit.id,
            relation_type=relation_type,
        )
        session.add(relation)
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=invoice.quote_id,
            invoice_id=invoice.id,
            actor_user_id=creator_id,
            event_type=DocumentChainEventType.INVOICE_CREATED,
            metadata={"document_kind": kind.value},
        )
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=invoice.quote_id,
            invoice_id=invoice.id,
            actor_user_id=creator_id,
            event_type=(
                DocumentChainEventType.REPLACEMENT_CREATED
                if relation_type == InvoiceRelationType.REPLACEMENT_OF
                else DocumentChainEventType.COMPENSATING_INVOICE_CREATED
            ),
            metadata={"credit_note_id": credit.id},
        )
        await session.flush()
        result = await _load_invoice_read(session, invoice)
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise CorrectionFollowupConflictError(
            "FOLLOWUP_CONFLICT", "A concurrent follow-up already exists."
        ) from exc
    except DBAPIError as exc:
        await session.rollback()
        if is_retryable_transaction_conflict(exc):
            raise CorrectionFollowupConflictError(
                "FOLLOWUP_CONFLICT", "Concurrent correction follow-up; retry the command."
            ) from exc
        raise
    except Exception:
        await session.rollback()
        raise


async def create_replacement_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    credit_id: uuid.UUID,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    return await _create_followup(
        session,
        company_id=company_id,
        credit_id=credit_id,
        relation_type=InvoiceRelationType.REPLACEMENT_OF,
        creator_id=creator_id,
    )


async def create_compensating_invoice_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    credit_id: uuid.UUID,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    return await _create_followup(
        session,
        company_id=company_id,
        credit_id=credit_id,
        relation_type=InvoiceRelationType.COMPENSATES_CREDIT,
        creator_id=creator_id,
    )


async def _lock_formal_chain(
    session: AsyncSession, *, company_id: uuid.UUID, quote_id: uuid.UUID
) -> tuple[Quote, list[Invoice]]:
    quote = (
        await session.execute(
            select(Quote)
            .where(Quote.id == quote_id, Quote.company_id == company_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if quote is None:
        raise LookupError("Quote not found.")
    if QuoteSettlementMode(quote.settlement_mode) != QuoteSettlementMode.FORMAL_ADVANCE:
        raise CorrectionFollowupValidationError(
            "FORMAL_CHAIN_REQUIRED", "Project cancellation requires a Formal Advance Quote."
        )
    charges = list(
        (
            await session.execute(
                select(Invoice)
                .where(
                    Invoice.company_id == company_id,
                    Invoice.quote_id == quote.id,
                    Invoice.document_kind != InvoiceDocumentKind.CREDIT_NOTE,
                )
                .options(selectinload(Invoice.credit_basis_lines))
                .order_by(Invoice.id)
                .with_for_update()
            )
        ).scalars()
    )
    credit_ids = list(
        (
            await session.execute(
                select(Invoice.id)
                .where(
                    Invoice.company_id == company_id,
                    Invoice.quote_id == quote.id,
                    Invoice.document_kind == InvoiceDocumentKind.CREDIT_NOTE,
                )
                .order_by(Invoice.id)
                .with_for_update()
            )
        ).scalars()
    )
    await session.execute(
        select(InvoiceCorrection)
        .where(InvoiceCorrection.credit_note_id.in_(credit_ids or [uuid.uuid4()]))
        .order_by(InvoiceCorrection.id)
        .with_for_update()
    )
    await session.execute(
        select(InvoiceCorrectionLine)
        .join(InvoiceCorrection)
        .where(InvoiceCorrection.credit_note_id.in_(credit_ids or [uuid.uuid4()]))
        .order_by(InvoiceCorrectionLine.id)
        .with_for_update()
    )
    return quote, charges


async def _formal_cancellation_sources(
    session: AsyncSession, quote: Quote, charges: list[Invoice]
) -> list[Invoice]:
    supplemental_ids = set(
        (
            await session.execute(
                select(InvoiceRelation.invoice_id)
                .where(
                    InvoiceRelation.company_id == quote.company_id,
                    InvoiceRelation.relation_type == InvoiceRelationType.COMPENSATES_CREDIT,
                )
                .order_by(InvoiceRelation.invoice_id)
            )
        ).scalars()
    )
    result: list[Invoice] = []
    for invoice in charges:
        kind = InvoiceDocumentKind(invoice.document_kind)
        if InvoiceStatus(invoice.status) not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}:
            continue
        if kind in {InvoiceDocumentKind.ADVANCE, InvoiceDocumentKind.FINAL} or (
            kind == InvoiceDocumentKind.STANDARD and invoice.id in supplemental_ids
        ):
            await _assert_advance_not_final_frozen(session, invoice)
            result.append(invoice)
    return sorted(result, key=lambda item: (item.invoice_date, item.invoice_number or "", item.id))


async def _build_cancellation_preview(
    session: AsyncSession,
    *,
    quote: Quote,
    charges: list[Invoice],
    request: ProjectCancellationRequest,
) -> ProjectCancellationPreview:
    invoice_date = request.invoice_date or date.today()
    sources: list[ProjectCancellationSourceRead] = []
    token_sources: list[dict[str, object]] = []
    for source in await _formal_cancellation_sources(session, quote, charges):
        if invoice_date < source.invoice_date:
            raise CorrectionFollowupValidationError(
                "CANCELLATION_DATE_BEFORE_SOURCE",
                "Cancellation Credit date cannot precede a remaining formal source date.",
            )
        issued = await _issued_lines_by_basis(session, quote.company_id, source.id)
        token_lines: list[dict[str, str]] = []
        totals = [_ZERO] * 7
        for basis in sorted(source.credit_basis_lines, key=lambda row: (row.sort_order, row.id)):
            remaining = _remaining_basis(basis, issued.get(basis.id, []))
            for index, value in enumerate(remaining):
                totals[index] += value
            token_lines.append(
                {
                    "basis_id": str(basis.id),
                    "quantity": str(remaining[0]),
                    "net": str(remaining[1]),
                    "vat": str(remaining[2]),
                    "gross": str(remaining[3]),
                    "base_net": str(remaining[4]),
                    "base_vat": str(remaining[5]),
                    "base_gross": str(remaining[6]),
                }
            )
        if totals[3] <= _ZERO:
            continue
        if source.invoice_number is None:
            raise CorrectionFollowupConflictError(
                "FORMAL_CHAIN_CONFLICT", "An issued formal source has no legal number."
            )
        sources.append(
            ProjectCancellationSourceRead(
                source_invoice_id=source.id,
                source_invoice_number=source.invoice_number,
                document_kind=InvoiceDocumentKind(source.document_kind),
                remaining_net_amount=totals[1],
                remaining_vat_amount=totals[2],
                remaining_gross_amount=totals[3],
                remaining_base_net_amount=totals[4],
                remaining_base_vat_amount=totals[5],
                remaining_base_gross_amount=totals[6],
            )
        )
        token_sources.append(
            {
                "source_id": str(source.id),
                "number": source.invoice_number,
                "kind": InvoiceDocumentKind(source.document_kind).value,
                "status": InvoiceStatus(source.status).value,
                "lines": token_lines,
            }
        )
    if not sources:
        raise CorrectionFollowupValidationError(
            "NO_REMAINING_FORMAL_CHARGE",
            "The formal project has no remaining charge basis to cancel.",
        )
    token_payload = {
        "quote_id": str(quote.id),
        "invoice_date": invoice_date.isoformat(),
        "supply_or_advance_date": (
            request.supply_or_advance_date.isoformat()
            if request.supply_or_advance_date is not None
            else None
        ),
        "reference_number": request.reference_number,
        "sources": token_sources,
    }
    token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ProjectCancellationPreview(
        quote_id=quote.id,
        preview_token=token,
        invoice_date=invoice_date,
        sources=sources,
        net_amount=sum((row.remaining_net_amount for row in sources), _ZERO),
        vat_amount=sum((row.remaining_vat_amount for row in sources), _ZERO),
        gross_amount=sum((row.remaining_gross_amount for row in sources), _ZERO),
        base_net_amount=sum((row.remaining_base_net_amount for row in sources), _ZERO),
        base_vat_amount=sum((row.remaining_base_vat_amount for row in sources), _ZERO),
        base_gross_amount=sum((row.remaining_base_gross_amount for row in sources), _ZERO),
    )


async def preview_project_cancellation(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    quote_id: uuid.UUID,
    request: ProjectCancellationRequest,
) -> ProjectCancellationPreview:
    await set_rls_company(session, company_id)
    quote, charges = await _lock_formal_chain(session, company_id=company_id, quote_id=quote_id)
    preview = await _build_cancellation_preview(
        session, quote=quote, charges=charges, request=request
    )
    # Preview is read-only.  End the lock-bearing transaction without writes.
    await session.rollback()
    return preview


async def create_project_cancellation_drafts(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    quote_id: uuid.UUID,
    request: ProjectCancellationCreateRequest,
    creator_id: uuid.UUID | None,
) -> ProjectCancellationResult:
    await set_rls_company(session, company_id)
    try:
        quote, charges = await _lock_formal_chain(session, company_id=company_id, quote_id=quote_id)
        preview_request = ProjectCancellationRequest(
            invoice_date=request.invoice_date,
            supply_or_advance_date=request.supply_or_advance_date,
            reference_number=request.reference_number,
        )
        preview = await _build_cancellation_preview(
            session, quote=quote, charges=charges, request=preview_request
        )
        if preview.preview_token != request.preview_token:
            raise CorrectionFollowupConflictError(
                "CANCELLATION_PREVIEW_STALE",
                "The formal project changed after cancellation preview.",
            )
        source_by_id = {source.id: source for source in charges}
        credit_reads: list[InvoiceRead] = []
        for row in preview.sources:
            source = source_by_id[row.source_invoice_id]
            calculation = await calculate_credit(
                session,
                company_id=company_id,
                source_id=source.id,
                request=CreditCalculationRequest(full_remaining=True),
            )
            credit = await _persist_credit_draft(
                session,
                source=source,
                calculation=calculation,
                body=CreditDraftCreate(
                    full_remaining=True,
                    invoice_date=preview.invoice_date,
                    supply_or_advance_date=request.supply_or_advance_date,
                    reference_number=request.reference_number,
                ),
                creator_id=creator_id,
            )
            await append_document_chain_event(
                session,
                company_id=company_id,
                quote_id=quote.id,
                invoice_id=credit.id,
                actor_user_id=creator_id,
                event_type=DocumentChainEventType.INVOICE_CREATED,
                metadata={"document_kind": InvoiceDocumentKind.CREDIT_NOTE.value},
            )
            await append_document_chain_event(
                session,
                company_id=company_id,
                quote_id=quote.id,
                invoice_id=credit.id,
                actor_user_id=creator_id,
                event_type=DocumentChainEventType.PROJECT_CANCELLATION_CREDIT_CREATED,
                metadata={"source_invoice_id": source.id},
            )
            credit_reads.append(await _load_invoice_read(session, credit))
        await session.commit()
        return ProjectCancellationResult(
            quote_id=quote.id,
            preview_token=preview.preview_token,
            credit_notes=credit_reads,
        )
    except (CreditConflictError, CreditValidationError):
        await session.rollback()
        raise
    except DBAPIError as exc:
        await session.rollback()
        if is_retryable_transaction_conflict(exc):
            raise CorrectionFollowupConflictError(
                "CANCELLATION_CONFLICT", "Concurrent project cancellation; retry."
            ) from exc
        raise
    except Exception:
        await session.rollback()
        raise
