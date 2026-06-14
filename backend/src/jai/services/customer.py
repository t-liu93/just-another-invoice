"""Customer service – CRUD + list with company-scoped isolation (M3 step 1 + 2).

Public API
----------
- ``list_customers`` – paginated list with optional ``q`` search.
- ``get_customer``   – single customer by id (company-scoped).
- ``create_customer`` – insert a new customer row (injects ``company_id``).
- ``update_customer`` – mutate an existing customer (company-scoped).
- ``delete_customer`` – remove a customer (company-scoped; DB cascade deletes addresses).

Design notes
------------
- All queries filter by ``company_id``; the front-end never sends it.
- ``q`` performs a case-insensitive ILIKE search across ``name``, ``email``,
  and ``company_name`` (OR-ed).
- ``company_id`` is injected on create/update from the authenticated user's
  ``company_id`` (red-line 2: no scope leaks to the front-end).
- Addresses are loaded explicitly via SELECT after flush (avoids async
  lazy-load issues; ``selectin`` on the relationship is a fallback).
"""

from __future__ import annotations

import uuid
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import AddressType
from jai.models.address import Address
from jai.models.customer import Customer
from jai.schemas.address import AddressRead, AddressWrite
from jai.schemas.customer import (
    CustomerListResponse,
    CustomerRead,
    CustomerWrite,
)


def _address_to_read(addr: Address) -> AddressRead:
    """Convert an ``Address`` ORM instance to ``AddressRead``."""
    return AddressRead(
        id=addr.id,
        type=addr.type,
        street=addr.street,
        house_number=addr.house_number,
        house_number_addition=addr.house_number_addition,
        postal_code=addr.postal_code,
        city=addr.city,
        province=addr.province,
        country_code=addr.country_code,
    )


def _to_read(customer: Customer, addresses: list[Address] | None = None) -> CustomerRead:
    """Convert a ``Customer`` ORM instance to ``CustomerRead``.

    Pass *addresses* explicitly so callers control loading; defaults to the
    ORM relationship attribute (loaded via selectin) when None.
    """
    addrs: list[Address] = addresses if addresses is not None else list(customer.addresses)
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
        invoice_prefix=customer.invoice_prefix,
        locale=cast(Literal["en", "zh"] | None, customer.locale),
        extra=customer.extra if customer.extra else {},
        addresses=[_address_to_read(a) for a in addrs],
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


async def _load_addresses(
    session: AsyncSession, customer_id: uuid.UUID
) -> list[Address]:
    """Load all addresses for *customer_id*, ordered by type."""
    stmt = (
        select(Address)
        .where(Address.customer_id == customer_id)
        .order_by(Address.type)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _apply_address_fields(addr: Address, aw: AddressWrite) -> None:
    """Write ``AddressWrite`` fields onto an ``Address`` ORM instance in-place."""
    addr.street = aw.street
    addr.house_number = aw.house_number
    addr.house_number_addition = aw.house_number_addition
    addr.postal_code = aw.postal_code
    addr.city = aw.city
    addr.province = aw.province
    addr.country_code = aw.country_code


async def _upsert_addresses(
    session: AsyncSession,
    customer_id: uuid.UUID,
    address_writes: list[AddressWrite],
) -> None:
    """Diff-and-upsert addresses for a customer.

    - Types present in *address_writes* but missing in DB → INSERT.
    - Types present in both → UPDATE in place.
    - Types in DB but absent from *address_writes* → DELETE (ORM delete).
    """
    existing_rows = await _load_addresses(session, customer_id)
    existing: dict[AddressType, Address] = {a.type: a for a in existing_rows}
    wanted: dict[AddressType, AddressWrite] = {aw.type: aw for aw in address_writes}

    # Delete types no longer wanted.
    for addr_type, addr in existing.items():
        if addr_type not in wanted:
            await session.delete(addr)

    # Insert or update remaining.
    for addr_type, aw in wanted.items():
        if addr_type in existing:
            _apply_address_fields(existing[addr_type], aw)
        else:
            new_addr = Address(customer_id=customer_id, type=addr_type)
            _apply_address_fields(new_addr, aw)
            session.add(new_addr)

    await session.flush()


async def list_customers(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: Literal["name", "created_at"] = "created_at",
) -> CustomerListResponse:
    """Return a paginated, optionally filtered list of customers.

    ``q`` performs ILIKE search on ``name``, ``email``, and ``company_name``.
    ``sort_by`` accepts ``"name"`` (asc) or ``"created_at"`` (desc, default).
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

    # Ordering: name A→Z or newest-first (default).
    order = Customer.name.asc() if sort_by == "name" else Customer.created_at.desc()

    # Paginated rows.
    rows_result = await session.execute(
        base.order_by(order).limit(limit).offset(offset)
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
    """Insert a new customer row with the given *company_id*, plus addresses."""
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
        invoice_prefix=data.invoice_prefix,
        locale=data.locale,
        extra=data.extra,
    )
    session.add(customer)
    await session.flush()
    await session.refresh(customer)

    if data.addresses:
        await _upsert_addresses(session, customer.id, data.addresses)

    addresses = await _load_addresses(session, customer.id)
    return _to_read(customer, addresses)


async def update_customer(
    session: AsyncSession,
    customer: Customer,
    data: CustomerWrite,
) -> CustomerRead:
    """Mutate an existing customer's scalar fields and diff addresses."""
    customer.name = data.name
    customer.contact_name = data.contact_name
    customer.company_name = data.company_name
    customer.email = data.email
    customer.phone = data.phone
    customer.website = data.website
    customer.vat_id = data.vat_id
    customer.currency = data.currency
    customer.invoice_prefix = data.invoice_prefix
    customer.locale = data.locale
    customer.extra = data.extra
    await session.flush()
    await session.refresh(customer)

    await _upsert_addresses(session, customer.id, data.addresses)

    addresses = await _load_addresses(session, customer.id)
    return _to_read(customer, addresses)


async def delete_customer(
    session: AsyncSession,
    customer: Customer,
) -> None:
    """Delete a customer row.  Addresses are cascade-deleted by DB FK."""
    await session.delete(customer)
    await session.flush()
