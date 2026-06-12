"""Estimate service – CRUD + costing calculation + snapshot (M6.5 step 2).

Public API
----------
- ``create_estimate``  – validate FKs, compute costing, persist 3 tables
- ``update_estimate``  – replace sub-tables, recompute amounts
- ``delete_estimate``  – DB cascade removes lines/groups
- ``get_estimate``     – fetch by id + company_id (returns None if not found)
- ``list_estimates``   – filtered/paginated list with customer name JOIN

Red-line compliance
-------------------
1. All money calculation in ``services.costing``; this module only persists.
2. company_id always injected by service; front-end never provides it.
3. No manual cascade deletes – DB ON DELETE CASCADE handles sub-tables.
8. Cost snapshot: unit_cost_excl_vat / margin_rate locked at write time;
   M4 catalogue changes do NOT back-fill historical estimates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models.customer import Customer
from jai.models.dictionary import Unit
from jai.models.estimate import Estimate, EstimateGroup, EstimateLine
from jai.models.product import Product
from jai.models.quote import Quote
from jai.models.vat import VatRate
from jai.schemas.estimate import (
    EstimateCalculationRequest,
    EstimateGroupRead,
    EstimateLineRead,
    EstimateListItem,
    EstimateListResponse,
    EstimateRead,
    EstimateWrite,
)
from jai.services.costing import compute_estimate
from jai.services.money import quantize_money

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_standard_vat_percent(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> Decimal | None:
    """Return the highest active VAT rate percent (for indicative total)."""
    stmt = (
        select(VatRate)
        .where(
            VatRate.company_id == company_id,
            VatRate.active == True,  # noqa: E712
        )
        .order_by(VatRate.percent.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    rate = result.scalar_one_or_none()
    return Decimal(str(rate.percent)) if rate else None


async def _validate_fks(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: EstimateWrite,
) -> None:
    """Validate all cross-company foreign key references in the write body."""
    # Customer
    if body.customer_id is not None:
        count_stmt = select(func.count()).where(
            Customer.id == body.customer_id,
            Customer.company_id == company_id,
        )
        found = (await session.execute(count_stmt)).scalar_one()
        if not found:
            raise ValueError("Customer not found or does not belong to this company.")

    # Group VAT rates
    vat_ids = {g.vat_rate_id for g in body.groups if g.vat_rate_id is not None}
    if vat_ids:
        count_stmt = select(func.count()).where(
            VatRate.id.in_(vat_ids),
            VatRate.company_id == company_id,
        )
        found = (await session.execute(count_stmt)).scalar_one()
        if found != len(vat_ids):
            raise ValueError(
                "One or more VAT rate IDs not found or do not belong to this company."
            )

    # Line products
    product_ids = {ln.product_id for ln in body.lines if ln.product_id is not None}
    if product_ids:
        count_stmt = select(func.count()).where(
            Product.id.in_(product_ids),
            Product.company_id == company_id,
        )
        found = (await session.execute(count_stmt)).scalar_one()
        if found != len(product_ids):
            raise ValueError(
                "One or more product IDs not found or do not belong to this company."
            )

    # Line units
    unit_ids = {ln.unit_id for ln in body.lines if ln.unit_id is not None}
    if unit_ids:
        count_stmt = select(func.count()).where(
            Unit.id.in_(unit_ids),
            Unit.company_id == company_id,
        )
        found = (await session.execute(count_stmt)).scalar_one()
        if found != len(unit_ids):
            raise ValueError(
                "One or more unit IDs not found or do not belong to this company."
            )


async def _estimate_to_read(
    session: AsyncSession,
    estimate: Estimate,
    company_id: uuid.UUID,
) -> EstimateRead:
    """Build a full EstimateRead from an ORM estimate (groups/lines already loaded)."""
    # Customer name
    customer_name: str | None = None
    if estimate.customer_id is not None:
        cust_stmt = select(Customer.name).where(Customer.id == estimate.customer_id)
        customer_name = (await session.execute(cust_stmt)).scalar_one_or_none()

    # Generated quote number
    generated_quote_number: str | None = None
    if estimate.generated_quote_id is not None:
        qnum_stmt = select(Quote.quote_number).where(
            Quote.id == estimate.generated_quote_id
        )
        generated_quote_number = (await session.execute(qnum_stmt)).scalar_one_or_none()

    # Batch-load VAT rate info for groups
    group_vat_ids = {
        g.vat_rate_id for g in estimate.groups if g.vat_rate_id is not None
    }
    vat_rate_map: dict[uuid.UUID, tuple[str, Decimal]] = {}
    if group_vat_ids:
        vr_stmt = select(VatRate).where(VatRate.id.in_(group_vat_ids))
        for vr in (await session.execute(vr_stmt)).scalars():
            vat_rate_map[vr.id] = (vr.label, Decimal(str(vr.percent)))

    # Indicative VAT
    standard_pct = await _get_standard_vat_percent(session, company_id)
    total_excl_vat = Decimal(str(estimate.total_excl_vat))
    total_incl_vat_indicative: Decimal | None = None
    indicative_vat_rate_percent: Decimal | None = None
    if standard_pct is not None:
        indicative_vat_rate_percent = standard_pct
        total_incl_vat_indicative = quantize_money(
            total_excl_vat * (Decimal("1") + standard_pct / Decimal("100"))
        )

    group_reads = []
    for g in estimate.groups:
        vr_label: str | None = None
        vr_percent: Decimal | None = None
        if g.vat_rate_id is not None and g.vat_rate_id in vat_rate_map:
            vr_label, vr_percent = vat_rate_map[g.vat_rate_id]
        group_reads.append(
            EstimateGroupRead(
                id=g.id,
                sort_order=g.sort_order,
                public_description=g.public_description,
                vat_rate_id=g.vat_rate_id,
                vat_rate_label=vr_label,
                vat_rate_percent=vr_percent,
                group_sell_excl_vat=Decimal(str(g.group_sell_excl_vat)),
            )
        )

    line_reads = [
        EstimateLineRead(
            id=ln.id,
            group_id=ln.group_id,
            sort_order=ln.sort_order,
            product_id=ln.product_id,
            name=ln.name,
            description=ln.description,
            unit_id=ln.unit_id,
            unit_name=ln.unit_name,
            unit_cost_excl_vat=Decimal(str(ln.unit_cost_excl_vat)),
            quantity=Decimal(str(ln.quantity)),
            margin_rate=Decimal(str(ln.margin_rate)),
            line_total=Decimal(str(ln.line_total)),
            margin_amount=Decimal(str(ln.margin_amount)),
            line_sell_excl_vat=Decimal(str(ln.line_sell_excl_vat)),
        )
        for ln in estimate.lines
    ]

    return EstimateRead(
        id=estimate.id,
        name=estimate.name,
        customer_id=estimate.customer_id,
        customer_name=customer_name,
        notes=estimate.notes,
        total_margin=Decimal(str(estimate.total_margin)),
        total_excl_vat=total_excl_vat,
        total_incl_vat_indicative=total_incl_vat_indicative,
        indicative_vat_rate_percent=indicative_vat_rate_percent,
        generated_quote_id=estimate.generated_quote_id,
        generated_quote_number=generated_quote_number,
        lines=line_reads,
        groups=group_reads,
        created_at=estimate.created_at,
        updated_at=estimate.updated_at,
    )


def _build_groups_and_lines(
    estimate_id: uuid.UUID,
    body: EstimateWrite,
    calc: object,  # EstimateCalculationRead
) -> tuple[list[EstimateGroup], list[EstimateLine]]:
    """Build EstimateGroup and EstimateLine ORM objects without persisting.

    Pre-generates UUIDs for groups so lines can reference them without any
    intermediate flush.  Returns (groups, lines) to be session.add()-ed by
    the caller.  Never assigns to ORM relationship attributes to avoid
    triggering sync lazy-loads in an async context.
    """
    from jai.schemas.estimate import EstimateCalculationRead  # local to avoid cycle

    calc_result: EstimateCalculationRead = calc  # type: ignore[assignment]

    # Build calc group lookup
    calc_group_map = {gc.ref: gc for gc in calc_result.groups}

    # Pre-assign UUIDs to groups and build ref → id map
    group_ref_to_id: dict[str, uuid.UUID] = {}
    new_groups: list[EstimateGroup] = []
    for i, g_input in enumerate(body.groups):
        gid = uuid.uuid4()
        gc = calc_group_map.get(g_input.ref)
        group = EstimateGroup(
            id=gid,
            estimate_id=estimate_id,
            sort_order=g_input.sort_order if g_input.sort_order is not None else i,
            public_description=g_input.public_description,
            vat_rate_id=g_input.vat_rate_id,
            group_sell_excl_vat=(
                gc.group_sell_excl_vat if gc is not None else Decimal("0.000")
            ),
        )
        new_groups.append(group)
        group_ref_to_id[g_input.ref] = gid

    # Build lines with group_id and computed snapshot amounts
    new_lines: list[EstimateLine] = []
    for i, (l_input, l_calc) in enumerate(
        zip(body.lines, calc_result.lines, strict=True)
    ):
        group_id = (
            group_ref_to_id[l_input.group_ref]
            if l_input.group_ref is not None and l_input.group_ref in group_ref_to_id
            else None
        )
        line = EstimateLine(
            estimate_id=estimate_id,
            group_id=group_id,
            sort_order=i,
            product_id=l_input.product_id,
            name=l_input.name,
            description=l_input.description,
            unit_id=l_input.unit_id,
            unit_name=l_input.unit_name,
            unit_cost_excl_vat=l_input.unit_cost_excl_vat,
            quantity=l_input.quantity,
            margin_rate=l_input.margin_rate,
            line_total=l_calc.line_total,
            margin_amount=l_calc.margin_amount,
            line_sell_excl_vat=l_calc.line_sell_excl_vat,
        )
        new_lines.append(line)

    return new_groups, new_lines


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def create_estimate(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: EstimateWrite,
) -> EstimateRead:
    """Validate FKs, compute costing, persist estimate + groups + lines."""
    await _validate_fks(session, company_id, body)

    standard_pct = await _get_standard_vat_percent(session, company_id)
    calc = compute_estimate(
        EstimateCalculationRequest(groups=body.groups, lines=body.lines),
        standard_vat_percent=standard_pct,
    )

    estimate = Estimate(
        company_id=company_id,
        name=body.name,
        customer_id=body.customer_id,
        notes=body.notes,
        total_margin=calc.total_margin,
        total_excl_vat=calc.total_excl_vat,
    )
    session.add(estimate)
    await session.flush()  # assign estimate.id before building sub-rows

    new_groups, new_lines = _build_groups_and_lines(estimate.id, body, calc)
    for g in new_groups:
        session.add(g)
    for ln in new_lines:
        session.add(ln)

    await session.flush()
    await session.commit()
    await session.refresh(estimate)
    return await _estimate_to_read(session, estimate, company_id)


async def update_estimate(
    session: AsyncSession,
    estimate_id: uuid.UUID,
    company_id: uuid.UUID,
    body: EstimateWrite,
) -> EstimateRead | None:
    """Replace sub-tables, recompute amounts. Returns None if not found / wrong company."""
    stmt = select(Estimate).where(
        Estimate.id == estimate_id,
        Estimate.company_id == company_id,
    )
    result = await session.execute(stmt)
    estimate = result.scalar_one_or_none()
    if estimate is None:
        return None

    await _validate_fks(session, company_id, body)

    standard_pct = await _get_standard_vat_percent(session, company_id)
    calc = compute_estimate(
        EstimateCalculationRequest(groups=body.groups, lines=body.lines),
        standard_vat_percent=standard_pct,
    )

    # Delete existing sub-rows via SQL DELETE (avoids accessing relationship
    # attributes synchronously which would trigger an illegal async lazy-load).
    # Lines reference groups via SET NULL FK, so delete lines first.
    await session.execute(
        delete(EstimateLine).where(EstimateLine.estimate_id == estimate.id)
    )
    await session.execute(
        delete(EstimateGroup).where(EstimateGroup.estimate_id == estimate.id)
    )
    await session.flush()

    # Update root fields; always touch updated_at so sub-table-only edits
    # (e.g. changing a line description with no net cost change) still refresh
    # the timestamp even when SQLAlchemy's dirty-check skips the parent UPDATE.
    estimate.name = body.name
    estimate.customer_id = body.customer_id
    estimate.notes = body.notes
    estimate.total_margin = calc.total_margin
    estimate.total_excl_vat = calc.total_excl_vat
    estimate.updated_at = datetime.now(UTC)

    new_groups, new_lines = _build_groups_and_lines(estimate.id, body, calc)
    for g in new_groups:
        session.add(g)
    for ln in new_lines:
        session.add(ln)

    await session.flush()
    await session.commit()
    await session.refresh(estimate)
    return await _estimate_to_read(session, estimate, company_id)


async def delete_estimate(
    session: AsyncSession,
    estimate_id: uuid.UUID,
    company_id: uuid.UUID,
) -> bool:
    """Delete estimate (DB cascade removes groups + lines). Returns False if not found."""
    stmt = select(Estimate).where(
        Estimate.id == estimate_id,
        Estimate.company_id == company_id,
    )
    result = await session.execute(stmt)
    estimate = result.scalar_one_or_none()
    if estimate is None:
        return False

    await session.delete(estimate)
    await session.commit()
    return True


async def get_estimate(
    session: AsyncSession,
    estimate_id: uuid.UUID,
    company_id: uuid.UUID,
) -> EstimateRead | None:
    """Return full EstimateRead or None if not found / wrong company."""
    stmt = select(Estimate).where(
        Estimate.id == estimate_id,
        Estimate.company_id == company_id,
    )
    result = await session.execute(stmt)
    estimate = result.scalar_one_or_none()
    if estimate is None:
        return None
    return await _estimate_to_read(session, estimate, company_id)


async def list_estimates(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: Literal["name", "created_at", "updated_at"] = "updated_at",
) -> EstimateListResponse:
    """Paginated list of estimates with optional customer name JOIN."""
    base = (
        select(Estimate, Customer.name.label("customer_name"))
        .outerjoin(Customer, Customer.id == Estimate.customer_id)
        .where(Estimate.company_id == company_id)
    )

    if q:
        term = f"%{q}%"
        base = base.where(
            or_(
                Estimate.name.ilike(term),
                Customer.name.ilike(term),
            )
        )

    if customer_id is not None:
        base = base.where(Estimate.customer_id == customer_id)

    # Total count
    count_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Sort column
    sort_col = {
        "name": Estimate.name,
        "created_at": Estimate.created_at,
        "updated_at": Estimate.updated_at,
    }[sort_by]

    rows_stmt = (
        base.order_by(sort_col)
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(rows_stmt)).all()

    items = [
        EstimateListItem(
            id=row.Estimate.id,
            name=row.Estimate.name,
            customer_id=row.Estimate.customer_id,
            customer_name=row.customer_name,
            total_excl_vat=Decimal(str(row.Estimate.total_excl_vat)),
            total_margin=Decimal(str(row.Estimate.total_margin)),
            generated_quote_id=row.Estimate.generated_quote_id,
            updated_at=row.Estimate.updated_at,
        )
        for row in rows
    ]
    return EstimateListResponse(items=items, total=total)
