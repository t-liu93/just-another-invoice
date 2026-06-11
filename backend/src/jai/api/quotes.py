"""Quote API routes – calculate + CRUD + status + convert + reactivate (M6 steps 1–3).

Endpoints:
  POST   /api/v1/quotes/calculate        – pricing preview (no persistence)
  GET    /api/v1/quotes                  – paginated list
  POST   /api/v1/quotes                  – create (allocates quote number)
  GET    /api/v1/quotes/{id}             – get by id
  PUT    /api/v1/quotes/{id}             – update (ACCEPTED blocks)
  DELETE /api/v1/quotes/{id}             – delete (cascade)
  POST   /api/v1/quotes/{id}/status      – status transition
  POST   /api/v1/quotes/{id}/convert     – convert quote → new DRAFT invoice
  POST   /api/v1/quotes/{id}/reactivate  – EXPIRED → SENT + extend valid_until
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import QuoteStatus
from jai.models.user import User
from jai.schemas.invoice import InvoiceCalculationRead, InvoiceRead
from jai.schemas.quote import (
    QuoteCalculationRead,
    QuoteCalculationRequest,
    QuoteListResponse,
    QuoteReactivateWrite,
    QuoteRead,
    QuoteStatusWrite,
    QuoteWrite,
)
from jai.services import company as company_svc
from jai.services.pricing import calculate_quote
from jai.services.quote import (
    convert_to_invoice,
    create_quote,
    delete_quote,
    get_quote,
    list_quotes,
    reactivate,
    transition_status,
    update_quote,
)

router = APIRouter(prefix="/api/v1", tags=["quotes"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


async def _get_company_currency(
    session: AsyncSession, company_id: uuid.UUID  # noqa: ARG001
) -> str:
    company = await company_svc.get_company(session)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company profile not found.",
        )
    return company.base_currency


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


# ---------------------------------------------------------------------------
# CRUD (step 2)
# ---------------------------------------------------------------------------


@router.get("/quotes", response_model=QuoteListResponse)
async def list_quotes_endpoint(
    q: str | None = Query(default=None, description="Search by number/reference/customer name"),
    customer_id: uuid.UUID | None = Query(default=None),
    status_filter: QuoteStatus | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["quote_date", "created_at", "quote_number"] = Query(default="quote_date"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteListResponse:
    """Return a paginated list of quotes for the current company."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_quotes(
        session,
        company_id,
        q_param=q,
        customer_id=customer_id,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.post("/quotes", response_model=QuoteRead, status_code=status.HTTP_201_CREATED)
async def create_quote_endpoint(
    body: QuoteWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteRead:
    """Create a new quote, allocate a quote number, and return the full record."""
    _owner_only(user)
    company_id = _require_company_id(user)
    company_currency = await _get_company_currency(session, company_id)
    try:
        return await create_quote(
            session,
            body=body,
            company_id=company_id,
            company_currency=company_currency,
            creator_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/quotes/{quote_id}", response_model=QuoteRead)
async def get_quote_endpoint(
    quote_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteRead:
    """Return a quote by ID. Applies read-time expiry check."""
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await get_quote(session, quote_id, company_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")
    return result


@router.put("/quotes/{quote_id}", response_model=QuoteRead)
async def update_quote_endpoint(
    quote_id: uuid.UUID,
    body: QuoteWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteRead:
    """Update a quote. Returns 409 if the quote is ACCEPTED."""
    _owner_only(user)
    company_id = _require_company_id(user)
    company_currency = await _get_company_currency(session, company_id)
    try:
        result = await update_quote(
            session,
            quote_id,
            body=body,
            company_id=company_id,
            company_currency=company_currency,
        )
    except ValueError as exc:
        # ACCEPTED-block raises ValueError; surface it as 409 to match spec
        msg = str(exc)
        if "ACCEPTED" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=msg
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")
    return result


@router.delete("/quotes/{quote_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quote_endpoint(
    quote_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a quote (cascade removes lines/taxes). Number is not recycled."""
    _owner_only(user)
    company_id = _require_company_id(user)
    deleted = await delete_quote(session, quote_id, company_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")


@router.post("/quotes/{quote_id}/status", response_model=QuoteRead)
async def transition_status_endpoint(
    quote_id: uuid.UUID,
    body: QuoteStatusWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteRead:
    """Transition quote status per M6 state machine."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await transition_status(session, quote_id, company_id, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")
    return result


# ---------------------------------------------------------------------------
# Convert / Reactivate (step 3)
# ---------------------------------------------------------------------------


@router.post(
    "/quotes/{quote_id}/convert",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
)
async def convert_quote_endpoint(
    quote_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    """Convert a quote to a new DRAFT/UNPAID invoice.

    Valid source statuses: SENT, ACCEPTED, EXPIRED (soft-expiry rule).
    Returns 409 if the quote was already converted.
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await convert_to_invoice(session, quote_id, company_id, creator_id=user.id)
    except ValueError as exc:
        msg = str(exc)
        if "already been converted" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")
    return result


@router.post("/quotes/{quote_id}/reactivate", response_model=QuoteRead)
async def reactivate_quote_endpoint(
    quote_id: uuid.UUID,
    body: QuoteReactivateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteRead:
    """Reactivate an EXPIRED quote: set status back to SENT and extend valid_until."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await reactivate(session, quote_id, company_id, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found.")
    return result
