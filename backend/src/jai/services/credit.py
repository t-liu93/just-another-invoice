"""Source-bound Credit Note calculation, drafts and issue-time revalidation."""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jai.db import set_rls_company
from jai.models._enums import (
    CreditLineInputMode,
    DocumentChainEventType,
    InvoiceCreditStatus,
    InvoiceDocumentKind,
    InvoicePaidStatus,
    InvoiceSettlementStatus,
    InvoiceStatus,
    SettingLevel,
)
from jai.models.document import (
    InvoiceCorrection,
    InvoiceCorrectionLine,
    InvoiceCreditBasisLine,
    InvoicePartySnapshot,
)
from jai.models.invoice import Invoice, InvoiceLine, InvoiceLineTax, InvoiceTax
from jai.models.quote import Quote
from jai.schemas.invoice import (
    CreditCalculationLineInput,
    CreditCalculationLineRead,
    CreditCalculationRead,
    CreditCalculationRequest,
    CreditDraftCreate,
    CreditDraftUpdate,
    InvoiceRead,
)
from jai.schemas.setting import SETTING_KEY_CREDIT_NUMBERING, CreditNumberingConfig
from jai.services.document_actions import credit_note_eligibility
from jai.services.document_chain import append_document_chain_event
from jai.services.invoice import _load_invoice_read
from jai.services.numbering import allocate_credit_number
from jai.services.settings import get_setting

_ZERO = Decimal("0")
_CENT = Decimal("0.01")
_QUANTITY = Decimal("0.001")


class CreditConflictError(ValueError):
    """A retryable/lifecycle conflict with a stable public error code."""

    def __init__(self, message: str, *, code: str = "CREDIT_CONFLICT") -> None:
        super().__init__(message)
        self.code = code


class CreditValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _allocate_part(total: Decimal, numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= _ZERO:
        return _ZERO
    return _money(total * numerator / denominator)


def _allocate_quantity(total: Decimal, numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= _ZERO:
        return _ZERO
    return (total * numerator / denominator).quantize(_QUANTITY, rounding=ROUND_HALF_UP)


async def _load_source(
    session: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, *, lock: bool
) -> Invoice:
    stmt = (
        select(Invoice)
        .where(Invoice.id == source_id, Invoice.company_id == company_id)
        .options(selectinload(Invoice.credit_basis_lines), selectinload(Invoice.party_snapshot))
    )
    if lock:
        stmt = stmt.with_for_update()
    source = (await session.execute(stmt)).scalar_one_or_none()
    if source is None:
        raise LookupError("Source invoice not found.")
    if InvoiceDocumentKind(source.document_kind) == InvoiceDocumentKind.CREDIT_NOTE:
        raise CreditValidationError("CREDIT_OF_CREDIT", "A Credit Note cannot be credited.")
    if InvoiceStatus(source.status) not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}:
        raise CreditValidationError("CREDIT_SOURCE_NOT_ISSUED", "Source invoice must be issued.")
    if not source.credit_basis_lines:
        raise CreditValidationError(
            "CREDIT_SOURCE_NO_BASIS", "Source invoice has no immutable credit basis."
        )
    return source


async def _lock_credit_source_context(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
) -> Invoice:
    """Acquire the Credit mutation prefix: Quote, charge sources, target source.

    An unlocked probe only discovers the Quote prefix.  Authoritative source
    data is always reread after the global Quote -> Invoice lock prefix.
    """
    quote_id = await session.scalar(
        select(Invoice.quote_id).where(
            Invoice.id == source_id, Invoice.company_id == company_id
        )
    )
    if quote_id is not None:
        quote = (
            await session.execute(
                select(Quote)
                .where(Quote.id == quote_id, Quote.company_id == company_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if quote is None:
            raise CreditConflictError("Source Quote no longer exists.")
        # A formal Credit may race Final issue/Advance Credit.  Lock all formal
        # charge sources in canonical order before the Credit suffix.
        await session.execute(
            select(Invoice)
            .where(
                Invoice.quote_id == quote.id,
                Invoice.document_kind.in_(
                    [InvoiceDocumentKind.ADVANCE, InvoiceDocumentKind.FINAL]
                ),
            )
            .order_by(Invoice.id)
            .with_for_update()
        )
    return await _load_source(session, company_id, source_id, lock=True)


async def _assert_advance_not_final_frozen(
    session: AsyncSession, source: Invoice, *, allow_stale_exhaustion: bool = False
) -> None:
    final_draft_exists = False
    if source.quote_id is not None:
        final_draft_exists = (
            await session.scalar(
                select(Invoice.id)
                .where(
                    Invoice.quote_id == source.quote_id,
                    Invoice.document_kind == InvoiceDocumentKind.FINAL,
                    Invoice.status == InvoiceStatus.DRAFT,
                )
                .limit(1)
            )
    ) is not None
    eligibility = credit_note_eligibility(source, final_draft_exists=final_draft_exists)
    # An issue command must replay a pre-existing DRAFT intent under locks.
    # If a competing Credit consumed the last basis in the meantime, let the
    # replay turn that into CREDIT_STALE_BASIS rather than replacing it with
    # the calculate/create projection reason.
    if allow_stale_exhaustion and eligibility.reason_code == "CREDIT_NO_REMAINING_BASIS":
        return
    if not eligibility.available:
        raise CreditConflictError(
            "This source cannot create a Credit Note in the current chain state.",
            code=eligibility.reason_code or "CREDIT_ACTION_UNAVAILABLE",
        )


async def _issued_lines_by_basis(
    session: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID
) -> dict[uuid.UUID, list[InvoiceCorrectionLine]]:
    rows = (
        await session.execute(
            select(InvoiceCorrectionLine)
            .join(InvoiceCorrection)
            .join(Invoice, Invoice.id == InvoiceCorrection.credit_note_id)
            .where(
                InvoiceCorrection.company_id == company_id,
                InvoiceCorrection.source_invoice_id == source_id,
                Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.COMPLETED]),
            )
            .order_by(
                Invoice.invoice_date, Invoice.invoice_number, Invoice.id, InvoiceCorrectionLine.id
            )
        )
    ).scalars()
    grouped: dict[uuid.UUID, list[InvoiceCorrectionLine]] = defaultdict(list)
    for row in rows:
        grouped[row.source_basis_line_id].append(row)
    return grouped


def _remaining_basis(
    basis: InvoiceCreditBasisLine, issued: list[InvoiceCorrectionLine]
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    quantity = Decimal(str(basis.quantity)) - sum((Decimal(str(x.quantity)) for x in issued), _ZERO)
    net = Decimal(str(basis.net_amount)) - sum((Decimal(str(x.net_amount)) for x in issued), _ZERO)
    vat = Decimal(str(basis.vat_amount)) - sum((Decimal(str(x.vat_amount)) for x in issued), _ZERO)
    gross = Decimal(str(basis.gross_amount)) - sum(
        (Decimal(str(x.gross_amount)) for x in issued), _ZERO
    )
    base_net = Decimal(str(basis.base_net_amount)) - sum(
        (Decimal(str(x.base_net_amount)) for x in issued), _ZERO
    )
    base_vat = Decimal(str(basis.base_vat_amount)) - sum(
        (Decimal(str(x.base_vat_amount)) for x in issued), _ZERO
    )
    base_gross = Decimal(str(basis.base_gross_amount)) - sum(
        (Decimal(str(x.base_gross_amount)) for x in issued), _ZERO
    )
    if min(quantity, net, vat, gross, base_net, base_vat, base_gross) < _ZERO:
        raise CreditConflictError(
            "Issued Credits exceed immutable source basis.", code="CREDIT_BASIS_CONFLICT"
        )
    return quantity, net, vat, gross, base_net, base_vat, base_gross


def _selection(
    basis: InvoiceCreditBasisLine,
    remaining: tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal],
    intent: CreditCalculationLineInput | None,
) -> tuple[
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    CreditLineInputMode,
    Decimal | None,
    Decimal | None,
]:
    rem_q, rem_net, rem_vat, rem_gross, rem_base_net, rem_base_vat, rem_base_gross = remaining

    def base_allocation(
        allocated_net: Decimal, allocated_vat: Decimal, numerator: Decimal, denominator: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Allocate frozen base amounts without inventing a second rounding path.

        M12 chains are single-currency, so a same-currency frozen basis must
        retain field-for-field parity.  The alternate branch is intentionally
        derived only from the remaining frozen base snapshot: it preserves a
        legacy/non-parity basis deterministically and lets a later full
        remainder close exactly to that snapshot.
        """
        if (rem_base_net, rem_base_vat, rem_base_gross) == (rem_net, rem_vat, rem_gross):
            return allocated_net, allocated_vat, allocated_net + allocated_vat
        base_net = _allocate_part(rem_base_net, numerator, denominator)
        base_vat = _allocate_part(rem_base_vat, numerator, denominator)
        return base_net, base_vat, base_net + base_vat
    if intent is None:
        return (*remaining, CreditLineInputMode.GROSS_AMOUNT, None, rem_gross)
    if intent.input_mode == CreditLineInputMode.QUANTITY:
        assert intent.quantity is not None
        if intent.quantity > rem_q:
            raise CreditValidationError(
                "CREDIT_QUANTITY_EXCEEDS_REMAINING", "Quantity exceeds remaining source basis."
            )
        q = intent.quantity
        # Allocate every component against the *remaining* snapshot.  Gross is
        # deliberately made the sum of independently rounded net/VAT values.
        net = _allocate_part(rem_net, q, rem_q)
        vat = _allocate_part(rem_vat, q, rem_q)
        base_net, base_vat, base_gross = base_allocation(net, vat, q, rem_q)
        return (
            q,
            net,
            vat,
            net + vat,
            base_net,
            base_vat,
            base_gross,
            intent.input_mode,
            q,
            None,
        )
    assert intent.gross_amount is not None
    if intent.gross_amount > rem_gross:
        raise CreditValidationError(
            "CREDIT_GROSS_EXCEEDS_REMAINING", "Gross amount exceeds remaining source basis."
        )
    gross = _money(intent.gross_amount)
    if gross > rem_gross:
        raise CreditValidationError(
            "CREDIT_GROSS_EXCEEDS_REMAINING", "Gross amount exceeds remaining source basis."
        )
    net = _allocate_part(rem_net, gross, rem_gross)
    vat = gross - net  # exact minor-unit closure, never recompute tax from live rates
    base_net, base_vat, base_gross = base_allocation(net, vat, gross, rem_gross)
    quantity = _allocate_quantity(rem_q, gross, rem_gross)
    return (
        quantity,
        net,
        vat,
        gross,
        base_net,
        base_vat,
        base_gross,
        intent.input_mode,
        None,
        gross,
    )


async def calculate_credit(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
    request: CreditCalculationRequest,
    allow_stale_exhaustion: bool = False,
) -> CreditCalculationRead:
    await set_rls_company(session, company_id)
    source = await _load_source(session, company_id, source_id, lock=False)
    await _assert_advance_not_final_frozen(
        session, source, allow_stale_exhaustion=allow_stale_exhaustion
    )
    issued = await _issued_lines_by_basis(session, company_id, source.id)
    intents = {line.source_basis_line_id: line for line in request.lines}
    known = {line.id for line in source.credit_basis_lines}
    if not set(intents).issubset(known):
        raise CreditValidationError(
            "CREDIT_BASIS_NOT_FOUND", "A selected source basis line is not part of this source."
        )
    rows: list[CreditCalculationLineRead] = []
    for basis in sorted(source.credit_basis_lines, key=lambda x: (x.sort_order, x.id)):
        remaining = _remaining_basis(basis, issued.get(basis.id, []))
        if request.full_remaining:
            if remaining[3] <= _ZERO:
                continue
            selected = _selection(basis, remaining, None)
        elif basis.id in intents:
            selected = _selection(basis, remaining, intents[basis.id])
        else:
            continue
        q, net, vat, gross, base_net, base_vat, base_gross, _, _, _ = selected
        if gross <= _ZERO:
            continue
        rows.append(
            CreditCalculationLineRead(
                source_basis_line_id=basis.id,
                source_invoice_line_id=basis.invoice_line_id,
                name=basis.name,
                description=basis.description,
                quantity=q,
                unit_name=basis.unit_name,
                vat_rate_id=basis.vat_rate_id,
                vat_rate_label=basis.vat_rate_label,
                vat_rate_percent=basis.vat_rate_percent,
                net_amount=net,
                vat_amount=vat,
                gross_amount=gross,
                base_net_amount=base_net,
                base_vat_amount=base_vat,
                base_gross_amount=base_gross,
            )
        )
    if not rows:
        raise CreditValidationError(
            "CREDIT_NO_REMAINING_BASIS", "No remaining source basis is available."
        )
    return CreditCalculationRead(
        source_invoice_id=source.id,
        remaining_gross_amount=sum(
            (_remaining_basis(x, issued.get(x.id, []))[3] for x in source.credit_basis_lines), _ZERO
        ),
        net_amount=sum((x.net_amount for x in rows), _ZERO),
        vat_amount=sum((x.vat_amount for x in rows), _ZERO),
        gross_amount=sum((x.gross_amount for x in rows), _ZERO),
        base_net_amount=sum((x.base_net_amount for x in rows), _ZERO),
        base_vat_amount=sum((x.base_vat_amount for x in rows), _ZERO),
        base_gross_amount=sum((x.base_gross_amount for x in rows), _ZERO),
        lines=rows,
    )


async def _persist_credit_draft(
    session: AsyncSession,
    *,
    source: Invoice,
    calculation: CreditCalculationRead,
    body: CreditDraftCreate | CreditDraftUpdate,
    creator_id: uuid.UUID | None,
    existing: Invoice | None = None,
) -> Invoice:
    if body.invoice_date < source.invoice_date:
        raise CreditValidationError(
            "CREDIT_DATE_BEFORE_SOURCE", "Credit date cannot precede source invoice date."
        )
    if body.due_date is not None and body.due_date < body.invoice_date:
        raise CreditValidationError(
            "CREDIT_INVALID_DUE_DATE", "Due date cannot precede Credit date."
        )
    if existing is None:
        credit = Invoice(
            company_id=source.company_id,
            customer_id=source.customer_id,
            quote_id=source.quote_id,
            document_kind=InvoiceDocumentKind.CREDIT_NOTE,
            status=InvoiceStatus.DRAFT,
            paid_status=InvoicePaidStatus.NOT_APPLICABLE,
            currency=source.currency,
            exchange_rate=source.exchange_rate,
            tax_mode=source.tax_mode,
            amounts_include_vat=source.amounts_include_vat,
            vat_treatment_id=source.vat_treatment_id,
            document_vat_rate_id=source.document_vat_rate_id,
            vat_treatment_code=source.vat_treatment_code,
            vat_treatment_label=source.vat_treatment_label,
            vat_treatment_effect=source.vat_treatment_effect,
            vat_treatment_requires_icp=source.vat_treatment_requires_icp,
            discount_type=source.discount_type,
            discount_value=source.discount_value,
            document_discount_amount=_ZERO,
            creator_id=creator_id,
        )
        session.add(credit)
        credit.lines = []
        credit.taxes = []
    else:
        credit = existing
        # Credit DRAFT children are owned by the aggregate.  Replace them
        # through ORM delete-orphan so InvoiceLineTax follows its FK/ORM
        # cascade; never issue hand-written child DELETEs.
        credit.lines.clear()
        credit.taxes.clear()
        correction = (
            await session.execute(
                select(InvoiceCorrection)
                .where(InvoiceCorrection.credit_note_id == credit.id)
                .options(selectinload(InvoiceCorrection.lines))
            )
        ).scalar_one()
        # The normalized child collection owns its lifecycle.  Replacing a
        # DRAFT selection uses ORM delete-orphan instead of hand-written child
        # deletes, preserving the project cascade invariant.
        correction.lines.clear()
    credit.reference_number = body.reference_number
    credit.invoice_date = body.invoice_date
    credit.due_date = body.due_date
    credit.supply_or_advance_date = body.supply_or_advance_date
    credit.subtotal_excl_vat = calculation.net_amount
    credit.line_discount_total = _ZERO
    credit.taxable_amount = calculation.net_amount
    credit.vat_total = calculation.vat_amount
    credit.total_incl_vat = calculation.gross_amount
    credit.due_amount = _ZERO
    credit.payable_before_payments = _ZERO
    credit.incoming_payment_total = _ZERO
    credit.credited_total = _ZERO
    credit.refunded_total = _ZERO
    credit.refund_due_amount = _ZERO
    credit.settlement_status = InvoiceSettlementStatus.SETTLED
    credit.credit_status = InvoiceCreditStatus.NOT_CREDITED
    credit.base_subtotal_excl_vat = calculation.base_net_amount
    credit.base_line_discount_total = _ZERO
    credit.base_taxable_amount = calculation.base_net_amount
    credit.base_vat_total = calculation.base_vat_amount
    credit.base_total_incl_vat = calculation.base_gross_amount
    credit.base_due_amount = _ZERO
    credit.base_payable_before_payments = _ZERO
    credit.base_incoming_payment_total = _ZERO
    credit.base_credited_total = _ZERO
    credit.base_refunded_total = _ZERO
    credit.base_refund_due_amount = _ZERO
    await session.flush()
    correction = (
        InvoiceCorrection(
            company_id=source.company_id, credit_note_id=credit.id, source_invoice_id=source.id
        )
        if existing is None
        else correction
    )
    if existing is None:
        session.add(correction)
        correction.lines = []
        await session.flush()
    correction.full_remaining = body.full_remaining
    correction.intent_provenance = "NATIVE"
    basis_map = {x.id: x for x in source.credit_basis_lines}
    input_map = {line.source_basis_line_id: line for line in body.lines}
    line_tax_rows: list[tuple[InvoiceLine, CreditCalculationLineRead, InvoiceCreditBasisLine]] = []
    for order, row in enumerate(calculation.lines):
        basis = basis_map[row.source_basis_line_id]
        line = InvoiceLine(
            invoice_id=credit.id,
            sort_order=order,
            name=row.name,
            description=row.description,
            quantity=row.quantity,
            unit_name=row.unit_name,
            unit_price=(row.net_amount / row.quantity if row.quantity else _ZERO),
            discount_type=source.discount_type,
            discount_value=_ZERO,
            vat_rate_id=row.vat_rate_id,
            vat_rate_label=row.vat_rate_label,
            vat_rate_percent=row.vat_rate_percent,
            subtotal_excl_vat=row.net_amount,
            subtotal_incl_vat=row.gross_amount,
            line_discount_amount=_ZERO,
            document_discount_share=_ZERO,
            taxable_amount=row.net_amount,
            vat_total=row.vat_amount,
            total_incl_vat=row.gross_amount,
        )
        line.line_taxes = []
        credit.lines.append(line)
        line_tax_rows.append((line, row, basis))
        intent = input_map.get(row.source_basis_line_id)
        # A full-remaining draft has no line-level request provenance; use its
        # selected remaining gross as the durable replay intent.  Explicit
        # requests preserve QUANTITY versus GROSS_AMOUNT exactly.
        input_mode = intent.input_mode if intent is not None else CreditLineInputMode.GROSS_AMOUNT
        input_quantity = intent.quantity if intent is not None else None
        input_gross = intent.gross_amount if intent is not None else row.gross_amount
        correction.lines.append(
            InvoiceCorrectionLine(
                company_id=source.company_id,
                correction_id=correction.id,
                source_basis_line_id=basis.id,
                sort_order=order,
                input_mode=input_mode.value,
                input_quantity=input_quantity,
                input_gross_amount=input_gross,
                quantity=row.quantity,
                net_amount=row.net_amount,
                vat_amount=row.vat_amount,
                gross_amount=row.gross_amount,
                base_net_amount=row.base_net_amount,
                base_vat_amount=row.base_vat_amount,
                base_gross_amount=row.base_gross_amount,
            )
        )
    await session.flush()
    if credit.tax_mode.value == "LINE":
        for line, row, basis in line_tax_rows:
            if row.vat_rate_id is not None:
                if basis.effective_vat_percent is None:
                    raise CreditConflictError(
                        "Source basis lacks its frozen effective VAT snapshot.",
                        code="CREDIT_SOURCE_NO_TAX_SNAPSHOT",
                    )
                line.line_taxes.append(
                    InvoiceLineTax(
                        invoice_line_id=line.id,
                        vat_rate_id=row.vat_rate_id,
                        vat_rate_label=row.vat_rate_label or "",
                        vat_rate_percent=row.vat_rate_percent or _ZERO,
                        effective_vat_percent=basis.effective_vat_percent,
                        taxable_amount=row.net_amount,
                        tax_amount=row.vat_amount,
                    )
                )
    if credit.tax_mode.value == "DOCUMENT" and calculation.lines:
        first = calculation.lines[0]
        first_basis = basis_map[first.source_basis_line_id]
        if first.vat_rate_id is not None:
            if first_basis.effective_vat_percent is None:
                raise CreditConflictError(
                    "Source basis lacks its frozen effective VAT snapshot.",
                    code="CREDIT_SOURCE_NO_TAX_SNAPSHOT",
                )
            credit.taxes.append(
                InvoiceTax(
                    invoice_id=credit.id,
                    vat_rate_id=first.vat_rate_id,
                    vat_rate_label=first.vat_rate_label or "",
                    vat_rate_percent=first.vat_rate_percent or _ZERO,
                    effective_vat_percent=first_basis.effective_vat_percent,
                    taxable_amount=calculation.net_amount,
                    tax_amount=calculation.vat_amount,
                )
            )
    await session.flush()
    return credit


async def create_credit_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
    body: CreditDraftCreate,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    await set_rls_company(session, company_id)
    try:
        source = await _lock_credit_source_context(
            session, company_id=company_id, source_id=source_id
        )
        await _assert_advance_not_final_frozen(session, source)
        calculation = await calculate_credit(
            session, company_id=company_id, source_id=source_id, request=body
        )
        credit = await _persist_credit_draft(
            session, source=source, calculation=calculation, body=body, creator_id=creator_id
        )
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=source.quote_id,
            invoice_id=credit.id,
            actor_user_id=creator_id,
            event_type=DocumentChainEventType.INVOICE_CREATED,
            metadata={"document_kind": InvoiceDocumentKind.CREDIT_NOTE.value},
        )
        await session.flush()
        read = await _load_invoice_read(session, credit)
        await session.commit()
        return read
    except Exception:
        await session.rollback()
        raise


async def update_credit_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    credit_id: uuid.UUID,
    body: CreditDraftUpdate,
    actor_user_id: uuid.UUID | None,
) -> InvoiceRead | None:
    await set_rls_company(session, company_id)
    try:
        source_id = await session.scalar(
            select(InvoiceCorrection.source_invoice_id)
            .join(Invoice, Invoice.id == InvoiceCorrection.credit_note_id)
            .where(
                Invoice.id == credit_id,
                Invoice.company_id == company_id,
                Invoice.document_kind == InvoiceDocumentKind.CREDIT_NOTE,
            )
        )
        if source_id is None:
            return None
        source = await _lock_credit_source_context(
            session, company_id=company_id, source_id=source_id
        )
        credit = (
            await session.execute(
                select(Invoice)
                .where(Invoice.id == credit_id, Invoice.company_id == company_id)
                .options(
                    selectinload(Invoice.lines).selectinload(InvoiceLine.line_taxes),
                    selectinload(Invoice.taxes),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if credit is None:
            return None
        if (
            InvoiceDocumentKind(credit.document_kind) != InvoiceDocumentKind.CREDIT_NOTE
            or InvoiceStatus(credit.status) != InvoiceStatus.DRAFT
        ):
            raise CreditConflictError("Only DRAFT Credit Notes can be updated.")
        correction = (
            await session.execute(
                select(InvoiceCorrection)
                .where(
                    InvoiceCorrection.credit_note_id == credit.id,
                    InvoiceCorrection.source_invoice_id == source.id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if correction is None:
            raise CreditConflictError("Credit source changed while acquiring locks.")
        await _assert_advance_not_final_frozen(session, source)
        calculation = await calculate_credit(
            session, company_id=company_id, source_id=source.id, request=body
        )
        credit = await _persist_credit_draft(
            session,
            source=source,
            calculation=calculation,
            body=body,
            creator_id=actor_user_id,
            existing=credit,
        )
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=source.quote_id,
            invoice_id=credit.id,
            actor_user_id=actor_user_id,
            event_type=DocumentChainEventType.INVOICE_UPDATED,
            metadata={"document_kind": InvoiceDocumentKind.CREDIT_NOTE.value},
        )
        await session.flush()
        read = await _load_invoice_read(session, credit)
        await session.commit()
        return read
    except Exception:
        await session.rollback()
        raise


async def issue_credit(
    session: AsyncSession,
    *,
    credit: Invoice,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    locked_source: Invoice | None = None,
    locked_correction: InvoiceCorrection | None = None,
) -> None:
    """Revalidate durable intent under source/Credit locks, then freeze it."""
    correction = locked_correction
    if correction is None:
        correction = (
            await session.execute(
                select(InvoiceCorrection)
                .where(InvoiceCorrection.credit_note_id == credit.id)
                .with_for_update()
            )
        ).scalar_one()
    source = locked_source
    if source is None:
        source = await _lock_credit_source_context(
            session, company_id=company_id, source_id=correction.source_invoice_id
        )
    if (
        correction.credit_note_id != credit.id
        or correction.source_invoice_id != source.id
        or source.company_id != company_id
    ):
        raise CreditConflictError(
            "Credit source changed while acquiring settlement locks."
        )
    if correction.full_remaining is None or correction.intent_provenance == "MIGRATED_AMBIGUOUS":
        raise CreditConflictError(
            "This migrated Credit DRAFT needs an explicit intent confirmation.",
            code="CREDIT_DRAFT_INTENT_CONFIRMATION_REQUIRED",
        )
    # The selected correction rows are durable request provenance.  A native
    # full-remaining DRAFT deliberately replays its semantic mode, rather
    # than its old materialised rows, so it covers the actual remaining basis
    # at issue time.  Explicit selections replay exactly.
    selections = list(
        (
            await session.execute(
                select(InvoiceCorrectionLine)
                .where(InvoiceCorrectionLine.correction_id == correction.id)
                .order_by(InvoiceCorrectionLine.sort_order)
                .with_for_update()
            )
        ).scalars()
    )
    request = CreditCalculationRequest(
        full_remaining=bool(correction.full_remaining),
        lines=[] if correction.full_remaining else [
            CreditCalculationLineInput(
                source_basis_line_id=x.source_basis_line_id,
                input_mode=CreditLineInputMode(x.input_mode),
                quantity=Decimal(str(x.input_quantity)) if x.input_quantity is not None else None,
                gross_amount=Decimal(str(x.input_gross_amount))
                if x.input_gross_amount is not None
                else None,
            )
            for x in selections
        ],
    )
    try:
        calculation = await calculate_credit(
            session,
            company_id=company_id,
            source_id=source.id,
            request=request,
            allow_stale_exhaustion=True,
        )
    except CreditValidationError as exc:
        # A DRAFT was valid when it was saved.  Once a competing issued Credit
        # has consumed that basis, issue is a stale-command conflict, not an
        # invalid user input.
        raise CreditConflictError(
            "Credit basis changed after the DRAFT was calculated.", code="CREDIT_STALE_BASIS"
        ) from exc
    selected_by_basis = {row.source_basis_line_id: row for row in selections}
    calculated_by_basis = {row.source_basis_line_id: row for row in calculation.lines}
    if not correction.full_remaining and (set(selected_by_basis) != set(calculated_by_basis) or any(
        (
            Decimal(str(selected.quantity)),
            Decimal(str(selected.net_amount)),
            Decimal(str(selected.vat_amount)),
            Decimal(str(selected.gross_amount)),
            Decimal(str(selected.base_net_amount)),
            Decimal(str(selected.base_vat_amount)),
            Decimal(str(selected.base_gross_amount)),
        )
        != (
            calculated.quantity,
            calculated.net_amount,
            calculated.vat_amount,
            calculated.gross_amount,
            calculated.base_net_amount,
            calculated.base_vat_amount,
            calculated.base_gross_amount,
        )
        for basis_id, selected in selected_by_basis.items()
        for calculated in [calculated_by_basis[basis_id]]
    )):
        raise CreditConflictError(
            "Credit basis changed after the DRAFT was calculated.", code="CREDIT_STALE_BASIS"
        )
    if correction.full_remaining:
        # ``full_remaining`` is semantic intent, not a reservation of the
        # materialised DRAFT snapshot.  Rebuild *all* owned snapshots from the
        # issue-time calculation while the source and Credit are locked.  The
        # same authoritative draft builder owns invoice lines/taxes,
        # correction lines and all invoice/base totals, so no alternate money
        # calculation can drift from create/update.
        await session.refresh(credit, attribute_names=["lines", "taxes"])
        await _persist_credit_draft(
            session,
            source=source,
            calculation=calculation,
            body=CreditDraftUpdate(
                full_remaining=True,
                invoice_date=credit.invoice_date,
                due_date=credit.due_date,
                supply_or_advance_date=credit.supply_or_advance_date,
                reference_number=credit.reference_number,
            ),
            creator_id=credit.creator_id,
            existing=credit,
        )
    # Freeze source party identity, never current master data.
    snapshot = source.party_snapshot
    if snapshot is None:
        raise CreditConflictError(
            "Source invoice lacks an issue party snapshot.", code="CREDIT_SOURCE_NO_SNAPSHOT"
        )
    credit.party_snapshot = InvoicePartySnapshot(
        company_id=company_id,
        invoice_id=credit.id,
        provenance=snapshot.provenance,
        seller_name=snapshot.seller_name,
        seller_legal_name=snapshot.seller_legal_name,
        seller_vat_id=snapshot.seller_vat_id,
        seller_coc_number=snapshot.seller_coc_number,
        seller_email=snapshot.seller_email,
        seller_phone=snapshot.seller_phone,
        seller_address=snapshot.seller_address,
        buyer_name=snapshot.buyer_name,
        buyer_company_name=snapshot.buyer_company_name,
        buyer_contact_name=snapshot.buyer_contact_name,
        buyer_vat_id=snapshot.buyer_vat_id,
        buyer_email=snapshot.buyer_email,
        buyer_phone=snapshot.buyer_phone,
        buyer_address=snapshot.buyer_address,
        locale=snapshot.locale,
        logo_id=snapshot.logo_id,
    )
    if InvoiceDocumentKind(source.document_kind) in {
        InvoiceDocumentKind.STANDARD,
        InvoiceDocumentKind.FINAL,
    }:
        affects_revenue = True
    else:
        affects_revenue = bool(
            source.quote_id
            and await session.scalar(
                select(Invoice.id)
                .where(
                    Invoice.quote_id == source.quote_id,
                    Invoice.document_kind == InvoiceDocumentKind.FINAL,
                    Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.COMPLETED]),
                )
                .limit(1)
            )
        )
    # The DB permits only an entirely empty DRAFT aggregate or the full
    # issue-time fact.  Determine the Advance-specific flag before assigning
    # any aggregate because its lookup can autoflush the session.
    correction.issued_net_amount = calculation.net_amount
    correction.issued_vat_amount = calculation.vat_amount
    correction.issued_gross_amount = calculation.gross_amount
    correction.issued_base_net_amount = calculation.base_net_amount
    correction.issued_base_vat_amount = calculation.base_vat_amount
    correction.issued_base_gross_amount = calculation.base_gross_amount
    correction.affects_revenue = affects_revenue
    source.credited_total = Decimal(str(source.credited_total)) + calculation.gross_amount
    source.base_credited_total = (
        Decimal(str(source.base_credited_total)) + calculation.base_gross_amount
    )
    source.credit_status = (
        InvoiceCreditStatus.CREDITED
        if source.credited_total == source.payable_before_payments
        else InvoiceCreditStatus.PARTIALLY_CREDITED
    )
    charge = Decimal(str(source.payable_before_payments)) - Decimal(str(source.credited_total))
    cash = Decimal(str(source.incoming_payment_total))
    base_charge = Decimal(str(source.base_payable_before_payments)) - Decimal(
        str(source.base_credited_total)
    )
    base_cash = Decimal(str(source.base_incoming_payment_total))
    source.due_amount = max(charge - cash, _ZERO)
    source.refund_due_amount = max(cash - charge, _ZERO)
    source.base_due_amount = max(base_charge - base_cash, _ZERO)
    source.base_refund_due_amount = max(base_cash - base_charge, _ZERO)
    source.settlement_status = (
        InvoiceSettlementStatus.REFUND_DUE
        if source.refund_due_amount
        else (
            InvoiceSettlementStatus.SETTLED
            if source.due_amount == _ZERO
            else (
                InvoiceSettlementStatus.PARTIALLY_SETTLED if cash else InvoiceSettlementStatus.OPEN
            )
        )
    )
    credit.status = InvoiceStatus.SENT
    credit.paid_status = InvoicePaidStatus.NOT_APPLICABLE
    credit.settlement_status = InvoiceSettlementStatus.SETTLED
    credit.issued_at = datetime.now(UTC)
    credit.issued_by_user_id = actor_user_id
    if credit.supply_or_advance_date is None:
        credit.supply_or_advance_date = credit.invoice_date
    config = await get_setting(
        session,
        SETTING_KEY_CREDIT_NUMBERING,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=CreditNumberingConfig,
    )
    credit.invoice_number, credit.sequence_number = await allocate_credit_number(
        session, company_id, credit.invoice_date, numbering_config=config or CreditNumberingConfig()
    )
    await append_document_chain_event(
        session,
        company_id=company_id,
        quote_id=source.quote_id,
        invoice_id=credit.id,
        actor_user_id=actor_user_id,
        event_type=DocumentChainEventType.INVOICE_ISSUED,
        metadata={
            "document_kind": InvoiceDocumentKind.CREDIT_NOTE.value,
            "status": InvoiceStatus.SENT.value,
        },
    )
    await session.flush()
