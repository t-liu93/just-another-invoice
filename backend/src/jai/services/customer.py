"""Customer service – CRUD + list with company-scoped isolation (M3 step 1).

Public API
----------
- ``list_customers`` – paginated list with optional ``q`` search.
- ``get_customer``   – single customer by id (company-scoped).
- ``create_customer`` – insert a new customer row (injects ``company_id``).
- ``update_customer`` – mutate an existing customer (company-scoped).
- ``delete_customer`` – remove a customer (company-scoped).

Design notes
------------
- All queries filter by ``company_id``; the front-end never sends it.
- ``q`` performs a case-insensitive ILIKE search across ``name``, ``email``,
  and ``company_name`` (OR-ed).
- ``company_id`` is injected on create/update from the authenticated user's
  ``company_id`` (red-line 2: no scope leaks to the front-end).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models.customer import Customer
from jai.schemas.customer import (
    CustomerListResponse,
    CustomerRead,
    CustomerWrite,
)


def _to_read(customer: Customer) -> CustomerRead:
    """Convert a ``Customer`` ORM instance to ``CustomerRead``."""
    return CustomerRead(
        id=customer.id,
        name=customer.name,
        contact_name=customer.contact_name,
        company_name=customer.company_name,
        email=customer.email,
        phone=customer.phone,
        website=customer.website,
        vat_id=customer.vat_id,
        currency=customer.currency,
        extra=customer.extra if customer.extra else {},
        addresses=[],  # placeholder for step 2
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


async def list_customers(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CustomerListResponse:
    """Return a paginated, optionally filtered list of customers.

    ``q`` performs ILIKE search on ``name``, ``email``, and ``company_name``.
    """
    base = select(Customer).where(Customer.company_id == company_id)
    count_base = select(func.count()).select_from(Customer).where(
        Customer.company_id == company_id
    )

    if q:
        pattern = f"%{q}%"
        ilike_clause = (
            Customer.name.ilike(pattern)
            | Customer.email.ilike(pattern)
            | Customer.company_name.ilike(pattern)
        )
        base = base.where(ilike_clause)
        count_base = count_base.where(ilike_clause)

    # Total count.
    total_result = await session.execute(count_base)
    total = total_result.scalar_one()

    # Paginated rows.
    rows_result = await session.execute(
        base.order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    )
    customers = rows_result.scalars().all()

    return CustomerListResponse(
        items=[_to_read(c) for c in customers],
        total=total,
    )


async def get_customer(
    session: AsyncSession,
    customer_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Customer | None:
    """Return a single customer scoped to *company_id*, or ``None``."""
    stmt = select(Customer).where(
        Customer.id == customer_id,
        Customer.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_customer(
    session: AsyncSession,
    data: CustomerWrite,
    company_id: uuid.UUID,
) -> CustomerRead:
    """Insert a new customer row with the given *company_id*."""
    customer = Customer(
        company_id=company_id,
        name=data.name,
        contact_name=data.contact_name,
        company_name=data.company_name,
        email=data.email,
        phone=data.phone,
        website=data.website,
        vat_id=data.vat_id,
        currency=data.currency,
        extra=data.extra,
    )
    session.add(customer)
    await session.flush()
    await session.refresh(customer)
    return _to_read(customer)


async def update_customer(
    session: AsyncSession,
    customer: Customer,
    data: CustomerWrite,
) -> CustomerRead:
    """Mutate an existing customer's scalar fields."""
    customer.name = data.name
    customer.contact_name = data.contact_name
    customer.company_name = data.company_name
    customer.email = data.email
    customer.phone = data.phone
    customer.website = data.website
    customer.vat_id = data.vat_id
    customer.currency = data.currency
    customer.extra = data.extra
    await session.flush()
    await session.refresh(customer)
    return _to_read(customer)


async def delete_customer(
    session: AsyncSession,
    customer: Customer,
) -> None:
    """Delete a customer row.  Address cascade handled by DB FK."""
    await session.delete(customer)
    await session.flush()
