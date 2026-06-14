"""PDF download endpoints – M9 step 1 / step 2 / step 3.

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
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User

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

    from jai.services.pdf import build_content_disposition, render_invoice_pdf

    pdf_bytes, filename = await render_invoice_pdf(
        session=session,
        invoice_id=invoice_id,
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
