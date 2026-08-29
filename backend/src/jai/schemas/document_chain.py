"""Backend-authoritative M12 document-chain projection schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

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
    quote_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class DocumentChainAvailableActionRead(BaseModel):
    code: str
    available: bool


class DocumentChainRead(BaseModel):
    """Read-only aggregate; all totals are calculated in services."""

    quote_id: uuid.UUID | None = None
    quote_number: str | None = None
    settlement_mode: QuoteSettlementMode
    settlement_mode_locked_at: datetime | None = None
    nodes: list[DocumentChainNodeRead]
    relations: list[DocumentChainRelationRead]
    events: list[DocumentChainEventRead]
    totals: DocumentChainTotals
    quote_total: Decimal = Decimal("0")
    final_total: Decimal | None = None
    quote_final_variance: Decimal | None = None
    available_actions: list[DocumentChainAvailableActionRead]
