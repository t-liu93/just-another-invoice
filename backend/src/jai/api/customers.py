"""Customer API routes – CRUD + paginated list (M3 step 1).

Endpoints:
  - ``GET    /api/v1/customers``      – list (search + pagination).
  - ``POST   /api/v1/customers``      – create.
  - ``GET    /api/v1/customers/{id}``  – read single.
  - ``PUT    /api/v1/customers/{id}``  – update.
  - ``DELETE /api/v1/customers/{id}``  – delete (cascade handled by DB FK).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.customer import CustomerListResponse, CustomerRead, CustomerWrite
from jai.services.customer import (
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    update_customer,
)

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


def _owner_only(user: User) -> None:
    """Ensure the authenticated user has the owner role."""
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required.",
        )


def _require_company_id(user: User) -> uuid.UUID:
    """Return the user's ``company_id``, raising 400 if not set."""
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


# ---------------------------------------------------------------------------
# CRUD + List
# ---------------------------------------------------------------------------


@router.get("", response_model=CustomerListResponse)
async def list_customers_endpoint(
    q: str | None = Query(None, description="Search name/email/company_name."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort_by: Literal["name", "created_at"] = Query(
        "created_at",
        description="Sort field: 'name' (A→Z) or 'created_at' (newest first).",
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CustomerListResponse:
    """Paginated customer list with optional search."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_customers(
        session, company_id, q=q, limit=limit, offset=offset, sort_by=sort_by
    )


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer_endpoint(
    body: CustomerWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CustomerRead:
    """Create a new customer."""
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await create_customer(session, body, company_id)
    await session.commit()
    return result


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer_endpoint(
    customer_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CustomerRead:
    """Return a single customer by id."""
    _owner_only(user)
    company_id = _require_company_id(user)
    customer = await get_customer(session, customer_id, company_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    from jai.services.customer import _to_read

    return _to_read(customer)


@router.put("/{customer_id}", response_model=CustomerRead)
async def update_customer_endpoint(
    customer_id: uuid.UUID,
    body: CustomerWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> CustomerRead:
    """Update an existing customer."""
    _owner_only(user)
    company_id = _require_company_id(user)
    customer = await get_customer(session, customer_id, company_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    result = await update_customer(session, customer, body)
    await session.commit()
    return result


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_endpoint(
    customer_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a customer. Addresses are cascade-deleted by DB FK."""
    _owner_only(user)
    company_id = _require_company_id(user)
    customer = await get_customer(session, customer_id, company_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    try:
        await delete_customer(session, customer)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete customer: it is referenced by one or more invoices.",
        ) from exc
