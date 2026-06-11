"""Quote API routes – calculate (M6 step 1).

Endpoints:
  POST   /api/v1/quotes/calculate      – pricing preview (no persistence)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.invoice import InvoiceCalculationRead
from jai.schemas.quote import QuoteCalculationRead, QuoteCalculationRequest
from jai.services.pricing import calculate_quote

router = APIRouter(prefix="/api/v1", tags=["quotes"])


def _owner_only(user: User) -> None:
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required."
        )


def _require_company_id(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


# ---------------------------------------------------------------------------
# Pricing preview (step 1)
# ---------------------------------------------------------------------------


@router.post(
    "/quotes/calculate",
    response_model=QuoteCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_quote_endpoint(
    body: QuoteCalculationRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceCalculationRead:
    """Preview quote pricing without persisting (red-line 1)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await calculate_quote(session, company_id=company_id, request=body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
