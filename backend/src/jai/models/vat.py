"""VAT dictionary ORM models – vat_rate + vat_treatment (M4 step 1).

Design:
- ``VatRate``: per-item tax rate (21 / 9 / 0 NL seeds, user-editable).
- ``VatTreatment``: document-level processing mode (system seeds, read-only UI
  in M4). Drives how a rate is applied or zeroed out (M5/M8 compute the result;
  M10 fills ``report_box``).
- Both tables are company-scoped (red-line 2), with UNIQUE(company_id, code/label).
- ``report_box`` is intentionally NULL in M4; filled in M10 with the author.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import VatTreatmentEffect, VatTreatmentSide

if TYPE_CHECKING:
    pass


class VatRate(Base):
    """Per-item / per-product VAT rate (e.g. 21%, 9%, 0%)."""

    __tablename__ = "vat_rate"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    percent: Mapped[object] = mapped_column(
        Numeric(6, 3), nullable=False, comment="Tax rate percentage, e.g. 21.000"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VatTreatment(Base):
    """Document-level VAT processing mode.

    System-seeded; UI shows as read-only in M4.  ``report_box`` is NULL until
    M10 where the author and agent co-define the BTW box mapping.
    """

    __tablename__ = "vat_treatment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[VatTreatmentSide] = mapped_column(nullable=False)
    effect: Mapped[VatTreatmentEffect] = mapped_column(nullable=False)
    report_box: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="BTW report box; NULL until M10."
    )
    requires_icp: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deductible: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="Input VAT deductibility hint (purchase side)."
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
