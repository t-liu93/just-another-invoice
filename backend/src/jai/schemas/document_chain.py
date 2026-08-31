"""Backend-authoritative M12 document-chain projection schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from jai.models._enums import (
    DocumentChainEventType,
    InvoiceDocumentKind,
    InvoiceStatus,
    QuoteSettlementMode,
)
from jai.schemas.quote import DocumentChainTotals


class DocumentChainNodeRead(BaseModel):
    """A Quote, formal document or cash row in stable chain order."""

    id: uuid.UUID
    node_type: str
    document_kind: InvoiceDocumentKind | None = None
    number: str | None = None
    status: InvoiceStatus | None = None
    occurred_on: date
    charge_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    incoming_payment_amount: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    due_amount: Decimal = Decimal("0")
    refund_due_amount: Decimal = Decimal("0")


class DocumentChainRelationRead(BaseModel):
    """A typed authoritative provenance edge, never a UI-derived backlink."""

    relation_type: str
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID


class DocumentChainEventRead(BaseModel):
    id: uuid.UUID
    event_type: DocumentChainEventType
    occurred_at: datetime
    # The database sequence is the causal tie-breaker for events that share
    # a timestamp.  It is intentionally exposed to the typed timeline, not
    # replaced by random UUID ordering.
    event_order: int
    quote_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class FollowupContextRead(BaseModel):
    """Frozen display facts for a Credit follow-up confirmation."""

    credit_note_id: uuid.UUID
    source_invoice_id: uuid.UUID
    relation_type: Literal["REPLACEMENT_OF", "COMPENSATES_CREDIT"]
    target_document_kind: InvoiceDocumentKind
    gross_amount: Decimal


class DocumentChainAvailableActionRead(BaseModel):
    code: str
    available: bool
    reason_code: str | None = None
    target_id: uuid.UUID | None = None
    target_type: Literal["QUOTE", "INVOICE"] | None = None
    followup_context: FollowupContextRead | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class DocumentChainApplicationRead(BaseModel):
    final_invoice_id: uuid.UUID
    advance_invoice_id: uuid.UUID
    occurred_on: date
    taxable_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal


class _TimelineItem(BaseModel):
    order: int


class DocumentChainTimelineNodeRead(_TimelineItem):
    kind: Literal["NODE"]
    node: DocumentChainNodeRead


class DocumentChainTimelineEventRead(_TimelineItem):
    kind: Literal["EVENT"]
    event: DocumentChainEventRead


class DocumentChainTimelineRelationRead(_TimelineItem):
    kind: Literal["RELATION"]
    relation: DocumentChainRelationRead


class DocumentChainTimelineApplicationRead(_TimelineItem):
    kind: Literal["APPLICATION"]
    application: DocumentChainApplicationRead


DocumentChainTimelineItemRead = Annotated[
    DocumentChainTimelineNodeRead
    | DocumentChainTimelineEventRead
    | DocumentChainTimelineRelationRead
    | DocumentChainTimelineApplicationRead,
    Field(discriminator="kind"),
]


class DocumentChainRead(BaseModel):
    """Read-only aggregate; all totals are calculated in services."""

    quote_id: uuid.UUID | None = None
    quote_number: str | None = None
    settlement_mode: QuoteSettlementMode
    settlement_mode_locked_at: datetime | None = None
    nodes: list[DocumentChainNodeRead]
    relations: list[DocumentChainRelationRead]
    events: list[DocumentChainEventRead]
    timeline: list[DocumentChainTimelineItemRead] = []
    totals: DocumentChainTotals
    quote_total: Decimal = Decimal("0")
    final_total: Decimal | None = None
    quote_final_variance: Decimal | None = None
    available_actions: list[DocumentChainAvailableActionRead]
