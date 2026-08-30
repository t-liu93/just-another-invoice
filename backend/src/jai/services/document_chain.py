"""M12 chain projection and append-only lifecycle-event helpers.

This module is the only place that derives document-chain totals.  It keeps
the API and Vue layer from performing settlement arithmetic.
"""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jai.db import set_rls_company
from jai.models._enums import (
    DocumentChainEventType,
    InvoiceDocumentKind,
    InvoiceStatus,
    QuoteSettlementMode,
    QuoteStatus,
)
from jai.models.document import DocumentChainEvent, InvoiceCorrection, InvoiceRelation
from jai.models.invoice import Invoice
from jai.models.payment import Payment
from jai.models.quote import Quote
from jai.schemas.document_chain import (
    DocumentChainAvailableActionRead,
    DocumentChainEventRead,
    DocumentChainNodeRead,
    DocumentChainRead,
    DocumentChainRelationRead,
)
from jai.schemas.quote import DocumentChainTotals

type SafeValue = object


class _EventMetadata(BaseModel):
    """Closed, non-renderable metadata shared by one event type only."""

    model_config = ConfigDict(extra="forbid")


class _ModeLockedMetadata(_EventMetadata):
    mode: QuoteSettlementMode


class _InvoiceKindMetadata(_EventMetadata):
    document_kind: InvoiceDocumentKind


class _InvoiceStatusMetadata(_InvoiceKindMetadata):
    status: InvoiceStatus


class _PaymentMetadata(_EventMetadata):
    payment_id: uuid.UUID
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def _amount_is_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Document-chain event amount must be finite.")
        return value


class _CreditRelationMetadata(_EventMetadata):
    credit_note_id: uuid.UUID


class _CancellationCreditMetadata(_EventMetadata):
    source_invoice_id: uuid.UUID


_EVENT_METADATA_MODELS: dict[DocumentChainEventType, type[_EventMetadata]] = {
    DocumentChainEventType.MODE_LOCKED: _ModeLockedMetadata,
    DocumentChainEventType.INVOICE_CREATED: _InvoiceKindMetadata,
    DocumentChainEventType.INVOICE_UPDATED: _InvoiceKindMetadata,
    DocumentChainEventType.INVOICE_ISSUED: _InvoiceStatusMetadata,
    DocumentChainEventType.INVOICE_STATUS_CHANGED: _InvoiceStatusMetadata,
    DocumentChainEventType.INVOICE_DELETED: _InvoiceKindMetadata,
    DocumentChainEventType.QUOTE_PAYMENT_CREATED: _PaymentMetadata,
    DocumentChainEventType.QUOTE_PAYMENT_UPDATED: _PaymentMetadata,
    DocumentChainEventType.QUOTE_PAYMENT_DELETED: _PaymentMetadata,
    DocumentChainEventType.INVOICE_PAYMENT_CREATED: _PaymentMetadata,
    DocumentChainEventType.INVOICE_PAYMENT_UPDATED: _PaymentMetadata,
    DocumentChainEventType.INVOICE_PAYMENT_DELETED: _PaymentMetadata,
    DocumentChainEventType.REPLACEMENT_CREATED: _CreditRelationMetadata,
    DocumentChainEventType.COMPENSATING_INVOICE_CREATED: _CreditRelationMetadata,
    DocumentChainEventType.PROJECT_CANCELLATION_CREDIT_CREATED: _CancellationCreditMetadata,
}


class ModeConflictError(ValueError):
    """A deterministic, machine-readable quote-branch conflict."""

    code = "MODE_CONFLICT"


def _safe_metadata(
    event_type: DocumentChainEventType, metadata: dict[str, SafeValue] | None
) -> dict[str, object]:
    """Validate and JSON-serialise the one closed schema for an event type."""
    try:
        # JSON mode deliberately turns UUID/Decimal into canonical strings.
        return (
            _EVENT_METADATA_MODELS[event_type]
            .model_validate(metadata or {})
            .model_dump(mode="json")
        )
    except ValidationError as exc:
        raise ValueError("Invalid document-chain event metadata.") from exc


async def append_document_chain_event(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    event_type: DocumentChainEventType,
    quote_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    metadata: dict[str, SafeValue] | None = None,
) -> DocumentChainEvent:
    """Add one event without committing; caller owns the outer transaction."""
    event = DocumentChainEvent(
        company_id=company_id,
        quote_id=quote_id,
        invoice_id=invoice_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        metadata_json=_safe_metadata(event_type, metadata),
    )
    session.add(event)
    await session.flush()
    return event


