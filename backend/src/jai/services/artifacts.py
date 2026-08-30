"""Byte-first immutable formal-output retention for M12 Step 9."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from jai.db import set_rls_company
from jai.models._enums import (
    DocumentArtifactKind,
    DocumentArtifactReason,
)
from jai.models.document_artifact import DocumentArtifact
from jai.services.pdf import FORMAL_OUTPUT_PIPELINE_VERSION

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

RENDERER_VERSION = FORMAL_OUTPUT_PIPELINE_VERSION


async def retain_invoice_artifact(
    session: AsyncSession, *, invoice_id: uuid.UUID, company_id: uuid.UUID,
    pdf_bytes: bytes, render_fingerprint: str, locale: str, filename: str,
    reason: DocumentArtifactReason,
) -> tuple[DocumentArtifact, bool]:
    await set_rls_company(session, company_id)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    # Owner, kind and exact bytes are the retention identity. Locale, filename,
    # fingerprint and renderer version document the first retained rendering.
    existing = await session.scalar(select(DocumentArtifact).where(
        DocumentArtifact.invoice_id == invoice_id,
        DocumentArtifact.artifact_kind == DocumentArtifactKind.FORMAL_DOCUMENT,
        DocumentArtifact.company_id == company_id,
        DocumentArtifact.sha256 == digest,
    ))
    if existing is not None:
        return existing, False
    # PDF metadata can make an otherwise identical presentation byte-variant.
    # Keep the first retained bytes canonical within that renderer presentation.
    canonical = await session.scalar(select(DocumentArtifact).where(
        DocumentArtifact.invoice_id == invoice_id,
        DocumentArtifact.artifact_kind == DocumentArtifactKind.FORMAL_DOCUMENT,
        DocumentArtifact.company_id == company_id,
        DocumentArtifact.locale == locale,
        DocumentArtifact.renderer_version == RENDERER_VERSION,
        DocumentArtifact.render_fingerprint == render_fingerprint,
    ))
    if canonical is not None:
        return canonical, False
    artifact = DocumentArtifact(
        company_id=company_id, invoice_id=invoice_id,
        artifact_kind=DocumentArtifactKind.FORMAL_DOCUMENT, pdf_bytes=pdf_bytes,
        sha256=digest, render_fingerprint=render_fingerprint, locale=locale, filename=filename,
        creation_reason=reason, renderer_version=RENDERER_VERSION,
    )
    try:
        async with session.begin_nested():
            session.add(artifact)
            await session.flush()
    except IntegrityError:
        # Concurrent equal downloads are one immutable object, not a duplicate.
        winner = await session.scalar(select(DocumentArtifact).where(
            DocumentArtifact.invoice_id == invoice_id,
            DocumentArtifact.artifact_kind == DocumentArtifactKind.FORMAL_DOCUMENT,
            DocumentArtifact.company_id == company_id,
            DocumentArtifact.sha256 == digest,
        ))
        if winner is None:
            winner = await session.scalar(select(DocumentArtifact).where(
                DocumentArtifact.invoice_id == invoice_id,
                DocumentArtifact.artifact_kind == DocumentArtifactKind.FORMAL_DOCUMENT,
                DocumentArtifact.company_id == company_id,
                DocumentArtifact.locale == locale,
                DocumentArtifact.renderer_version == RENDERER_VERSION,
                DocumentArtifact.render_fingerprint == render_fingerprint,
            ))
        if winner is None:
            raise
        return winner, False
    return artifact, True


async def retain_refund_artifact(
    session: AsyncSession, *, refund_id: uuid.UUID, company_id: uuid.UUID,
    pdf_bytes: bytes, render_fingerprint: str, locale: str, filename: str,
    reason: DocumentArtifactReason,
) -> tuple[DocumentArtifact, bool]:
    await set_rls_company(session, company_id)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    existing = await session.scalar(select(DocumentArtifact).where(
        DocumentArtifact.refund_payment_id == refund_id,
        DocumentArtifact.artifact_kind == DocumentArtifactKind.REFUND_CONFIRMATION,
        DocumentArtifact.company_id == company_id,
        DocumentArtifact.sha256 == digest,
    ))
    if existing is not None:
        return existing, False
    canonical = await session.scalar(select(DocumentArtifact).where(
        DocumentArtifact.refund_payment_id == refund_id,
        DocumentArtifact.artifact_kind == DocumentArtifactKind.REFUND_CONFIRMATION,
        DocumentArtifact.company_id == company_id,
        DocumentArtifact.locale == locale,
        DocumentArtifact.renderer_version == RENDERER_VERSION,
        DocumentArtifact.render_fingerprint == render_fingerprint,
    ))
    if canonical is not None:
        return canonical, False
    artifact = DocumentArtifact(
        company_id=company_id, refund_payment_id=refund_id,
        artifact_kind=DocumentArtifactKind.REFUND_CONFIRMATION, pdf_bytes=pdf_bytes,
        sha256=digest, render_fingerprint=render_fingerprint, locale=locale, filename=filename,
        creation_reason=reason, renderer_version=RENDERER_VERSION,
    )
    try:
        async with session.begin_nested():
            session.add(artifact)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(select(DocumentArtifact).where(
            DocumentArtifact.refund_payment_id == refund_id,
            DocumentArtifact.artifact_kind == DocumentArtifactKind.REFUND_CONFIRMATION,
            DocumentArtifact.company_id == company_id,
            DocumentArtifact.sha256 == digest,
        ))
        if winner is None:
            winner = await session.scalar(select(DocumentArtifact).where(
                DocumentArtifact.refund_payment_id == refund_id,
                DocumentArtifact.artifact_kind == DocumentArtifactKind.REFUND_CONFIRMATION,
                DocumentArtifact.company_id == company_id,
                DocumentArtifact.locale == locale,
                DocumentArtifact.renderer_version == RENDERER_VERSION,
                DocumentArtifact.render_fingerprint == render_fingerprint,
            ))
        if winner is None:
            raise
        return winner, False
    return artifact, True


async def list_invoice_artifacts(session: AsyncSession, *, invoice_id: uuid.UUID, company_id: uuid.UUID) -> list[DocumentArtifact]:
    from jai.models._enums import InvoiceStatus
    from jai.models.invoice import Invoice

    await set_rls_company(session, company_id)
    owner = await session.scalar(select(Invoice.id).where(
        Invoice.id == invoice_id,
        Invoice.company_id == company_id,
        Invoice.status.in_((InvoiceStatus.SENT, InvoiceStatus.COMPLETED)),
    ))
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return list((await session.execute(select(DocumentArtifact).where(
        DocumentArtifact.company_id == company_id, DocumentArtifact.invoice_id == invoice_id,
    ).order_by(DocumentArtifact.created_at.desc()))).scalars().all())


async def list_refund_artifacts(session: AsyncSession, *, refund_id: uuid.UUID, company_id: uuid.UUID) -> list[DocumentArtifact]:
    from jai.models._enums import PaymentDirection
    from jai.models.payment import Payment

    await set_rls_company(session, company_id)
    owner = await session.scalar(select(Payment.id).where(
        Payment.id == refund_id,
        Payment.company_id == company_id,
        Payment.direction == PaymentDirection.REFUND,
    ))
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found.")
    return list((await session.execute(select(DocumentArtifact).where(
        DocumentArtifact.company_id == company_id, DocumentArtifact.refund_payment_id == refund_id,
    ).order_by(DocumentArtifact.created_at.desc()))).scalars().all())


async def get_invoice_artifact(session: AsyncSession, *, invoice_id: uuid.UUID, artifact_id: uuid.UUID, company_id: uuid.UUID) -> DocumentArtifact:
    await set_rls_company(session, company_id)
    artifact = await session.scalar(select(DocumentArtifact).where(
        DocumentArtifact.id == artifact_id, DocumentArtifact.company_id == company_id,
        DocumentArtifact.invoice_id == invoice_id,
    ))
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    return artifact


async def get_refund_artifact(session: AsyncSession, *, refund_id: uuid.UUID, artifact_id: uuid.UUID, company_id: uuid.UUID) -> DocumentArtifact:
    await set_rls_company(session, company_id)
    artifact = await session.scalar(select(DocumentArtifact).where(
        DocumentArtifact.id == artifact_id, DocumentArtifact.company_id == company_id,
        DocumentArtifact.refund_payment_id == refund_id,
    ))
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    return artifact
