"""Immutable exact PDF artifacts retained for M12 formal output."""
# ruff: noqa: E501

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import DocumentArtifactKind, DocumentArtifactReason


class DocumentArtifact(Base):
    """The exact bytes downloaded or successfully attached to an email.

    There is deliberately no mutable storage path: the database row is the
    immutable retention object and ``sha256`` is computed from ``pdf_bytes``.
    Exactly one owner is present, enforced by the additive database migration.
    """

    __tablename__ = "document_artifact"
    __table_args__ = (
        UniqueConstraint(
            "invoice_id", "artifact_kind", "sha256",
            name="uq_document_artifact_invoice_hash",
        ),
        UniqueConstraint(
            "refund_payment_id", "artifact_kind", "sha256",
            name="uq_document_artifact_refund_hash",
        ),
        # Renderer bytes can contain nondeterministic PDF metadata.  This
        # canonicalizes one presentation without weakening exact-byte SHA
        # identity, which intentionally ignores locale/version.
        UniqueConstraint(
            "invoice_id", "artifact_kind", "locale", "renderer_version", "render_fingerprint",
            name="uq_document_artifact_invoice_render",
        ),
        UniqueConstraint(
            "refund_payment_id", "artifact_kind", "locale", "renderer_version", "render_fingerprint",
            name="uq_document_artifact_refund_render",
        ),
        Index("ix_document_artifact_company_created", "company_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice.id", ondelete="CASCADE"), nullable=True, index=True
    )
    refund_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment.id", ondelete="CASCADE"), nullable=True, index=True
    )
    artifact_kind: Mapped[DocumentArtifactKind] = mapped_column(nullable=False)
    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    render_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(String(5), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    creation_reason: Mapped[DocumentArtifactReason] = mapped_column(nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