async def lock_quote_mode(
    session: AsyncSession,
    quote: Quote,
    target: QuoteSettlementMode,
    *,
    actor_user_id: uuid.UUID | None,
) -> None:
    """Lock a Quote branch within its already-held transaction/row lock."""
    current = QuoteSettlementMode(quote.settlement_mode)
    if current not in (QuoteSettlementMode.UNSET, target):
        raise ModeConflictError(
            f"Quote is locked to {current.value}; {target.value} is not allowed."
        )
    if current == target:
        return
    quote.settlement_mode = target
    quote.settlement_mode_locked_at = datetime.now().astimezone()
    await append_document_chain_event(
        session,
        company_id=quote.company_id,
        quote_id=quote.id,
        actor_user_id=actor_user_id,
        event_type=DocumentChainEventType.MODE_LOCKED,
        metadata={"mode": target.value},
    )


async def quote_has_converted_invoice(
    session: AsyncSession, *, company_id: uuid.UUID, quote_id: uuid.UUID
) -> bool:
    """Invoice.quote_id is the conversion authority; backlink is only a cache."""
    return (
        await session.scalar(
            select(Invoice.id)
            .where(Invoice.company_id == company_id, Invoice.quote_id == quote_id)
            .limit(1)
        )
    ) is not None


async def conversion_is_available(session: AsyncSession, quote: Quote) -> bool:
    """One predicate shared by projection and quote conversion command."""
    return (
        QuoteSettlementMode(quote.settlement_mode)
        in {
            QuoteSettlementMode.UNSET,
            QuoteSettlementMode.DIRECT_INVOICE,
            QuoteSettlementMode.RECEIPT_ONLY,
        }
        and QuoteStatus(quote.status)
        in {
            QuoteStatus.SENT,
            QuoteStatus.ACCEPTED,
            QuoteStatus.EXPIRED,
        }
        and not await quote_has_converted_invoice(
            session, company_id=quote.company_id, quote_id=quote.id
        )
    )


def _totals(invoices: list[Invoice], quote_payments: list[Payment]) -> DocumentChainTotals:
    zero = Decimal("0")
    charge = sum((Decimal(str(i.payable_before_payments)) for i in invoices), zero)
    credit = sum((Decimal(str(i.credited_total)) for i in invoices), zero)
    incoming = sum((Decimal(str(i.incoming_payment_total)) for i in invoices), zero)
    refund = sum((Decimal(str(i.refunded_total)) for i in invoices), zero)
    base_charge = sum((Decimal(str(i.base_payable_before_payments)) for i in invoices), zero)
    base_credit = sum((Decimal(str(i.base_credited_total)) for i in invoices), zero)
    base_incoming = sum((Decimal(str(i.base_incoming_payment_total)) for i in invoices), zero)
    base_refund = sum((Decimal(str(i.base_refunded_total)) for i in invoices), zero)
    if not invoices:
        incoming = sum((Decimal(str(p.amount)) for p in quote_payments), zero)
        base_incoming = sum((Decimal(str(p.base_amount)) for p in quote_payments), zero)
    return DocumentChainTotals(
        charge_total=charge,
        credit_total=credit,
        incoming_payment_total=incoming,
        refund_total=refund,
        due_amount=max(charge - credit - incoming + refund, zero),
        refund_due_amount=max(incoming - refund - charge + credit, zero),
        base_charge_total=base_charge,
        base_credit_total=base_credit,
        base_incoming_payment_total=base_incoming,
        base_refund_total=base_refund,
        base_due_amount=max(base_charge - base_credit - base_incoming + base_refund, zero),
        base_refund_due_amount=max(base_incoming - base_refund - base_charge + base_credit, zero),
    )


