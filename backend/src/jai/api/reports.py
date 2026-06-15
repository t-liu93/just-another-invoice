"""Reports API routes (M10 step 1).

Endpoints (all GET, owner-only, requires MFA):
  GET /api/v1/reports/profit-loss  – P/L report with time series.

Design:
- Thin controller: validate parameters, call services/reporting, return schema.
- ``from`` is a Python reserved word; accepted via ``Query(..., alias="from")``
  and exposed as ``from`` in OpenAPI (D-alias pattern).
- Auth pattern mirrors api/payments.py: ``current_mfa_user`` + ``_owner_only``
  + ``_require_company_id``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.report import ProfitLossReport
from jai.services.reporting.pl import compute_profit_loss

router = APIRouter(prefix="/api/v1", tags=["reports"])


# ---------------------------------------------------------------------------
# Auth helpers (mirror payments.py pattern)
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
# P/L report
# ---------------------------------------------------------------------------


@router.get(
    "/reports/profit-loss",
    response_model=ProfitLossReport,
    response_model_by_alias=True,
)
async def get_profit_loss(
    date_from: Annotated[date, Query(alias="from", description="Inclusive start date.")],
    date_to: Annotated[date, Query(alias="to", description="Inclusive end date.")],
    granularity: Literal["month", "quarter"] = Query(
        default="month",
        description="Time-series bucket size: 'month' or 'quarter'.",
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProfitLossReport:
    """Return a P/L (profit & loss) report for the given date range.

    Revenue is aggregated from invoices with status SENT or COMPLETED,
    using ``base_taxable_amount`` (net EUR, post-discount).

    Expenses are aggregated from confirmed (``is_draft=false``) expenses
    with straight-line depreciation prorated by ``business_percentage``.
    Each expense contributes an annual slice to the year of its purchase
    anniversary within the requested window.

    Returns a top-level summary plus a time-series breakdown by month
    or quarter.
    """
    _owner_only(user)
    company_id = _require_company_id(user)

    if date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'from' must not be after 'to'.",
        )

    return await compute_profit_loss(
        session,
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
    )
