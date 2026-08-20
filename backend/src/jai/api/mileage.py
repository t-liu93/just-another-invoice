"""M11 mileage routes: defaults, dictionaries, calculation, and trip CRUD."""

from __future__ import annotations

import uuid
from datetime import date
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.api.expenses import _owner_only, _require_company_id
from jai.auth.deps import current_mfa_user
from jai.db import get_session
from jai.models.user import User
from jai.schemas.mileage import (
    MileageCalculationRead,
    MileageCalculationRequest,
    MileageDefaultsRead,
    MileageDefaultsUpdate,
    MileageExpenseListResponse,
    MileageExpenseRead,
    MileageExpenseWrite,
    MileageRateAdjustmentListResponse,
    MileageRateListResponse,
    MileageRateRead,
    MileageRateWrite,
    MileageRecalculationApplyRead,
    MileageRecalculationApplyRequest,
    MileageRecalculationPreviewRead,
    MileageTransportTypeListResponse,
    MileageTransportTypeRead,
    MileageTransportTypeWrite,
)
from jai.services.mileage import (
    MileageConfigurationError,
    get_mileage_defaults,
    update_mileage_defaults,
)
from jai.services.mileage import (
    calculate_mileage_expense as calculate_mileage_expense_service,
)
from jai.services.mileage import (
    create_mileage_expense as create_mileage_expense_service,
)
from jai.services.mileage import (
    create_mileage_rate as create_mileage_rate_service,
)
from jai.services.mileage import (
    create_mileage_transport_type as create_mileage_transport_type_service,
)
from jai.services.mileage import (
    delete_mileage_expense as delete_mileage_expense_service,
)
from jai.services.mileage import (
    delete_mileage_rate as delete_mileage_rate_service,
)
from jai.services.mileage import (
    delete_mileage_transport_type as delete_mileage_transport_type_service,
)
from jai.services.mileage import (
    get_mileage_expense as get_mileage_expense_service,
)
from jai.services.mileage import (
    get_mileage_rate as get_mileage_rate_service,
)
from jai.services.mileage import (
    get_mileage_transport_type as get_mileage_transport_type_service,
)
from jai.services.mileage import (
    list_mileage_expenses as list_mileage_expenses_service,
)
from jai.services.mileage import (
    list_mileage_rates as list_mileage_rates_service,
)
from jai.services.mileage import (
    list_mileage_transport_types as list_mileage_transport_types_service,
)
from jai.services.mileage import (
    update_mileage_expense as update_mileage_expense_service,
)
from jai.services.mileage import (
    update_mileage_rate as update_mileage_rate_service,
)
from jai.services.mileage import (
    update_mileage_transport_type as update_mileage_transport_type_service,
)

router = APIRouter(prefix="/api/v1", tags=["mileage"])


def _not_implemented() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "This M11 mileage endpoint is contract-locked but not implemented "
            "until its scheduled step."
        ),
    )


