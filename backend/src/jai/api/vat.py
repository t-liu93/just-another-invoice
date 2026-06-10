"""VAT dictionary API routes – vat_rate + vat_treatment CRUD (M4 step 1).

Endpoints:
  GET    /api/v1/vat-rates             – list all rates for the company
  POST   /api/v1/vat-rates             – create a new rate
  GET    /api/v1/vat-rates/{id}        – single rate
  PUT    /api/v1/vat-rates/{id}        – update
  DELETE /api/v1/vat-rates/{id}        – delete

  GET    /api/v1/vat-treatments        – list treatments (optional ?side= filter)
  POST   /api/v1/vat-treatments        – create
  GET    /api/v1/vat-treatments/{id}   – single treatment
  PUT    /api/v1/vat-treatments/{id}   – update
  DELETE /api/v1/vat-treatments/{id}   – delete
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models._enums import VatTreatmentSide
from jai.models.user import User
from jai.schemas.vat import (
    VatRateListResponse,
    VatRateRead,
    VatRateWrite,
    VatTreatmentListResponse,
    VatTreatmentRead,
    VatTreatmentWrite,
)
from jai.services.vat import (
    create_vat_rate,
    create_vat_treatment,
    delete_vat_rate,
    delete_vat_treatment,
    get_vat_rate,
    get_vat_treatment,
    list_vat_rates,
    list_vat_treatments,
    update_vat_rate,
    update_vat_treatment,
)

router = APIRouter(prefix="/api/v1", tags=["vat"])


def _owner_only(user: User) -> None:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required.")


def _require_company_id(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


# ---------------------------------------------------------------------------
# VAT Rates
# ---------------------------------------------------------------------------


@router.get("/vat-rates", response_model=VatRateListResponse)
async def list_vat_rates_endpoint(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatRateListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_vat_rates(session, company_id)


@router.post(
    "/vat-rates", response_model=VatRateRead, status_code=status.HTTP_201_CREATED
)
async def create_vat_rate_endpoint(
    body: VatRateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatRateRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_vat_rate(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return result


@router.get("/vat-rates/{rate_id}", response_model=VatRateRead)
async def get_vat_rate_endpoint(
    rate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatRateRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    rate = await get_vat_rate(session, rate_id, company_id)
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VAT rate not found.")
    from jai.services.vat import _rate_to_read

    return _rate_to_read(rate)


@router.put("/vat-rates/{rate_id}", response_model=VatRateRead)
async def update_vat_rate_endpoint(
    rate_id: uuid.UUID,
    body: VatRateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatRateRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    rate = await get_vat_rate(session, rate_id, company_id)
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VAT rate not found.")
    try:
        result = await update_vat_rate(session, rate, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return result


@router.delete("/vat-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vat_rate_endpoint(
    rate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    rate = await get_vat_rate(session, rate_id, company_id)
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VAT rate not found.")
    try:
        await delete_vat_rate(session, rate)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete VAT rate: it is referenced by one or more invoices.",
        ) from exc


# ---------------------------------------------------------------------------
# VAT Treatments
# ---------------------------------------------------------------------------


@router.get("/vat-treatments", response_model=VatTreatmentListResponse)
async def list_vat_treatments_endpoint(
    side: VatTreatmentSide | None = Query(None, description="Filter by side: SALES or PURCHASE"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatTreatmentListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_vat_treatments(session, company_id, side=side)


@router.post(
    "/vat-treatments",
    response_model=VatTreatmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_vat_treatment_endpoint(
    body: VatTreatmentWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatTreatmentRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        result = await create_vat_treatment(session, body, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return result


@router.get("/vat-treatments/{treatment_id}", response_model=VatTreatmentRead)
async def get_vat_treatment_endpoint(
    treatment_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatTreatmentRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    treatment = await get_vat_treatment(session, treatment_id, company_id)
    if treatment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="VAT treatment not found."
        )
    from jai.services.vat import _treatment_to_read

    return _treatment_to_read(treatment)


@router.put("/vat-treatments/{treatment_id}", response_model=VatTreatmentRead)
async def update_vat_treatment_endpoint(
    treatment_id: uuid.UUID,
    body: VatTreatmentWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> VatTreatmentRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    treatment = await get_vat_treatment(session, treatment_id, company_id)
    if treatment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="VAT treatment not found."
        )
    try:
        result = await update_vat_treatment(session, treatment, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return result


@router.delete("/vat-treatments/{treatment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vat_treatment_endpoint(
    treatment_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    treatment = await get_vat_treatment(session, treatment_id, company_id)
    if treatment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="VAT treatment not found."
        )
    try:
        await delete_vat_treatment(session, treatment)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete VAT treatment: it is referenced by one or more invoices.",
        ) from exc