async def get_document_chain(
    session: AsyncSession, *, company_id: uuid.UUID, quote_id: uuid.UUID
) -> DocumentChainRead | None:
    """Load the bounded-query, ordered projection rooted at a Quote."""
    await set_rls_company(session, company_id)
    quote = (
        await session.execute(
            select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id)
        )
    ).scalar_one_or_none()
    if quote is None:
        return None
    invoice_rows = (
        await session.execute(
            select(
                Invoice,
                InvoiceCorrection.source_invoice_id,
                InvoiceRelation.related_credit_note_id,
                InvoiceRelation.relation_type,
            )
            .outerjoin(InvoiceCorrection, InvoiceCorrection.credit_note_id == Invoice.id)
            .outerjoin(InvoiceRelation, InvoiceRelation.invoice_id == Invoice.id)
            .where(Invoice.company_id == company_id, Invoice.quote_id == quote.id)
            .order_by(Invoice.created_at, Invoice.id)
        )
    ).all()
    invoices = [invoice for invoice, _, _, _ in invoice_rows]
    invoice_ids = [invoice.id for invoice in invoices]
    payments = list(
        (
            await session.execute(
                select(Payment)
                .where(
                    Payment.company_id == company_id,
                    or_(
                        Payment.quote_id == quote.id,
                        Payment.invoice_id.in_(invoice_ids or [uuid.uuid4()]),
                    ),
                )
                .order_by(Payment.created_at, Payment.id)
            )
        ).scalars()
    )
    events = list(
        (
            await session.execute(
                select(DocumentChainEvent)
                .where(
                    DocumentChainEvent.company_id == company_id,
                    or_(
                        DocumentChainEvent.quote_id == quote.id,
                        DocumentChainEvent.invoice_id.in_(invoice_ids or [uuid.uuid4()]),
                    ),
                )
                .order_by(DocumentChainEvent.event_order)
            )
        ).scalars()
    )
    quote_payments = [payment for payment in payments if payment.quote_id == quote.id]
    totals = _totals(invoices, quote_payments)
    node_rows: list[tuple[tuple[object, int, str], DocumentChainNodeRead]] = [
        (
            (quote.created_at, 0, str(quote.id)),
            DocumentChainNodeRead(
                id=quote.id,
                node_type="QUOTE",
                number=quote.quote_number,
                occurred_on=quote.quote_date,
            ),
        )
    ]
    node_rows.extend(
        (
            (invoice.created_at, 1, str(invoice.id)),
            DocumentChainNodeRead(
                id=invoice.id,
                node_type="INVOICE",
                document_kind=InvoiceDocumentKind(invoice.document_kind),
                number=invoice.invoice_number,
                status=invoice.status,
                occurred_on=invoice.invoice_date,
                charge_amount=Decimal(str(invoice.payable_before_payments)),
                credit_amount=Decimal(str(invoice.credited_total)),
                incoming_payment_amount=Decimal(str(invoice.incoming_payment_total)),
                refund_amount=Decimal(str(invoice.refunded_total)),
                due_amount=Decimal(str(invoice.due_amount)),
                refund_due_amount=Decimal(str(invoice.refund_due_amount)),
            ),
        )
        for invoice in invoices
    )
    node_rows.extend(
        (
            (payment.created_at, 2, str(payment.id)),
            DocumentChainNodeRead(
                id=payment.id,
                node_type="PAYMENT",
                number=None,
                occurred_on=payment.payment_date,
                incoming_payment_amount=Decimal(str(payment.amount)),
            ),
        )
        for payment in payments
    )
    nodes = [node for _, node in sorted(node_rows, key=lambda row: row[0])]
    relations = [
        DocumentChainRelationRead(
            relation_type="QUOTE_TO_INVOICE", from_node_id=quote.id, to_node_id=invoice.id
        )
        for invoice in invoices
    ]
    credit_sources = {
        invoice.id: source_id
        for invoice, source_id, _, _ in invoice_rows
        if source_id is not None
    }
    relations.extend(
        DocumentChainRelationRead(
            relation_type="INVOICE_TO_CREDIT_NOTE",
            from_node_id=source_id,
            to_node_id=credit_id,
        )
        for credit_id, source_id in credit_sources.items()
    )
    relations.extend(
        DocumentChainRelationRead(
            relation_type=relation_type.value,
            from_node_id=related_credit_note_id,
            to_node_id=invoice.id,
        )
        for invoice, _, related_credit_note_id, relation_type in invoice_rows
        if related_credit_note_id is not None and relation_type is not None
    )
    for payment in payments:
        if payment.quote_id == quote.id:
            relations.append(
                DocumentChainRelationRead(
                    relation_type="QUOTE_TO_PAYMENT", from_node_id=quote.id, to_node_id=payment.id
                )
            )
        if payment.invoice_id is not None and payment.invoice_id in invoice_ids:
            relations.append(
                DocumentChainRelationRead(
                    relation_type="INVOICE_TO_PAYMENT",
                    from_node_id=payment.invoice_id,
                    to_node_id=payment.id,
                )
            )
    mode = QuoteSettlementMode(quote.settlement_mode)
    # Delayed import keeps the Advance command's event/mode helpers acyclic
    # while making this projection use the exact same structural predicate as
    # the create command.  It intentionally excludes amount/date input checks.
    from jai.services.advance import assess_advance_creation

    advance_creation = await assess_advance_creation(session, quote)
    issued_advance_exists = any(
        InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.ADVANCE
        and InvoiceStatus(invoice.status) in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
        for invoice in invoices
    )
    final_exists = any(
        InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.FINAL
        for invoice in invoices
    )
    open_advance_exists = any(
        InvoiceDocumentKind(invoice.document_kind) == InvoiceDocumentKind.ADVANCE
        and InvoiceStatus(invoice.status) == InvoiceStatus.DRAFT
        for invoice in invoices
    )
    actions = [
        DocumentChainAvailableActionRead(
            code="CONVERT_TO_INVOICE",
            available=await conversion_is_available(session, quote),
        ),
        DocumentChainAvailableActionRead(
            code="RECORD_QUOTE_PAYMENT",
            available=(
                mode in {QuoteSettlementMode.UNSET, QuoteSettlementMode.RECEIPT_ONLY}
                and QuoteStatus(quote.status) == QuoteStatus.ACCEPTED
                and quote.vat_treatment_code == "NL_DOMESTIC"
                and not invoices
            ),
        ),
        DocumentChainAvailableActionRead(
            code="CREATE_ADVANCE", available=advance_creation.available
        ),
        DocumentChainAvailableActionRead(
            code="CREATE_FINAL",
            available=(
                mode == QuoteSettlementMode.FORMAL_ADVANCE
                and QuoteStatus(quote.status) == QuoteStatus.ACCEPTED
                and issued_advance_exists
                and not final_exists
                and not open_advance_exists
            ),
        ),
        DocumentChainAvailableActionRead(code="CREATE_CREDIT_NOTE", available=False),
    ]
    return DocumentChainRead(
        quote_id=quote.id,
        quote_number=quote.quote_number,
        settlement_mode=mode,
        settlement_mode_locked_at=quote.settlement_mode_locked_at,
        nodes=nodes,
        relations=relations,
        events=[
            DocumentChainEventRead(
                id=event.id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                quote_id=event.quote_id,
                invoice_id=event.invoice_id,
                actor_user_id=event.actor_user_id,
                metadata=event.metadata_json,
            )
            for event in events
        ],
        totals=totals,
        quote_total=Decimal(str(quote.total_incl_vat)),
        available_actions=actions,
    )


