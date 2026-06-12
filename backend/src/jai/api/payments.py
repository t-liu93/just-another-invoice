"""Payment API routes – record, list, read, edit, and delete payments (M7).

Endpoints:
  POST   /api/v1/invoices/{invoice_id}/payments  – record a payment → 201
  GET    /api/v1/invoices/{invoice_id}/payments  – list payments for invoice → 200
  GET    /api/v1/payments/{id}                   – get a single payment → 200
  PUT    /api/v1/payments/{id}                   – edit a payment → 200
  DELETE /api/v1/payments/{id}                   – delete a payment → 200 (aggregate body)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.payment import InvoicePaymentsResponse, PaymentInput, PaymentRead
from jai.services.payment import (
    delete_payment,
    get_payment,
    list_invoice_payments,
    record_payment,
    update_payment,
)

router = APIRouter(prefix="/api/v1", tags=["payments"])


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
# Record a payment
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=InvoicePaymentsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment_endpoint(
    invoice_id: uuid.UUID,
    body: PaymentInput,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoicePaymentsResponse:
    """Record one payment for an invoice and return the updated aggregate."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await record_payment(
            session,
            invoice_id=invoice_id,
            company_id=company_id,
            body=body,
            creator_id=user.id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# List payments for an invoice
# ---------------------------------------------------------------------------


@router.get(
    "/invoices/{invoice_id}/payments",
    response_model=InvoicePaymentsResponse,
)
async def list_invoice_payments_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoicePaymentsResponse:
    """Return all payments for an invoice plus current payment state."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await list_invoice_payments(session, invoice_id, company_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Get a single payment
# ---------------------------------------------------------------------------


@router.get("/payments/{payment_id}", response_model=PaymentRead)
async def get_payment_endpoint(
    payment_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentRead:
    """Fetch a single payment by id (scoped to caller's company)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    p = await get_payment(session, payment_id, company_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found."
        )
    return p


# ---------------------------------------------------------------------------
# Edit a payment (step 2)
# ---------------------------------------------------------------------------


@router.put("/payments/{payment_id}", response_model=InvoicePaymentsResponse)
async def update_payment_endpoint(
    payment_id: uuid.UUID,
    body: PaymentInput,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoicePaymentsResponse:
    """Edit a payment and return the updated invoice aggregate."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await update_payment(session, payment_id, company_id, body)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Delete a payment (step 2)
# ---------------------------------------------------------------------------


@router.delete("/payments/{payment_id}", response_model=InvoicePaymentsResponse)
async def delete_payment_endpoint(
    payment_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoicePaymentsResponse:
    """Delete a payment and return the updated invoice aggregate.

    Returns 200 with the aggregate body (not 204) so the front-end gets the
    new due_amount / paid_status / status in a single round-trip.
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await delete_payment(session, payment_id, company_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
