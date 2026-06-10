"""Invoice API routes – calculate endpoint (M5 step 1).

Endpoints:
  POST /api/v1/invoices/calculate – pricing preview (no persistence)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.invoice import InvoiceCalculationRead, InvoiceCalculationRequest
from jai.services.pricing import calculate_invoice

router = APIRouter(prefix="/api/v1", tags=["invoices"])


def _owner_only(user: User) -> None:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required.")


def _require_company_id(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


# ---------------------------------------------------------------------------
# Pricing preview
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/calculate",
    response_model=InvoiceCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_invoice_endpoint(
    body: InvoiceCalculationRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceCalculationRead:
    """Preview invoice pricing without persisting.

    Reads customer / company / VAT rate / treatment from DB; delegates
    calculation to ``services.pricing`` (red-line 1).
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await calculate_invoice(session, company_id=company_id, request=body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
