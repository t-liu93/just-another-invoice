"""Content template / content block / note template ORM models (M6 step 4).

Three "named libraries" shared by quotes and invoices:
- ``DocumentTemplate`` + ``DocumentTemplateLine`` – reusable line-item bundles.
- ``ContentBlock`` – standard text blocks (warranty, T&C, bank info, payment terms).
- ``NoteTemplate`` – reusable note snippets.

Design:
- All tables are company-scoped (company_id FK → company.id, RESTRICT).
- UNIQUE(company_id, name) on each root table prevents duplicates.
- ContentBlock has a partial unique on (company_id, kind) WHERE is_default,
  ensuring at most one default per kind.
- DocumentTemplateLine uses SET NULL for unit_id (deleting a unit does not
  break template lines) and RESTRICT for vat_rate_id (must not delete a rate
  still referenced by a template).
- Applying a template or content block to a document is always a *value copy*
  (snapshot); deleting a template/block never affects persisted documents.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jai.db import Base
from jai.models._enums import ContentBlockKind, DiscountType, DocumentTemplateScope

_MONEY = Numeric(18, 3)
_DISCOUNT_VALUE = Numeric(10, 3)


# ---------------------------------------------------------------------------
# Document Template + Lines
# ---------------------------------------------------------------------------


class DocumentTemplate(Base):
    """Named library of reusable line-item bundles.

    Each template can apply to quotes, invoices, or both (``applies_to``).
    Lines are value-copied into documents on "apply"; the template is never
    modified by that operation.
    """

    __tablename__ = "document_template"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    applies_to: Mapped[DocumentTemplateScope] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # -- Relationships --------------------------------------------------------
    lines: Mapped[list[DocumentTemplateLine]] = relationship(
        "DocumentTemplateLine",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DocumentTemplateLine.sort_order",
    )

    # -- Table constraints ----------------------------------------------------
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_document_template_company_name"),
    )


class DocumentTemplateLine(Base):
    """Single line within a document template.

    Mirrors InvoiceLineInput / QuoteLineInput structure.  VAT rate FK uses
    RESTRICT so a rate in active use by a template cannot be accidentally deleted.
    """

    __tablename__ = "document_template_line"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_template.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Long-form description; text (red-line 10)."
    )
    quantity: Mapped[object] = mapped_column(_MONEY, nullable=False)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="SET NULL"),
        nullable=True,
    )
    unit_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_price: Mapped[object | None] = mapped_column(_MONEY, nullable=True)
    discount_type: Mapped[DiscountType] = mapped_column(
        nullable=False, server_default="NONE"
    )
    discount_value: Mapped[object] = mapped_column(
        _DISCOUNT_VALUE, nullable=False, server_default=text("0")
    )
    vat_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vat_rate.id", ondelete="RESTRICT"),
        nullable=True,
    )


# ---------------------------------------------------------------------------
# Content Block (standard text blocks)
# ---------------------------------------------------------------------------


class ContentBlock(Base):
    """Standard named text block, categorised by kind.

    Each kind (WARRANTY / TERMS / BANK / PAYMENT_TERMS) can have multiple
    entries; at most one per kind can be marked ``is_default`` (enforced by a
    partial unique index in the migration).
    """

    __tablename__ = "content_block"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ContentBlockKind] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # -- Table constraints ----------------------------------------------------
    __table_args__ = (
        UniqueConstraint("company_id", "kind", "name", name="uq_content_block_company_kind_name"),
        # Partial unique: at most one default per (company_id, kind).
        # Expressed in the Alembic migration as raw SQL; not representable
        # purely in SQLAlchemy UniqueConstraint.
    )


# ---------------------------------------------------------------------------
# Note Template
# ---------------------------------------------------------------------------


class NoteTemplate(Base):
    """Reusable note / remark snippet (named library).

    Value-copied into document ``notes`` on "insert"; deleting a template
    never affects persisted documents.
    """

    __tablename__ = "note_template"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # -- Table constraints ----------------------------------------------------
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_note_template_company_name"),
    )