@router.get("/settings/mileage-defaults", response_model=MileageDefaultsRead)
async def get_mileage_defaults_endpoint(
    user: User = Depends(current_mfa_user), session: AsyncSession = Depends(get_session)
) -> MileageDefaultsRead:
    _owner_only(user)
    try:
        return await get_mileage_defaults(session, _require_company_id(user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put("/settings/mileage-defaults", response_model=MileageDefaultsRead)
async def update_mileage_defaults_endpoint(
    body: MileageDefaultsUpdate,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageDefaultsRead:
    _owner_only(user)
    try:
        result = await update_mileage_defaults(session, _require_company_id(user), body)
        await session.commit()
        return result
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MileageConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/mileage-transport-types", response_model=MileageTransportTypeListResponse)
async def list_mileage_transport_types(
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageTransportTypeListResponse:
    _owner_only(user)
    return await list_mileage_transport_types_service(session, _require_company_id(user))


@router.post(
    "/mileage-transport-types",
    response_model=MileageTransportTypeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_mileage_transport_type(
    body: MileageTransportTypeWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageTransportTypeRead:
    _owner_only(user)
    try:
        return await create_mileage_transport_type_service(session, _require_company_id(user), body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/mileage-transport-types/{transport_type_id}", response_model=MileageTransportTypeRead)
async def get_mileage_transport_type(
    transport_type_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageTransportTypeRead:
    _owner_only(user)
    try:
        return await get_mileage_transport_type_service(
            session, transport_type_id, _require_company_id(user)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/mileage-transport-types/{transport_type_id}", response_model=MileageTransportTypeRead)
async def update_mileage_transport_type(
    transport_type_id: uuid.UUID,
    body: MileageTransportTypeWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageTransportTypeRead:
    _owner_only(user)
    try:
        return await update_mileage_transport_type_service(
            session, transport_type_id, _require_company_id(user), body
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete(
    "/mileage-transport-types/{transport_type_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_mileage_transport_type(
    transport_type_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    _owner_only(user)
    try:
        await delete_mileage_transport_type_service(
            session, transport_type_id, _require_company_id(user)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/mileage-rates", response_model=MileageRateListResponse)
async def list_mileage_rates(
    user: User = Depends(current_mfa_user), session: AsyncSession = Depends(get_session)
) -> MileageRateListResponse:
    _owner_only(user)
    return await list_mileage_rates_service(session, _require_company_id(user))


@router.post("/mileage-rates", response_model=MileageRateRead, status_code=status.HTTP_201_CREATED)
async def create_mileage_rate(
    body: MileageRateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageRateRead:
    _owner_only(user)
    try:
        return await create_mileage_rate_service(session, _require_company_id(user), body)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/mileage-rates/{rate_id}", response_model=MileageRateRead)
async def get_mileage_rate(
    rate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageRateRead:
    _owner_only(user)
    try:
        return await get_mileage_rate_service(session, rate_id, _require_company_id(user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/mileage-rates/{rate_id}", response_model=MileageRateRead)
async def update_mileage_rate(
    rate_id: uuid.UUID,
    body: MileageRateWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageRateRead:
    _owner_only(user)
    try:
        return await update_mileage_rate_service(session, rate_id, _require_company_id(user), body)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/mileage-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mileage_rate(
    rate_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    _owner_only(user)
    try:
        await delete_mileage_rate_service(session, rate_id, _require_company_id(user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mileage-expenses/calculate", response_model=MileageCalculationRead)
async def calculate_mileage_expense(
    body: MileageCalculationRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageCalculationRead:
    _owner_only(user)
    try:
        return await calculate_mileage_expense_service(session, _require_company_id(user), body)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MileageConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/mileage-expenses", response_model=MileageExpenseRead, status_code=status.HTTP_201_CREATED
)
async def create_mileage_expense(
    body: MileageExpenseWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageExpenseRead:
    _owner_only(user)
    try:
        return await create_mileage_expense_service(
            session, _require_company_id(user), body, creator_id=user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MileageConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/mileage-expenses", response_model=MileageExpenseListResponse)
async def list_mileage_expenses(
    q: str | None = None,
    transport_type_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="trip_date", pattern="^(trip_date|created_at)$"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageExpenseListResponse:
    _owner_only(user)
    return await list_mileage_expenses_service(
        session,
        _require_company_id(user),
        q=q,
        transport_type_id=transport_type_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.get("/mileage-expenses/{trip_id}", response_model=MileageExpenseRead)
async def get_mileage_expense(
    trip_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageExpenseRead:
    _owner_only(user)
    try:
        return await get_mileage_expense_service(session, trip_id, _require_company_id(user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MileageConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put("/mileage-expenses/{trip_id}", response_model=MileageExpenseRead)
async def update_mileage_expense(
    trip_id: uuid.UUID,
    body: MileageExpenseWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> MileageExpenseRead:
    _owner_only(user)
    try:
        return await update_mileage_expense_service(
            session, trip_id, _require_company_id(user), body, actor_id=user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MileageConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.delete("/mileage-expenses/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mileage_expense(
    trip_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    _owner_only(user)
    try:
        await delete_mileage_expense_service(session, trip_id, _require_company_id(user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MileageConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/mileage-expenses/{trip_id}/rate-adjustments", response_model=MileageRateAdjustmentListResponse
)
async def list_mileage_rate_adjustments(
    trip_id: uuid.UUID, user: User = Depends(current_mfa_user)
) -> MileageRateAdjustmentListResponse:
    _owner_only(user)
    _not_implemented()


@router.post(
    "/mileage-expenses/rate-recalculation/preview", response_model=MileageRecalculationPreviewRead
)
async def preview_mileage_rate_recalculation(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_mfa_user),
) -> MileageRecalculationPreviewRead:
    _owner_only(user)
    _not_implemented()


@router.post(
    "/mileage-expenses/rate-recalculation/apply", response_model=MileageRecalculationApplyRead
)
async def apply_mileage_rate_recalculation(
    body: MileageRecalculationApplyRequest, user: User = Depends(current_mfa_user)
) -> MileageRecalculationApplyRead:
    _owner_only(user)
    _not_implemented()
