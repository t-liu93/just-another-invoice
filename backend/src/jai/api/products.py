"""Product catalogue API routes – product_category + product CRUD (M4 step 3).

Endpoints:
  GET/POST  /api/v1/product-categories
  GET/PUT/DELETE /api/v1/product-categories/{id}

  GET       /api/v1/products  (q, category_id, limit, offset, sort_by)
  POST      /api/v1/products
  GET/PUT/DELETE /api/v1/products/{id}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.product import (
    ProductCategoryListResponse,
    ProductCategoryRead,
    ProductCategoryWrite,
    ProductListResponse,
    ProductRead,
    ProductWrite,
)
from jai.services.product import (
    _cat_to_read,
    _product_to_read,
    create_product,
    create_product_category,
    delete_product,
    delete_product_category,
    get_product,
    get_product_category,
    list_product_categories,
    list_products,
    update_product,
    update_product_category,
)

router = APIRouter(prefix="/api/v1", tags=["products"])


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
# Product Categories
# ---------------------------------------------------------------------------


@router.get("/product-categories", response_model=ProductCategoryListResponse)
async def list_product_categories_endpoint(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductCategoryListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_product_categories(session, company_id)


@router.post(
    "/product-categories",
    response_model=ProductCategoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_product_category_endpoint(
    body: ProductCategoryWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductCategoryRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_product_category(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.get("/product-categories/{cat_id}", response_model=ProductCategoryRead)
async def get_product_category_endpoint(
    cat_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductCategoryRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_product_category(session, cat_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product category not found.",
        )
    return _cat_to_read(obj)


@router.put("/product-categories/{cat_id}", response_model=ProductCategoryRead)
async def update_product_category_endpoint(
    cat_id: uuid.UUID,
    body: ProductCategoryWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductCategoryRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_product_category(session, cat_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product category not found.",
        )
    try:
        result = await update_product_category(session, obj, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.delete(
    "/product-categories/{cat_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_product_category_endpoint(
    cat_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_product_category(session, cat_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product category not found.",
        )
    await delete_product_category(session, obj)
    await session.commit()


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


@router.get("/products", response_model=ProductListResponse)
async def list_products_endpoint(
    q: str | None = Query(default=None),
    category_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="created_at", pattern="^(name|created_at)$"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_products(
        session,
        company_id,
        q=q,
        category_id=category_id,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_product_endpoint(
    body: ProductWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_product(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.get("/products/{product_id}", response_model=ProductRead)
async def get_product_endpoint(
    product_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_product(session, product_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found."
        )
    from jai.services.product import _load_category

    category = await _load_category(session, obj.category_id, company_id)
    return _product_to_read(obj, category)


@router.put("/products/{product_id}", response_model=ProductRead)
async def update_product_endpoint(
    product_id: uuid.UUID,
    body: ProductWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_product(session, product_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found."
        )
    try:
        result = await update_product(session, obj, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    product_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    obj = await get_product(session, product_id, company_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found."
        )
    await delete_product(session, obj)
    await session.commit()
