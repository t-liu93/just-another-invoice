"""Estimate API routes – calculate + CRUD + generate-quote (M6.5 steps 1–3).

Endpoints:
  POST   /api/v1/estimates/calculate          – costing preview (no persistence)
  GET    /api/v1/estimates                    – paginated list
  POST   /api/v1/estimates                    – create estimate + groups + lines
  GET    /api/v1/estimates/{id}               – get by id
  PUT    /api/v1/estimates/{id}               – update (replace sub-tables, recompute)
  DELETE /api/v1/estimates/{id}               – delete (DB cascade)
  POST   /api/v1/estimates/{id}/generate-quote – generate a new DRAFT quote (step 3)
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.models.vat import VatRate
from jai.schemas.estimate import (
    EstimateCalculationRead,
    EstimateCalculationRequest,
    EstimateListResponse,
    EstimateRead,
    EstimateWrite,
)
from jai.schemas.quote import QuoteRead
from jai.services.costing import compute_estimate
from jai.services.estimate import (
    create_estimate,
    delete_estimate,
    duplicate_estimate,
    generate_quote_from_estimate,
    get_estimate,
    list_estimates,
    update_estimate,
)

router = APIRouter(prefix="/api/v1", tags=["estimates"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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
# Costing preview (step 1) – must be defined before {id} routes
# ---------------------------------------------------------------------------


@router.post(
    "/estimates/calculate",
    response_model=EstimateCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_estimate_endpoint(
    body: EstimateCalculationRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EstimateCalculationRead:
    """Preview estimate costing without persisting."""
    _owner_only(user)
    company_id = _require_company_id(user)

    # Collect and validate vat_rate_ids from groups (non-null ones)
    vat_rate_ids: set[uuid.UUID] = set()
    for group in body.groups:
        if group.vat_rate_id is not None:
            vat_rate_ids.add(group.vat_rate_id)

    if vat_rate_ids:
        rates_stmt = select(VatRate).where(
            VatRate.id.in_(vat_rate_ids),
            VatRate.company_id == company_id,
        )
        rates_result = await session.execute(rates_stmt)
        rate_rows = rates_result.scalars().all()
        found_ids = {r.id for r in rate_rows}

        missing = vat_rate_ids - found_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "VAT rate(s) not found or do not belong to this company: "
                    f"{missing}"
                ),
            )

    # Determine standard VAT rate (highest active percent)
    standard_stmt = (
        select(VatRate)
        .where(
            VatRate.company_id == company_id,
            VatRate.active == True,  # noqa: E712
        )
        .order_by(VatRate.percent.desc())
        .limit(1)
    )
    standard_result = await session.execute(standard_stmt)
    standard_rate = standard_result.scalar_one_or_none()

    standard_vat_percent: Decimal | None = (
        Decimal(str(standard_rate.percent)) if standard_rate else None
    )

    try:
        return compute_estimate(body, standard_vat_percent=standard_vat_percent)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# CRUD (step 2)
# ---------------------------------------------------------------------------


@router.get("/estimates", response_model=EstimateListResponse)
async def list_estimates_endpoint(
    q: str | None = Query(default=None, description="Search by estimate name or customer name"),
    customer_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["name", "created_at", "updated_at"] = Query(default="updated_at"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EstimateListResponse:
    """Return a paginated list of estimates for the current company."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_estimates(
        session,
        company_id,
        q=q,
        customer_id=customer_id,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.post("/estimates", response_model=EstimateRead, status_code=status.HTTP_201_CREATED)
async def create_estimate_endpoint(
    body: EstimateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EstimateRead:
    """Create a new estimate with groups and lines."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await create_estimate(session, company_id=company_id, body=body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/estimates/{estimate_id}", response_model=EstimateRead)
async def get_estimate_endpoint(
    estimate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EstimateRead:
    """Return an estimate by ID (owner-only)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await get_estimate(session, estimate_id=estimate_id, company_id=company_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estimate not found.")
    return result


@router.put("/estimates/{estimate_id}", response_model=EstimateRead)
async def update_estimate_endpoint(
    estimate_id: uuid.UUID,
    body: EstimateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EstimateRead:
    """Update an estimate: replace lines/groups, recompute amounts."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await update_estimate(
            session,
            estimate_id=estimate_id,
            company_id=company_id,
            body=body,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estimate not found.")
    return result


@router.delete("/estimates/{estimate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_estimate_endpoint(
    estimate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an estimate and all its groups/lines."""
    _owner_only(user)
    company_id = _require_company_id(user)
    deleted = await delete_estimate(session, estimate_id=estimate_id, company_id=company_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estimate not found.")


@router.post(
    "/estimates/{estimate_id}/duplicate",
    response_model=EstimateRead,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_estimate_endpoint(
    estimate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EstimateRead:
    """Duplicate an estimate (owner-only).

    Creates a full copy of the estimate including groups and lines.
    The copy's name has \" (copy)\" appended; generated_quote_id is not copied
    (the duplicate is always a fresh draft).
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    result = await duplicate_estimate(
        session, estimate_id=estimate_id, company_id=company_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Estimate not found."
        )
    return result


@router.post(
    "/estimates/{estimate_id}/generate-quote",
    response_model=QuoteRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate_quote_endpoint(
    estimate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuoteRead:
    """Generate a new DRAFT quote from the estimate (M6.5 step 3).

    One public quote line per estimate group that contains at least one line.
    Zero-leak: no cost / margin / internal estimate data enters the quote.
    Can be called repeatedly; each call creates a fresh DRAFT quote and
    updates estimate.generated_quote_id to the latest one.
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await generate_quote_from_estimate(
            session,
            estimate_id=estimate_id,
            company_id=company_id,
            creator_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estimate not found.")
    return result
