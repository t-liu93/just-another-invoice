"""Byte-first immutable formal-output retention for M12 Step 9."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from typing import TYPE_CHECKING

import pypdfium2 as pdfium
from fastapi import HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from jai.db import set_rls_company
from jai.models._enums import (
    DocumentArtifactKind,
    DocumentArtifactReason,
)
from jai.models.document_artifact import DocumentArtifact
from jai.models.invoice import Invoice
from jai.services.pdf import FORMAL_OUTPUT_PIPELINE_VERSION

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

RENDERER_VERSION = FORMAL_OUTPUT_PIPELINE_VERSION
EXTERNAL_UPLOAD_RENDERER_VERSION = "external-upload-v1"


class ArtifactFileValidationError(ValueError):
    """A stable, non-parser-leaking failure for a proposed artifact upload."""


_INVALID_ARTIFACT_FILE_MESSAGE = "The file must be a valid PDF within the configured size limit."
_UNSAFE_UPLOAD_FILENAME = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def normalize_uploaded_artifact_filename(filename: str) -> str:
    """Return safe display metadata for an opaque uploaded PDF.

    The name never becomes a path and is only later used in attachment/header
    metadata.  Keep a useful Unicode name where possible; the response header
    builder supplies its independent RFC 6266 ASCII fallback.
    """
    name = unicodedata.normalize("NFKC", filename).replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_UPLOAD_FILENAME.sub("", name).strip().strip(".")
    if not name:
        return "uploaded-document.pdf"
    return name[:255]


async def ensure_invoice_artifact_upload_eligible(
    session: AsyncSession, *, invoice_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    """Check the M13 upload boundary before consuming a request body.

    This is deliberately read-only and deliberately does *not* lock the
    parent.  Step 2 must acquire the normal formal-output parent lock and
    repeat the zero-artifact check in its write transaction.  ``invoice`` is
    not an RLS-protected table in the current deployment posture, so its
    ownership check belongs at this centralized service boundary.  The RLS
    context still protects the artifact lookup below.
    """
    from jai.models._enums import InvoiceStatus

    await set_rls_company(session, company_id)
    invoice = await session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == company_id)
    )
    if invoice is None or invoice.status not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ARTIFACT_UPLOAD_NOT_FOUND",
                "message": "Invoice not found.",
            },
        )

    has_artifact = await session.scalar(
        select(exists().where(DocumentArtifact.invoice_id == invoice_id))
    )
    if has_artifact:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ARTIFACT_ALREADY_EXISTS",
                "message": "An artifact already exists for this invoice.",
            },
        )


def validate_artifact_pdf(content: bytes, mime_type: str, max_bytes: int) -> int:
    """Validate exact uploaded PDF bytes and return their page count.

    This boundary intentionally accepts bytes rather than a filename or a
    filesystem path.  It performs no rewriting, sanitising, or persistence;
    callers may retain the original ``content`` only after this check passes.
    """
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if (
        normalized_mime != "application/pdf"
        or len(content) > max_bytes
        or not content.startswith(b"%PDF")
    ):
        raise ArtifactFileValidationError(_INVALID_ARTIFACT_FILE_MESSAGE)

    try:
        document = pdfium.PdfDocument(content)
        try:
            page_count = len(document)
        finally:
            document.close()
    except Exception as exc:
        raise ArtifactFileValidationError(_INVALID_ARTIFACT_FILE_MESSAGE) from exc

    if page_count < 1:
        raise ArtifactFileValidationError(_INVALID_ARTIFACT_FILE_MESSAGE)
    return page_count


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
        DocumentArtifact.render_fingerprint == render_fingerprint,
        # An original historical upload is canonical for its recorded formal
        # presentation even though it intentionally has an external marker.
        # Generated documents remain version-scoped as they were in M12.
        (
            (DocumentArtifact.renderer_version == RENDERER_VERSION)
            | (DocumentArtifact.creation_reason == DocumentArtifactReason.UPLOAD)
        ),
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
                DocumentArtifact.render_fingerprint == render_fingerprint,
                (
                    (DocumentArtifact.renderer_version == RENDERER_VERSION)
                    | (DocumentArtifact.creation_reason == DocumentArtifactReason.UPLOAD)
                ),
            ))
        if winner is None:
            raise
        return winner, False
    return artifact, True


async def create_uploaded_invoice_artifact(
    session: AsyncSession,
    *,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    pdf_bytes: bytes,
    original_filename: str,
) -> DocumentArtifact:
    """Persist the one permitted historical original under the invoice lock.

    Rendering is deliberately invoked only after the exclusive parent lock:
    it follows the M12 formal-output preparation path for locale/fingerprint
    and serializes against Download/Send's shared parent renderer lock.
    """
    from jai.models._enums import InvoiceStatus
    from jai.services.pdf import render_invoice_pdf_artifact

    await set_rls_company(session, company_id)
    invoice = await session.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.company_id == company_id)
        .with_for_update()
    )
    if invoice is None or invoice.status not in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ARTIFACT_UPLOAD_NOT_FOUND", "message": "Invoice not found."},
        )
    has_artifact = await session.scalar(
        select(exists().where(DocumentArtifact.invoice_id == invoice_id))
    )
    if has_artifact:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ARTIFACT_ALREADY_EXISTS",
                "message": "An artifact already exists for this invoice.",
            },
        )

    # Do not create a parallel fingerprint calculation or use generated bytes.
    _, _, locale, render_fingerprint = await render_invoice_pdf_artifact(
        session, invoice_id, company_id
    )
    artifact = DocumentArtifact(
        company_id=company_id,
        invoice_id=invoice_id,
        artifact_kind=DocumentArtifactKind.FORMAL_DOCUMENT,
        pdf_bytes=pdf_bytes,
        sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        render_fingerprint=render_fingerprint,
        locale=locale,
        filename=normalize_uploaded_artifact_filename(original_filename),
        creation_reason=DocumentArtifactReason.UPLOAD,
        renderer_version=EXTERNAL_UPLOAD_RENDERER_VERSION,
    )
    session.add(artifact)
    await session.flush()
    return artifact


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
