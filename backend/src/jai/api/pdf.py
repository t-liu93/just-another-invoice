"""PDF download endpoints – M9 step 1 / step 2 / step 3 / step 4.

Endpoints
---------
GET /api/v1/invoices/{id}/pdf?locale=en|zh
    Download an invoice as a PDF file.
    - locale is optional; when omitted the D2 resolution chain is used:
        customer.locale → company default → "en".
    - Returns application/pdf with Content-Disposition: attachment.
    - company_id is injected from the authenticated user (red-line 2).
    - Cross-company or missing invoice → 404.
    - owner-only (same guard as api/invoices.py).

GET /api/v1/quotes/{id}/pdf?locale=en|zh
    Download a quote as a PDF file.
    - Same locale resolution, same auth guards.
    - No due_amount / paid_status rendered (quotes have no payment dimension).
    - No cost/margin fields rendered (client-facing zero-leakage).

GET /api/v1/payments/{id}/receipt-pdf?locale=en|zh
    Download a payment receipt as a PDF file.
    - Single payment → one receipt (D3).
    - Same locale resolution, same auth guards.
    - Receipt is download-only; no email sending (D3).
    - Amounts taken from payment and invoice snapshots, never recalculated.
"""
# ruff: noqa: E501

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import DocumentArtifactReason, InvoiceStatus
from jai.models.user import User
from jai.schemas.artifact import DocumentArtifactListResponse, DocumentArtifactRead

from .invoices import _owner_only, _require_company_id

router = APIRouter(prefix="/api/v1", tags=["pdf"])


@router.get(
    "/invoices/{invoice_id}/pdf",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Invoice PDF download.",
        }
    },
)
async def download_invoice_pdf(
    invoice_id: uuid.UUID,
    locale: Literal["en", "zh"] | None = Query(
        default=None,
        description=(
            "Document language. When omitted the D2 resolution chain is used: "
            "customer.locale → company default → 'en'."
        ),
    ),
    preview: bool = Query(
        default=False,
        description="Render for the in-app preview only; do not retain an artifact.",
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Render and download an invoice as PDF.

    ``locale`` controls the language of static labels (Invoice, Date, Due Date,
    etc.).  User-entered content (names, descriptions, notes) is rendered as-is.

    When ``locale`` is omitted the smart locale-resolution chain (D2) is used:
    customer.locale → company-level default → "en".
    """
    _owner_only(user)
    company_id = _require_company_id(user)

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from jai.models.invoice import Invoice
    from jai.services.artifacts import retain_invoice_artifact
    from jai.services.pdf import build_content_disposition, render_invoice_pdf_artifact

    pdf_bytes, filename, resolved_locale, render_fingerprint = await render_invoice_pdf_artifact(
        session=session,
        invoice_id=invoice_id,
        company_id=company_id,
        locale=locale,
    )
    invoice = await session.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.company_id == company_id)
        .options(selectinload(Invoice.party_snapshot))
    )
    # A draft is a preview-only document.  Issued formal documents retain the
    # exact bytes that this response returns.
    if (
        not preview
        and invoice is not None
        and invoice.status in {InvoiceStatus.SENT, InvoiceStatus.COMPLETED}
    ):
        artifact, _ = await retain_invoice_artifact(
            session, invoice_id=invoice_id, company_id=company_id, pdf_bytes=pdf_bytes,
            render_fingerprint=render_fingerprint, locale=resolved_locale,
            filename=filename, reason=DocumentArtifactReason.DOWNLOAD,
        )
        await session.commit()
        pdf_bytes = artifact.pdf_bytes

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": build_content_disposition(filename),
        },
    )


@router.get("/invoices/{invoice_id}/artifacts", response_model=DocumentArtifactListResponse)
async def list_invoice_artifacts_endpoint(
    invoice_id: uuid.UUID, user: User = Depends(current_mfa_user), session: AsyncSession = Depends(get_session)
) -> DocumentArtifactListResponse:
    _owner_only(user)
    from jai.services.artifacts import list_invoice_artifacts
    rows = await list_invoice_artifacts(session, invoice_id=invoice_id, company_id=_require_company_id(user))
    return DocumentArtifactListResponse(items=[DocumentArtifactRead.model_validate(row) for row in rows])


@router.get("/invoices/{invoice_id}/artifacts/{artifact_id}", response_class=Response)
async def download_invoice_artifact_endpoint(
    invoice_id: uuid.UUID, artifact_id: uuid.UUID, user: User = Depends(current_mfa_user), session: AsyncSession = Depends(get_session)
) -> Response:
    _owner_only(user)
    from jai.services.artifacts import get_invoice_artifact
    from jai.services.pdf import build_content_disposition
    artifact = await get_invoice_artifact(session, invoice_id=invoice_id, artifact_id=artifact_id, company_id=_require_company_id(user))
    return Response(content=artifact.pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": build_content_disposition(artifact.filename)})


@router.get(
    "/quotes/{quote_id}/pdf",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Quote PDF download.",
        }
    },
)
async def download_quote_pdf(
    quote_id: uuid.UUID,
    locale: Literal["en", "zh"] | None = Query(
        default=None,
        description=(
            "Document language. When omitted the D2 resolution chain is used: "
            "customer.locale → company default → 'en'."
        ),
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Render and download a quote as PDF.

    ``locale`` controls the language of static labels (Quote, Date, Valid Until,
    etc.).  User-entered content (names, descriptions, notes) is rendered as-is.

    When ``locale`` is omitted the smart locale-resolution chain (D2) is used:
    customer.locale → company-level default → "en".

    No cost/margin/estimate data is included in the PDF (client-facing
    zero-leakage guard).  No due_amount / paid_status is rendered (quotes have
    no payment dimension).
    """
    _owner_only(user)
    company_id = _require_company_id(user)

    from jai.services.pdf import build_content_disposition, render_quote_pdf

    pdf_bytes, filename = await render_quote_pdf(
        session=session,
        quote_id=quote_id,
        company_id=company_id,
        locale=locale,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": build_content_disposition(filename),
        },
    )


@router.get(
    "/payments/{payment_id}/receipt-pdf",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Payment receipt PDF download.",
        }
    },
)
async def download_payment_receipt_pdf(
    payment_id: uuid.UUID,
    locale: Literal["en", "zh"] | None = Query(
        default=None,
        description=(
            "Document language. When omitted the D2 resolution chain is used: "
            "customer.locale → company default → 'en'."
        ),
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Render and download a payment receipt as PDF.

    Produces a single-payment receipt (D3).  The receipt shows:
    - Company header + customer billing address.
    - Related invoice number.
    - Payment date, amount, and payment method (from snapshot).
    - Invoice total, amount paid (total), and amount due (from invoice snapshots).

    Amounts come exclusively from DB snapshots – never recalculated (red-line 1).
    Receipt is download-only; no email sending (D3).

    ``locale`` controls the language of static labels.  When omitted the D2
    resolution chain is used: customer.locale → company-level default → 'en'.
    """
    _owner_only(user)
    company_id = _require_company_id(user)

    from jai.services.pdf import build_content_disposition, render_payment_receipt_pdf

    pdf_bytes, filename = await render_payment_receipt_pdf(
        session=session,
        payment_id=payment_id,
        company_id=company_id,
        locale=locale,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": build_content_disposition(filename),
        },
    )
