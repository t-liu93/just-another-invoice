"""Dictionary API routes – payment_method / expense_category / unit CRUD (M4 step 2).

Endpoints (5 each × 3 resources = 15 total):
  GET/POST  /api/v1/payment-methods        (list / create)
  GET/PUT/DELETE /api/v1/payment-methods/{id}

  GET/POST  /api/v1/expense-categories
  GET/PUT/DELETE /api/v1/expense-categories/{id}

  GET/POST  /api/v1/units
  GET/PUT/DELETE /api/v1/units/{id}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
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
from jai.services.dictionary import (
    _ec_to_read,
    _pm_to_read,
    _unit_to_read,
    create_expense_category,
    create_payment_method,
    create_unit,
    delete_expense_category,
    delete_payment_method,
    delete_unit,
    get_expense_category,
    get_payment_method,
    get_unit,
    list_expense_categories,
    list_payment_methods,
    list_units,
    update_expense_category,
    update_payment_method,
    update_unit,
)

router = APIRouter(prefix="/api/v1", tags=["dictionaries"])


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
# Payment Methods
# ---------------------------------------------------------------------------


@router.get("/payment-methods", response_model=PaymentMethodListResponse)
async def list_payment_methods_endpoint(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentMethodListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_payment_methods(session, company_id)


@router.post(
    "/payment-methods",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_method_endpoint(
    body: PaymentMethodWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentMethodRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_payment_method(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.get("/payment-methods/{pm_id}", response_model=PaymentMethodRead)
async def get_payment_method_endpoint(
    pm_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentMethodRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_payment_method(session, pm_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment method not found."
        )
    return _pm_to_read(obj)


@router.put("/payment-methods/{pm_id}", response_model=PaymentMethodRead)
async def update_payment_method_endpoint(
    pm_id: uuid.UUID,
    body: PaymentMethodWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentMethodRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_payment_method(session, pm_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment method not found."
        )
    try:
        result = await update_payment_method(session, obj, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.delete("/payment-methods/{pm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payment_method_endpoint(
    pm_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_payment_method(session, pm_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment method not found."
        )
    await delete_payment_method(session, obj)
    await session.commit()


# ---------------------------------------------------------------------------
# Expense Categories
# ---------------------------------------------------------------------------


@router.get("/expense-categories", response_model=ExpenseCategoryListResponse)
async def list_expense_categories_endpoint(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ExpenseCategoryListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_expense_categories(session, company_id)


@router.post(
    "/expense-categories",
    response_model=ExpenseCategoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_expense_category_endpoint(
    body: ExpenseCategoryWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ExpenseCategoryRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_expense_category(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.get("/expense-categories/{ec_id}", response_model=ExpenseCategoryRead)
async def get_expense_category_endpoint(
    ec_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ExpenseCategoryRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_expense_category(session, ec_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense category not found."
        )
    return _ec_to_read(obj)


@router.put("/expense-categories/{ec_id}", response_model=ExpenseCategoryRead)
async def update_expense_category_endpoint(
    ec_id: uuid.UUID,
    body: ExpenseCategoryWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ExpenseCategoryRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_expense_category(session, ec_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense category not found."
        )
    try:
        result = await update_expense_category(session, obj, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.delete("/expense-categories/{ec_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense_category_endpoint(
    ec_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_expense_category(session, ec_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Expense category not found."
        )
    await delete_expense_category(session, obj)
    await session.commit()


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@router.get("/units", response_model=UnitListResponse)
async def list_units_endpoint(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> UnitListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_units(session, company_id)


@router.post(
    "/units",
    response_model=UnitRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_unit_endpoint(
    body: UnitWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> UnitRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_unit(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.get("/units/{unit_id}", response_model=UnitRead)
async def get_unit_endpoint(
    unit_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> UnitRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_unit(session, unit_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found."
        )
    return _unit_to_read(obj)


@router.put("/units/{unit_id}", response_model=UnitRead)
async def update_unit_endpoint(
    unit_id: uuid.UUID,
    body: UnitWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> UnitRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_unit(session, unit_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found."
        )
    try:
        result = await update_unit(session, obj, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit_endpoint(
    unit_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_unit(session, unit_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found."
        )
    await delete_unit(session, obj)
    await session.commit()
