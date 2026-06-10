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
    ProductImportResult,
    ProductImportRow,
    ProductImportRowError,
    ProductListResponse,
    ProductRead,
    ProductWrite,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# DB column upper bounds used for per-row import validation.
_IMPORT_COST_MAX = Decimal("99999999999.999")   # NUMERIC(14,3)
_IMPORT_MARGIN_MAX = Decimal("99.9999")          # NUMERIC(6,4)


def _parse_import_decimal(
    raw: str | Decimal | None,
    field: str,
    *,
    min_val: Decimal | None = None,
    max_val: Decimal | None = None,
) -> tuple[Decimal | None, str | None]:
    """Parse a raw import value to Decimal with optional range checks.

    Returns (parsed_value, error_message); on success error_message is None.
    """
    if raw is None:
        return None, None
    try:
        parsed = Decimal(str(raw))
    except Exception:
        return None, f"{field}: not a valid number ({raw!r})"
    if not parsed.is_finite():
        return None, f"{field}: not a valid number ({raw!r})"
    if min_val is not None and parsed < min_val:
        return None, f"{field}: must be >= {min_val}"
    if max_val is not None and parsed > max_val:
        return None, f"{field}: must be <= {max_val}"
    return parsed, None


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
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ValueError(f"A product with SKU '{data.sku}' already exists.") from exc
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
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ValueError(f"A product with SKU '{data.sku}' already exists.") from exc
    await session.refresh(obj)
    category = await _load_category(session, obj.category_id, obj.company_id)
    return _product_to_read(obj, category)


async def delete_product(session: AsyncSession, obj: Product) -> None:
    await session.delete(obj)
    await session.flush()


# ---------------------------------------------------------------------------
# Product import (step 4)
# ---------------------------------------------------------------------------


async def import_products(
    session: AsyncSession,
    rows: list[ProductImportRow],
    company_id: uuid.UUID,
) -> ProductImportResult:
    """Bulk-create products from structured import rows (Excel paste → TSV → JSON).

    Lookup maps for category/unit/vat_rate are loaded once per call.
    Per-row failures go to ``errors``; valid rows are batch-flushed.
    """
    # Pre-load lookup maps (one query each, scoped to company_id).
    cats_result = await session.execute(
        select(ProductCategory).where(ProductCategory.company_id == company_id)
    )
    cat_by_name: dict[str, uuid.UUID] = {
        c.name.lower(): c.id for c in cats_result.scalars().all()
    }

    units_result = await session.execute(
        select(Unit).where(Unit.company_id == company_id)
    )
    unit_by_code: dict[str, uuid.UUID] = {
        u.code.lower(): u.id for u in units_result.scalars().all()
    }

    vat_result = await session.execute(
        select(VatRate).where(VatRate.company_id == company_id)
    )
    vat_by_percent: dict[Decimal, uuid.UUID] = {
        Decimal(str(v.percent)): v.id for v in vat_result.scalars().all()
    }

    # Pre-fetch existing products by supplier SKU for upsert matching.
    non_empty_skus = [r.sku.strip() for r in rows if r.sku and r.sku.strip()]
    existing_by_sku: dict[str, Product] = {}
    if non_empty_skus:
        existing_result = await session.execute(
            select(Product).where(
                Product.company_id == company_id,
                Product.sku.in_(non_empty_skus),
            )
        )
        existing_by_sku = {
            (p.sku or "").strip(): p
            for p in existing_result.scalars().all()
            if p.sku
        }

    created = 0
    updated = 0
    errors: list[ProductImportRowError] = []

    for idx, row in enumerate(rows):
        row_errors: list[str] = []

        name = row.name.strip() if row.name else ""
        if not name:
            row_errors.append("name is required")

        # Resolve category_name.
        category_id: uuid.UUID | None = None
        has_category = row.category_name is not None and row.category_name.strip()
        if has_category:
            key = row.category_name.strip().lower()  # type: ignore[union-attr]
            if key in cat_by_name:
                category_id = cat_by_name[key]
            else:
                row_errors.append(f"Unknown category: '{row.category_name}'")

        # Resolve unit_code.
        unit_id: uuid.UUID | None = None
        has_unit = row.unit_code is not None and row.unit_code.strip()
        if has_unit:
            key = row.unit_code.strip().lower()  # type: ignore[union-attr]
            if key in unit_by_code:
                unit_id = unit_by_code[key]
            else:
                row_errors.append(f"Unknown unit code: '{row.unit_code}'")

        # Parse and validate numeric fields (per-row errors, not whole-request 422).
        cost_val, cost_err = _parse_import_decimal(
            row.purchase_cost_excl_vat, "purchase_cost_excl_vat",
            min_val=Decimal("0"), max_val=_IMPORT_COST_MAX,
        )
        if cost_err:
            row_errors.append(cost_err)

        margin_val, margin_err = _parse_import_decimal(
            row.margin_rate, "margin_rate",
            min_val=Decimal("0"), max_val=_IMPORT_MARGIN_MAX,
        )
        if margin_err:
            row_errors.append(margin_err)

        # Resolve vat_percent.
        default_vat_rate_id: uuid.UUID | None = None
        if row.vat_percent is not None:
            pct_val, pct_err = _parse_import_decimal(row.vat_percent, "vat_percent")
            if pct_err:
                row_errors.append(pct_err)
            elif pct_val not in vat_by_percent:
                row_errors.append(f"Unknown VAT percent: {row.vat_percent}")
            else:
                default_vat_rate_id = vat_by_percent[pct_val]

        if row_errors:
            errors.append(ProductImportRowError(row=idx, message="; ".join(row_errors)))
            continue

        sku = row.sku.strip() if row.sku and row.sku.strip() else None

        if sku and sku in existing_by_sku:
            # Upsert: update fields that are explicitly provided in this row.
            obj = existing_by_sku[sku]
            obj.name = name
            if has_category:
                obj.category_id = category_id
            if has_unit:
                obj.unit_id = unit_id
            if cost_val is not None:
                obj.purchase_cost_excl_vat = cost_val
            if margin_val is not None:
                obj.margin_rate = margin_val
            if row.vat_percent is not None:
                obj.default_vat_rate_id = default_vat_rate_id
            updated += 1
        else:
            obj = Product(
                company_id=company_id,
                name=name,
                sku=sku,
                category_id=category_id,
                unit_id=unit_id,
                purchase_cost_excl_vat=cost_val,
                margin_rate=margin_val,
                default_vat_rate_id=default_vat_rate_id,
                extra={},
            )
            session.add(obj)
            if sku:
                existing_by_sku[sku] = obj  # track within-batch dedup
            created += 1

    if created > 0 or updated > 0:
        await session.flush()

    return ProductImportResult(created=created, updated=updated, errors=errors)
