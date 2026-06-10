"""Product catalogue service – CRUD for product_category + product (M4 step 3).

Public API
----------
Category:
- ``list_product_categories``
- ``get_product_category``
- ``create_product_category``
- ``update_product_category``
- ``delete_product_category``

Product:
- ``list_products``        – filtered / paginated / sorted; returns (items, total)
- ``get_product``
- ``create_product``
- ``update_product``
- ``delete_product``

Design notes
------------
- All queries filter by ``company_id``; front-end never sends it (red-line 2).
- ``effective_margin_rate`` is computed at read time:
    product.margin_rate if set → else category.default_margin_rate → else None.
  It is NOT persisted (no double-write).
- UNIQUE(company_id, name) on product_category; IntegrityError → ValueError.
- Decimal precision follows red-line 1; no money arithmetic here.
- No seeds for product_category (JIT review: leave empty, user fills it in).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models.dictionary import Unit
from jai.models.product import Product, ProductCategory
from jai.models.vat import VatRate
from jai.schemas.product import (
    ProductCategoryListResponse,
    ProductCategoryRead,
    ProductCategoryWrite,
    ProductListResponse,
    ProductRead,
    ProductWrite,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cat_to_read(obj: ProductCategory) -> ProductCategoryRead:
    return ProductCategoryRead(
        id=obj.id,
        name=obj.name,
        default_margin_rate=(
            Decimal(str(obj.default_margin_rate))
            if obj.default_margin_rate is not None
            else None
        ),
        active=obj.active,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _resolve_effective_margin(
    product: Product,
    category: ProductCategory | None,
) -> Decimal | None:
    """Compute effective_margin_rate without persisting."""
    if product.margin_rate is not None:
        return Decimal(str(product.margin_rate))
    if category is not None and category.default_margin_rate is not None:
        return Decimal(str(category.default_margin_rate))
    return None


async def _load_category(
    session: AsyncSession,
    category_id: uuid.UUID | None,
    company_id: uuid.UUID,
) -> ProductCategory | None:
    if category_id is None:
        return None
    stmt = select(ProductCategory).where(
        ProductCategory.id == category_id,
        ProductCategory.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _product_to_read(
    obj: Product,
    category: ProductCategory | None,
) -> ProductRead:
    return ProductRead(
        id=obj.id,
        name=obj.name,
        sku=obj.sku,
        category_id=obj.category_id,
        unit_id=obj.unit_id,
        purchase_cost_excl_vat=(
            Decimal(str(obj.purchase_cost_excl_vat))
            if obj.purchase_cost_excl_vat is not None
            else None
        ),
        margin_rate=(
            Decimal(str(obj.margin_rate)) if obj.margin_rate is not None else None
        ),
        effective_margin_rate=_resolve_effective_margin(obj, category),
        default_vat_rate_id=obj.default_vat_rate_id,
        supplier=obj.supplier,
        extra=obj.extra or {},
        active=obj.active,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


async def _validate_product_fks(
    session: AsyncSession,
    company_id: uuid.UUID,
    category_id: uuid.UUID | None,
    unit_id: uuid.UUID | None,
    default_vat_rate_id: uuid.UUID | None,
) -> None:
    """Raise ValueError if any FK points to a row that doesn't belong to this company."""
    if category_id is not None:
        row = await session.execute(
            select(ProductCategory.id).where(
                ProductCategory.id == category_id,
                ProductCategory.company_id == company_id,
            )
        )
        if row.scalar_one_or_none() is None:
            raise ValueError(f"Product category {category_id} not found for this company.")

    if unit_id is not None:
        row = await session.execute(
            select(Unit.id).where(
                Unit.id == unit_id,
                Unit.company_id == company_id,
            )
        )
        if row.scalar_one_or_none() is None:
            raise ValueError(f"Unit {unit_id} not found for this company.")

    if default_vat_rate_id is not None:
        row = await session.execute(
            select(VatRate.id).where(
                VatRate.id == default_vat_rate_id,
                VatRate.company_id == company_id,
            )
        )
        if row.scalar_one_or_none() is None:
            raise ValueError(f"VAT rate {default_vat_rate_id} not found for this company.")


