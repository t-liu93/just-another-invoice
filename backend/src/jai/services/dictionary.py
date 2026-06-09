"""Dictionary service – CRUD + seeding for payment_method / expense_category / unit (M4 step 2).

Public API
----------
- ``list_payment_methods`` / ``get_payment_method`` / ``create_payment_method``
  / ``update_payment_method`` / ``delete_payment_method``

- ``list_expense_categories`` / ``get_expense_category`` / ``create_expense_category``
  / ``update_expense_category`` / ``delete_expense_category``

- ``list_units`` / ``get_unit`` / ``create_unit`` / ``update_unit`` / ``delete_unit``

- ``seed_for_company``  – insert default entries for a new company (idempotent).

Design notes
------------
- All queries filter by ``company_id``; front-end never sends it.
- UNIQUE(company_id, name) on payment_method and expense_category,
  UNIQUE(company_id, code) on unit; duplicate raises ValueError → HTTP 409.
- No money calculations here (red-line 1).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models.dictionary import ExpenseCategory, PaymentMethod, Unit
from jai.schemas.dictionary import (
    ExpenseCategoryListResponse,
    ExpenseCategoryRead,
    ExpenseCategoryWrite,
    PaymentMethodListResponse,
    PaymentMethodRead,
    PaymentMethodWrite,
    UnitListResponse,
    UnitRead,
    UnitWrite,
)

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_PAYMENT_METHOD_SEEDS: list[str] = ["Bank transfer", "Cash"]

_EXPENSE_CATEGORY_SEEDS: list[dict[str, object]] = [
    {"name": "Tool", "default_deductible": True},
    {"name": "Consumable", "default_deductible": True},
    {"name": "Product", "default_deductible": True},
    {"name": "Regular", "default_deductible": True},
    {"name": "Travel", "default_deductible": None},
]

_UNIT_SEEDS: list[dict[str, object]] = [
    {"code": "pcs", "name": "Piece"},
    {"code": "hr", "name": "Hour"},
    {"code": "m", "name": "Meter"},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pm_to_read(obj: PaymentMethod) -> PaymentMethodRead:
    return PaymentMethodRead(
        id=obj.id,
        name=obj.name,
        active=obj.active,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _ec_to_read(obj: ExpenseCategory) -> ExpenseCategoryRead:
    return ExpenseCategoryRead(
        id=obj.id,
        name=obj.name,
        description=obj.description,
        default_deductible=obj.default_deductible,
        active=obj.active,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _unit_to_read(obj: Unit) -> UnitRead:
    return UnitRead(
        id=obj.id,
        code=obj.code,
        name=obj.name,
        active=obj.active,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


async def _check_pm_name_unique(
    session: AsyncSession,
    company_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(PaymentMethod).where(
        PaymentMethod.company_id == company_id,
        PaymentMethod.name == name,
    )
    if exclude_id is not None:
        stmt = stmt.where(PaymentMethod.id != exclude_id)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"Payment method '{name}' already exists for this company.")


async def _check_ec_name_unique(
    session: AsyncSession,
    company_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(ExpenseCategory).where(
        ExpenseCategory.company_id == company_id,
        ExpenseCategory.name == name,
    )
    if exclude_id is not None:
        stmt = stmt.where(ExpenseCategory.id != exclude_id)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"Expense category '{name}' already exists for this company.")


async def _check_unit_code_unique(
    session: AsyncSession,
    company_id: uuid.UUID,
    code: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(Unit).where(
        Unit.company_id == company_id,
        Unit.code == code,
    )
    if exclude_id is not None:
        stmt = stmt.where(Unit.id != exclude_id)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"Unit code '{code}' already exists for this company.")


# ---------------------------------------------------------------------------
# Payment Method CRUD
# ---------------------------------------------------------------------------


async def list_payment_methods(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> PaymentMethodListResponse:
    stmt = (
        select(PaymentMethod)
        .where(PaymentMethod.company_id == company_id)
        .order_by(PaymentMethod.name)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return PaymentMethodListResponse(items=[_pm_to_read(r) for r in rows])


async def get_payment_method(
    session: AsyncSession,
    pm_id: uuid.UUID,
    company_id: uuid.UUID,
) -> PaymentMethod | None:
    stmt = select(PaymentMethod).where(
        PaymentMethod.id == pm_id,
        PaymentMethod.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_payment_method(
    session: AsyncSession,
    data: PaymentMethodWrite,
    company_id: uuid.UUID,
) -> PaymentMethodRead:
    await _check_pm_name_unique(session, company_id, data.name)
    obj = PaymentMethod(company_id=company_id, name=data.name, active=data.active)
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Payment method '{data.name}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _pm_to_read(obj)


async def update_payment_method(
    session: AsyncSession,
    obj: PaymentMethod,
    data: PaymentMethodWrite,
) -> PaymentMethodRead:
    await _check_pm_name_unique(session, obj.company_id, data.name, exclude_id=obj.id)
    obj.name = data.name
    obj.active = data.active
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Payment method '{data.name}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _pm_to_read(obj)


async def delete_payment_method(session: AsyncSession, obj: PaymentMethod) -> None:
    await session.delete(obj)
    await session.flush()


# ---------------------------------------------------------------------------
# Expense Category CRUD
# ---------------------------------------------------------------------------


async def list_expense_categories(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> ExpenseCategoryListResponse:
    stmt = (
        select(ExpenseCategory)
        .where(ExpenseCategory.company_id == company_id)
        .order_by(ExpenseCategory.name)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return ExpenseCategoryListResponse(items=[_ec_to_read(r) for r in rows])


async def get_expense_category(
    session: AsyncSession,
    ec_id: uuid.UUID,
    company_id: uuid.UUID,
) -> ExpenseCategory | None:
    stmt = select(ExpenseCategory).where(
        ExpenseCategory.id == ec_id,
        ExpenseCategory.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_expense_category(
    session: AsyncSession,
    data: ExpenseCategoryWrite,
    company_id: uuid.UUID,
) -> ExpenseCategoryRead:
    await _check_ec_name_unique(session, company_id, data.name)
    obj = ExpenseCategory(
        company_id=company_id,
        name=data.name,
        description=data.description,
        default_deductible=data.default_deductible,
        active=data.active,
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Expense category '{data.name}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _ec_to_read(obj)


async def update_expense_category(
    session: AsyncSession,
    obj: ExpenseCategory,
    data: ExpenseCategoryWrite,
) -> ExpenseCategoryRead:
    await _check_ec_name_unique(session, obj.company_id, data.name, exclude_id=obj.id)
    obj.name = data.name
    obj.description = data.description
    obj.default_deductible = data.default_deductible
    obj.active = data.active
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Expense category '{data.name}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _ec_to_read(obj)


async def delete_expense_category(session: AsyncSession, obj: ExpenseCategory) -> None:
    await session.delete(obj)
    await session.flush()


# ---------------------------------------------------------------------------
# Unit CRUD
# ---------------------------------------------------------------------------


async def list_units(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> UnitListResponse:
    stmt = (
        select(Unit)
        .where(Unit.company_id == company_id)
        .order_by(Unit.name)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return UnitListResponse(items=[_unit_to_read(r) for r in rows])


async def get_unit(
    session: AsyncSession,
    unit_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Unit | None:
    stmt = select(Unit).where(
        Unit.id == unit_id,
        Unit.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_unit(
    session: AsyncSession,
    data: UnitWrite,
    company_id: uuid.UUID,
) -> UnitRead:
    await _check_unit_code_unique(session, company_id, data.code)
    obj = Unit(
        company_id=company_id,
        code=data.code,
        name=data.name,
        active=data.active,
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Unit code '{data.code}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _unit_to_read(obj)


async def update_unit(
    session: AsyncSession,
    obj: Unit,
    data: UnitWrite,
) -> UnitRead:
    await _check_unit_code_unique(session, obj.company_id, data.code, exclude_id=obj.id)
    obj.code = data.code
    obj.name = data.name
    obj.active = data.active
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Unit code '{data.code}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _unit_to_read(obj)


async def delete_unit(session: AsyncSession, obj: Unit) -> None:
    await session.delete(obj)
    await session.flush()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def seed_for_company(session: AsyncSession, company_id: uuid.UUID) -> None:
    """Insert default payment methods, expense categories, and units for *company_id*.

    Idempotent: skips rows that already exist by name / code.
    Safe to call on every company creation, including the first.
    """
    # Payment methods.
    existing_pm_names_result = await session.execute(
        select(PaymentMethod.name).where(PaymentMethod.company_id == company_id)
    )
    existing_pm_names = set(existing_pm_names_result.scalars().all())
    for name in _PAYMENT_METHOD_SEEDS:
        if name not in existing_pm_names:
            session.add(PaymentMethod(company_id=company_id, name=name, active=True))

    # Expense categories.
    existing_ec_names_result = await session.execute(
        select(ExpenseCategory.name).where(ExpenseCategory.company_id == company_id)
    )
    existing_ec_names = set(existing_ec_names_result.scalars().all())
    for seed in _EXPENSE_CATEGORY_SEEDS:
        if seed["name"] not in existing_ec_names:
            session.add(
                ExpenseCategory(
                    company_id=company_id,
                    name=seed["name"],
                    default_deductible=seed.get("default_deductible"),
                    active=True,
                )
            )

    # Units.
    existing_unit_codes_result = await session.execute(
        select(Unit.code).where(Unit.company_id == company_id)
    )
    existing_unit_codes = set(existing_unit_codes_result.scalars().all())
    for seed in _UNIT_SEEDS:
        if seed["code"] not in existing_unit_codes:
            session.add(
                Unit(
                    company_id=company_id,
                    code=seed["code"],
                    name=seed["name"],
                    active=True,
                )
            )

    await session.flush()
