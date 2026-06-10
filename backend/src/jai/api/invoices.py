"""Invoice API routes – calculate + CRUD + status + product options (M5 steps 1 & 3).

Endpoints:
  POST   /api/v1/invoices/calculate      – pricing preview (no persistence)
  GET    /api/v1/invoices                – paginated list with filters
  POST   /api/v1/invoices                – create invoice (allocate number)
  GET    /api/v1/invoices/{id}           – fetch single invoice
  PUT    /api/v1/invoices/{id}           – update invoice (preserve number)
  DELETE /api/v1/invoices/{id}           – delete invoice
  POST   /api/v1/invoices/{id}/status    – transition lifecycle status
  GET    /api/v1/invoice-product-options – customer-safe product search
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.invoice import (
    InvoiceCalculationRead,
    InvoiceCalculationRequest,
    InvoiceListResponse,
    InvoiceRead,
    InvoiceStatusWrite,
    InvoiceWrite,
    ProductInvoiceOptionListResponse,
)
from jai.services.invoice import (
    create_invoice,
    delete_invoice,
    get_invoice,
    list_invoice_product_options,
    list_invoices,
    transition_status,
    update_invoice,
)
from jai.services.pricing import calculate_invoice

router = APIRouter(prefix="/api/v1", tags=["invoices"])


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


async def _require_company(
    user: User,
    session: AsyncSession,
) -> tuple[uuid.UUID, str]:
    """Return (company_id, base_currency); raises 400 if no company is set."""
    from sqlalchemy import select

    from jai.models.company import Company

    company_id = _require_company_id(user)
    stmt = select(Company).where(Company.id == company_id)
    result = await session.execute(stmt)
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company not found.",
        )
    return company_id, company.base_currency


# ---------------------------------------------------------------------------
# Pricing preview (step 1)
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
    """Preview invoice pricing without persisting (red-line 1)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await calculate_invoice(session, company_id=company_id, request=body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Invoice CRUD (step 3)
# ---------------------------------------------------------------------------


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_invoices_endpoint(
    q: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    paid_status_filter: str | None = Query(default=None, alias="paid_status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="invoice_date",
        pattern="^(invoice_date|created_at|invoice_number)$",
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_invoices(
        session,
        company_id,
        q=q,
        customer_id=customer_id,
        status=status_filter,
        paid_status=paid_status_filter,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.post(
    "/invoices",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_invoice_endpoint(
    body: InvoiceWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    """Create invoice: allocates number, calculates amounts, persists."""
    _owner_only(user)
    company_id, base_currency = await _require_company(user, session)
    try:
        return await create_invoice(
            session,
            body,
            company_id=company_id,
            company_currency=base_currency,
            creator_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/invoices/{invoice_id}", response_model=InvoiceRead)
async def get_invoice_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    inv = await get_invoice(session, invoice_id, company_id)
    if inv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    return inv


@router.put("/invoices/{invoice_id}", response_model=InvoiceRead)
async def update_invoice_endpoint(
    invoice_id: uuid.UUID,
    body: InvoiceWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    company_id, base_currency = await _require_company(user, session)
    try:
        inv = await update_invoice(
            session,
            invoice_id,
            body,
            company_id=company_id,
            company_currency=base_currency,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if inv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    return inv


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        deleted = await delete_invoice(session, invoice_id, company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )


@router.post("/invoices/{invoice_id}/status", response_model=InvoiceRead)
async def transition_status_endpoint(
    invoice_id: uuid.UUID,
    body: InvoiceStatusWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        inv = await transition_status(session, invoice_id, company_id, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if inv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    return inv


# ---------------------------------------------------------------------------
# Invoice product options (step 3) – customer-safe projection
# ---------------------------------------------------------------------------


@router.get(
    "/invoice-product-options",
    response_model=ProductInvoiceOptionListResponse,
)
async def list_invoice_product_options_endpoint(
    q: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductInvoiceOptionListResponse:
    """Customer-safe product search for invoice line auto-fill.

    Returns only id/name/unit/default_vat_rate_id – never cost/margin/supplier.
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_invoice_product_options(session, company_id, q=q, limit=limit)
