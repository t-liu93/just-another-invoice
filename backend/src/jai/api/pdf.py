"""PDF download endpoints – M9 step 1.

Endpoints
---------
GET /api/v1/invoices/{id}/pdf?locale=en|zh
    Download an invoice as a PDF file.
    - locale defaults to "en" (smart default / resolve_document_locale is step 2).
    - Returns application/pdf with Content-Disposition: attachment.
    - company_id is injected from the authenticated user (red-line 2).
    - Cross-company or missing invoice → 404.
    - owner-only (same guard as api/invoices.py).
"""

from __future__ import annotations

import uuid

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
    locale: str = Query(default="en", pattern="^(en|zh)$"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Render and download an invoice as PDF.

    ``locale`` controls the language of static labels (Invoice, Date, Due Date,
    etc.).  User-entered content (names, descriptions, notes) is rendered as-is.

    The smart locale-resolution chain (customer.locale → company default → "en")
    is wired in step 2; for now the caller must supply the locale explicitly,
    defaulting to "en".
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