async def get_invoice_document_chain(
    session: AsyncSession, *, company_id: uuid.UUID, invoice_id: uuid.UUID
) -> DocumentChainRead | None:
    await set_rls_company(session, company_id)
    invoice = (
        await session.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == company_id)
        )
    ).scalar_one_or_none()
    if invoice is None:
        return None
    if invoice.quote_id is None:
        # Direct documents have no Quote root.  Their correction family is an
        # undirected graph: source <-> Credit and Credit <-> positive follow-up.
        # One company-scoped recursive CTE finds the complete component from
        # any member.  The UUID path prevents corrupted/cyclic provenance from
        # causing an unbounded walk; all subsequent reads are fixed bulk queries.
        component_ids = list(
            (
                await session.execute(
                    text(
                        "WITH RECURSIVE component(id, path) AS ("
                        " SELECT CAST(:invoice_id AS uuid), ARRAY[CAST(:invoice_id AS uuid)]"
                        " UNION ALL "
                        " SELECT edge.id, component.path || edge.id "
                        " FROM component "
                        " JOIN LATERAL ("
                        "   SELECT correction.credit_note_id AS id "
                        "   FROM invoice_correction AS correction "
                        "   WHERE correction.company_id = CAST(:company_id AS uuid) "
                        "     AND correction.source_invoice_id = component.id "
                        "   UNION "
                        "   SELECT correction.source_invoice_id AS id "
                        "   FROM invoice_correction AS correction "
                        "   WHERE correction.company_id = CAST(:company_id AS uuid) "
                        "     AND correction.credit_note_id = component.id "
                        "   UNION "
                        "   SELECT relation.invoice_id AS id "
                        "   FROM invoice_relation AS relation "
                        "   WHERE relation.company_id = CAST(:company_id AS uuid) "
                        "     AND relation.related_credit_note_id = component.id "
                        "   UNION "
                        "   SELECT relation.related_credit_note_id AS id "
                        "   FROM invoice_relation AS relation "
                        "   WHERE relation.company_id = CAST(:company_id AS uuid) "
                        "     AND relation.invoice_id = component.id"
                        " ) AS edge ON TRUE "
                        " WHERE NOT edge.id = ANY(component.path)"
                        ") SELECT DISTINCT id FROM component ORDER BY id"
                    ),
                    {"invoice_id": invoice.id, "company_id": company_id},
                )
            ).scalars()
        )
        direct_invoices = list(
            (
                await session.execute(
                    select(Invoice)
                    .where(
                        Invoice.company_id == company_id,
                        Invoice.id.in_(component_ids),
                    )
                    .order_by(Invoice.id)
                )
            ).scalars()
        )
        direct_invoice_ids = [item.id for item in direct_invoices]
        corrections = list(
            (
                await session.execute(
                    select(InvoiceCorrection)
                    .where(
                        InvoiceCorrection.company_id == company_id,
                        InvoiceCorrection.source_invoice_id.in_(direct_invoice_ids),
                        InvoiceCorrection.credit_note_id.in_(direct_invoice_ids),
                    )
                    .order_by(InvoiceCorrection.id)
                )
            ).scalars()
        )
        related_positive_rows = list(
            (
                await session.execute(
                    select(InvoiceRelation)
                    .where(
                        InvoiceRelation.company_id == company_id,
                        InvoiceRelation.related_credit_note_id.in_(direct_invoice_ids),
                        InvoiceRelation.invoice_id.in_(direct_invoice_ids),
                    )
                    .order_by(InvoiceRelation.created_at, InvoiceRelation.id)
                )
            ).scalars()
        )
        payments = list(
            (
                await session.execute(
                    select(Payment)
                    .where(
                        Payment.company_id == company_id,
                        Payment.invoice_id.in_(direct_invoice_ids),
                    )
                    .order_by(Payment.created_at, Payment.id)
                )
            ).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(DocumentChainEvent)
                    .where(
                        DocumentChainEvent.company_id == company_id,
                        DocumentChainEvent.invoice_id.in_(direct_invoice_ids),
                    )
                    .order_by(DocumentChainEvent.event_order)
                )
            ).scalars()
        )
        nodes = [
            *[
                DocumentChainNodeRead(
                    id=item.id,
                    node_type="INVOICE",
                    document_kind=InvoiceDocumentKind(item.document_kind),
                    number=item.invoice_number,
                    status=item.status,
                    occurred_on=item.invoice_date,
                    charge_amount=Decimal(str(item.payable_before_payments)),
                    credit_amount=Decimal(str(item.credited_total)),
                    incoming_payment_amount=Decimal(str(item.incoming_payment_total)),
                    refund_amount=Decimal(str(item.refunded_total)),
                    due_amount=Decimal(str(item.due_amount)),
                    refund_due_amount=Decimal(str(item.refund_due_amount)),
                )
                for item in direct_invoices
            ],
            *[
                DocumentChainNodeRead(
                    id=payment.id,
                    node_type="PAYMENT",
                    occurred_on=payment.payment_date,
                    incoming_payment_amount=Decimal(str(payment.amount)),
                )
                for payment in payments
            ],
        ]
        nodes.sort(key=lambda node: (node.occurred_on, str(node.id)))
        return DocumentChainRead(
            settlement_mode=QuoteSettlementMode.UNSET,
            nodes=nodes,
            relations=[
                DocumentChainRelationRead(
                    relation_type="INVOICE_TO_PAYMENT",
                    from_node_id=payment.invoice_id,
                    to_node_id=payment.id,
                )
                for payment in payments
                if payment.invoice_id is not None
            ]
            + [
                DocumentChainRelationRead(
                    relation_type="INVOICE_TO_CREDIT_NOTE",
                    from_node_id=correction.source_invoice_id,
                    to_node_id=correction.credit_note_id,
                )
                for correction in corrections
            ]
            + [
                DocumentChainRelationRead(
                    relation_type=relation.relation_type.value,
                    from_node_id=relation.related_credit_note_id,
                    to_node_id=relation.invoice_id,
                )
                for relation in related_positive_rows
            ],
            events=[
                DocumentChainEventRead(
                    id=event.id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    quote_id=event.quote_id,
                    invoice_id=event.invoice_id,
                    actor_user_id=event.actor_user_id,
                    metadata=event.metadata_json,
                )
                for event in events
            ],
            totals=_totals(direct_invoices, []),
            available_actions=[
                DocumentChainAvailableActionRead(code="CREATE_ADVANCE", available=False),
                DocumentChainAvailableActionRead(code="CREATE_CREDIT_NOTE", available=False),
            ],
        )
    return await get_document_chain(session, company_id=company_id, quote_id=invoice.quote_id)
