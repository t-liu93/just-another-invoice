"""``NumberSequence`` ORM model – concurrency-safe invoice sequence counter (M5 step 2).

Design:
- One row per (company, document_type, scope[, customer_id]) triple.
- The service layer acquires a row-level lock (SELECT … FOR UPDATE) before
  reading and incrementing ``next_value``, so concurrent invoice creation never
  produces duplicate sequence numbers (red-line 4).
- Partial unique indexes enforce uniqueness at the DB level as a second
  safeguard; these are defined in the migration, not here (PG partial indexes
  are not expressible as SQLAlchemy table constraints).
- ``customer_id`` is only populated for CUSTOMER-scope rows; it cascades on
  customer deletion so the customer-specific counter is cleaned up automatically.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import NumberSequenceScope


class NumberSequence(Base):
    """Sequence counter row for a company / customer."""

    __tablename__ = "number_sequence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    document_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Document type identifier, e.g. 'INVOICE'.",
    )

    scope: Mapped[NumberSequenceScope] = mapped_column(
        nullable=False,
        comment="COMPANY or CUSTOMER scope.",
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer.id", ondelete="CASCADE"),
        nullable=True,
        comment="Non-null only for CUSTOMER-scope rows.",
    )

    next_value: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        comment="The next sequence number to be issued (1-based).",
    )

    __table_args__ = (
        CheckConstraint("next_value >= 1", name="ck_number_sequence_next_value_ge1"),
        # Partial unique indexes are created in the Alembic migration (PG only);
        # they are not expressible as SQLAlchemy UniqueConstraint with WHERE.
    )