async def _check_category_unique(
    session: AsyncSession,
    company_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(ProductCategory).where(
        ProductCategory.company_id == company_id,
        ProductCategory.name == name,
    )
    if exclude_id is not None:
        stmt = stmt.where(ProductCategory.id != exclude_id)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise ValueError(f"Product category '{name}' already exists for this company.")


# ---------------------------------------------------------------------------
# ProductCategory CRUD
# ---------------------------------------------------------------------------


async def list_product_categories(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> ProductCategoryListResponse:
    stmt = (
        select(ProductCategory)
        .where(ProductCategory.company_id == company_id)
        .order_by(ProductCategory.name)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return ProductCategoryListResponse(items=[_cat_to_read(r) for r in rows])


async def get_product_category(
    session: AsyncSession,
    cat_id: uuid.UUID,
    company_id: uuid.UUID,
) -> ProductCategory | None:
    stmt = select(ProductCategory).where(
        ProductCategory.id == cat_id,
        ProductCategory.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_product_category(
    session: AsyncSession,
    data: ProductCategoryWrite,
    company_id: uuid.UUID,
) -> ProductCategoryRead:
    await _check_category_unique(session, company_id, data.name)
    obj = ProductCategory(
        company_id=company_id,
        name=data.name,
        default_margin_rate=data.default_margin_rate,
        active=data.active,
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Product category '{data.name}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _cat_to_read(obj)


async def update_product_category(
    session: AsyncSession,
    obj: ProductCategory,
    data: ProductCategoryWrite,
) -> ProductCategoryRead:
    await _check_category_unique(session, obj.company_id, data.name, exclude_id=obj.id)
    obj.name = data.name
    obj.default_margin_rate = data.default_margin_rate
    obj.active = data.active
    try:
        await session.flush()
    except IntegrityError as err:
        await session.rollback()
        raise ValueError(
            f"Product category '{data.name}' already exists for this company."
        ) from err
    await session.refresh(obj)
    return _cat_to_read(obj)


async def delete_product_category(
    session: AsyncSession, obj: ProductCategory
) -> None:
    await session.delete(obj)
    await session.flush()


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------


async def list_products(
    session: AsyncSession,
    company_id: uuid.UUID,
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "created_at",
) -> ProductListResponse:
    base = select(Product).where(Product.company_id == company_id)

    if q:
        pattern = f"%{q}%"
        base = base.where(
            or_(
                Product.name.ilike(pattern),
                Product.sku.ilike(pattern),
            )
        )
    if category_id is not None:
        base = base.where(Product.category_id == category_id)

    # Count total matching rows.
    count_stmt = select(func.count()).select_from(base.subquery())
    total_result = await session.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Sort.
    order_col = Product.name if sort_by == "name" else Product.created_at.desc()
    items_stmt = base.order_by(order_col).limit(limit).offset(offset)
    items_result = await session.execute(items_stmt)
    rows = items_result.scalars().all()

    # Bulk-load the categories referenced by the fetched rows (in one query).
    cat_ids = {r.category_id for r in rows if r.category_id is not None}
    categories: dict[uuid.UUID, ProductCategory] = {}
    if cat_ids:
        cat_stmt = select(ProductCategory).where(
            ProductCategory.id.in_(cat_ids),
            ProductCategory.company_id == company_id,
        )
        cat_result = await session.execute(cat_stmt)
        for cat in cat_result.scalars().all():
            categories[cat.id] = cat

    items = [
        _product_to_read(r, categories.get(r.category_id) if r.category_id else None)
        for r in rows
    ]
    return ProductListResponse(items=items, total=total)


async def get_product(
    session: AsyncSession,
    product_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Product | None:
    stmt = select(Product).where(
        Product.id == product_id,
        Product.company_id == company_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_product(
    session: AsyncSession,
    data: ProductWrite,
    company_id: uuid.UUID,
) -> ProductRead:
    await _validate_product_fks(
        session, company_id, data.category_id, data.unit_id, data.default_vat_rate_id
    )
    obj = Product(
        company_id=company_id,
        name=data.name,
        sku=data.sku,
        category_id=data.category_id,
        unit_id=data.unit_id,
        purchase_cost_excl_vat=data.purchase_cost_excl_vat,
        margin_rate=data.margin_rate,
        default_vat_rate_id=data.default_vat_rate_id,
        supplier=data.supplier,
        extra=data.extra or {},
        active=data.active,
    )
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    category = await _load_category(session, obj.category_id, company_id)
    return _product_to_read(obj, category)


async def update_product(
    session: AsyncSession,
    obj: Product,
    data: ProductWrite,
) -> ProductRead:
    await _validate_product_fks(
        session, obj.company_id, data.category_id, data.unit_id, data.default_vat_rate_id
    )
    obj.name = data.name
    obj.sku = data.sku
    obj.category_id = data.category_id
    obj.unit_id = data.unit_id
    obj.purchase_cost_excl_vat = data.purchase_cost_excl_vat
    obj.margin_rate = data.margin_rate
    obj.default_vat_rate_id = data.default_vat_rate_id
    obj.supplier = data.supplier
    obj.extra = data.extra or {}
    obj.active = data.active
    await session.flush()
    await session.refresh(obj)
    category = await _load_category(session, obj.category_id, obj.company_id)
    return _product_to_read(obj, category)


async def delete_product(session: AsyncSession, obj: Product) -> None:
    await session.delete(obj)
    await session.flush()
