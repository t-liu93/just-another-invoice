"""Estimate API routes -- calculate (M6.5 step 1).

Endpoints:
  POST /api/v1/estimates/calculate  -- costing preview (no persistence)
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.models.vat import VatRate
from jai.schemas.estimate import (
    EstimateCalculationRead,
    EstimateCalculationRequest,
)
from jai.services.costing import compute_estimate

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
# Costing preview (step 1)
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
        # Batch-load all referenced rates, validate they belong to company
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

    # Pure computation
    return compute_estimate(body, standard_vat_percent=standard_vat_percent)
