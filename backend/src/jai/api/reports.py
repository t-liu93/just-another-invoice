"""Reports API routes (M10 steps 1 & 2).

Endpoints (all GET, owner-only, requires MFA):
  GET /api/v1/reports/profit-loss  – P/L report with time series.
  GET /api/v1/reports/vat-return   – BTW VAT return summary (NL ruleset).

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import SettingLevel
from jai.models.company import Company
from jai.models.user import User
from jai.schemas.report import ProfitLossReport, VatReturnReport
from jai.schemas.setting import SETTING_KEY_VAT_RATE_TIERS, VatRateTiers
from jai.services.reporting.btw import compute_vat_return
from jai.services.reporting.pl import compute_profit_loss
from jai.services.settings import get_setting

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


# ---------------------------------------------------------------------------
# VAT return report (M10 step 2)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/vat-return",
    response_model=VatReturnReport,
    response_model_by_alias=True,
)
async def get_vat_return(
    year: int = Query(
        description="Calendar year of the VAT return period (e.g. 2026).",
        ge=2000,
        le=2100,
    ),
    quarter: int = Query(
        description="Quarter of the VAT return period (1–4).",
        ge=1,
        le=4,
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatReturnReport:
    """Return the BTW (VAT) quarterly return summary for the given year and quarter.

    Aggregates invoice VAT snapshots and expense VAT amounts into the standard
    Dutch BTW return boxes (1a/1b/1c/1d/1e/2a/3a/3b/3c/4a/4b/5b) using the
    NL ruleset.  Non-NL companies fall back to the NL ruleset with a warning.

    The rate tier thresholds (hoog/laag/zero) are read from the company-level
    ``reporting.vat_rate_tiers`` setting (defaults: hoog=21, laag=9, zero=0).

    Box 1d (private-use correction) is non-zero only when quarter == 4 and is
    based on the full calendar year's expenses.
    """
    _owner_only(user)
    company_id = _require_company_id(user)

    # Fetch the company record (needed for country_code).
    stmt_co = select(Company).where(Company.id == company_id)
    result_co = await session.execute(stmt_co)
    company = result_co.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company profile not found.",
        )

    # Load VAT rate tier thresholds from the company-level setting (with defaults).
    tiers_setting = await get_setting(
        session,
        SETTING_KEY_VAT_RATE_TIERS,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=VatRateTiers,
    )
    tiers = tiers_setting if tiers_setting is not None else VatRateTiers()

    return await compute_vat_return(
        session,
        company=company,
        year=year,
        quarter=quarter,
        tiers=tiers,
    )
