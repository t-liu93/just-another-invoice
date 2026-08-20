"""Contract models for the M11 mileage-expense workflow.

Step 1 intentionally locks the complete OpenAPI shape.  Persistence endpoints
are registered as controlled stubs until their respective atomic steps land.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_NUMERIC_18_3_MAX = Decimal("999999999999999.999")
_NUMERIC_QUANT = Decimal("0.001")


def validate_positive_numeric_18_3(value: Decimal, label: str) -> Decimal:
    """Validate the positive, exact numeric domain shared with the service.

    PostgreSQL ``NUMERIC(18,3)`` can store insignificant trailing zeroes
    (``1.2300``) exactly.  Comparing an exact three-decimal quantisation keeps
    that representable input in the same domain as the service layer.
    """
    try:
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{label} must be a positive finite Decimal.")
        if value.quantize(_NUMERIC_QUANT) != value:
            raise ValueError(f"{label} must have at most 3 decimal places.")
        if value > _NUMERIC_18_3_MAX:
            raise ValueError(f"{label} exceeds the NUMERIC(18,3) limit.")
    except DecimalException as exc:
        raise ValueError(
            f"{label} must be a positive finite Decimal representable as NUMERIC(18,3)."
        ) from exc
    return value


def validate_nonnegative_numeric_18_3(value: Decimal, label: str) -> Decimal:
    """Validate a finite, non-negative value storable in ``NUMERIC(18,3)``.

    This is deliberately distinct from the distance/rate input domain: a
    positive distance multiplied by a positive rate may legitimately round to
    zero when the final amount is quantised to currency cents.
    """
    try:
        if not value.is_finite() or value < 0:
            raise ValueError(f"{label} must be a non-negative finite Decimal.")
        if value.quantize(_NUMERIC_QUANT) != value:
            raise ValueError(f"{label} must have at most 3 decimal places.")
        if value > _NUMERIC_18_3_MAX:
            raise ValueError(f"{label} exceeds the NUMERIC(18,3) limit.")
    except DecimalException as exc:
        raise ValueError(
            f"{label} must be a non-negative finite Decimal representable as NUMERIC(18,3)."
        ) from exc
    return value


class MileageDefaultsRead(BaseModel):
    expense_category_id: uuid.UUID
    default_transport_type_id: uuid.UUID


class MileageDefaultsUpdate(MileageDefaultsRead):
    pass


class MileageTransportTypeWrite(BaseModel):
    name: str = Field(min_length=1)
    active: bool = True

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Transport type name must not be blank.")
        return value


class MileageTransportTypeRead(MileageTransportTypeWrite):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class MileageTransportTypeListResponse(BaseModel):
    items: list[MileageTransportTypeRead]


class MileageRateWrite(BaseModel):
    transport_type_id: uuid.UUID | None = None
    effective_from: date
    rate_per_km: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)

    @field_validator("rate_per_km")
    @classmethod
    def _rate_per_km_fits_numeric_18_3(cls, value: Decimal) -> Decimal:
        return validate_positive_numeric_18_3(value, "Mileage rate")


class MileageRateRead(MileageRateWrite):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class MileageRateListResponse(BaseModel):
    items: list[MileageRateRead]


class MileageCalculationRequest(BaseModel):
    trip_date: date
    transport_type_id: uuid.UUID | None = None
    one_way_distance_km: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    round_trip: bool = False

    @field_validator("one_way_distance_km")
    @classmethod
    def _distance_fits_numeric_18_3(cls, value: Decimal) -> Decimal:
        return validate_positive_numeric_18_3(value, "One-way distance")


class MileageCalculationRead(BaseModel):
    one_way_distance_km: Decimal
    total_distance_km: Decimal
    rate_rule_id: uuid.UUID
    rate_effective_from: date
    rate_per_km: Decimal
    amount: Decimal
    currency: str


class MileageExpenseWrite(MileageCalculationRequest):
    origin_address: str | None = None
    destination_address: str | None = None
    purpose: str | None = None
    note: str | None = None


class MileageExpenseRead(MileageExpenseWrite):
    id: uuid.UUID
    expense_id: uuid.UUID | None = None
    expense_category_id: uuid.UUID | None = None
    transport_type_name: str
    total_distance_km: Decimal
    rate_rule_id: uuid.UUID | None = None
    rate_effective_from: date
    rate_per_km: Decimal
    amount: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime


class MileageExpenseListItem(BaseModel):
    id: uuid.UUID
    trip_date: date
    transport_type_id: uuid.UUID | None = None
    transport_type_name: str
    one_way_distance_km: Decimal
    total_distance_km: Decimal
    round_trip: bool
    rate_per_km: Decimal
    amount: Decimal
    purpose: str | None = None
    origin_address: str | None = None
    destination_address: str | None = None
    created_at: datetime


class MileageExpenseListResponse(BaseModel):
    items: list[MileageExpenseListItem]
    total: int


class MileageRateAdjustmentRead(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    old_rate_rule_id: uuid.UUID | None = None
    new_rate_rule_id: uuid.UUID | None = None
    old_rate_transport_type_id: uuid.UUID | None = None
    new_rate_transport_type_id: uuid.UUID | None = None
    old_rate_transport_type_name: str | None = None
    new_rate_transport_type_name: str | None = None
    old_rate_effective_from: date
    new_rate_effective_from: date
    old_rate_per_km: Decimal
    new_rate_per_km: Decimal
    old_amount: Decimal
    new_amount: Decimal
    actor_id: uuid.UUID | None = None
    created_at: datetime


class MileageRateAdjustmentListResponse(BaseModel):
    items: list[MileageRateAdjustmentRead]


class MileageRecalculationPreviewItem(BaseModel):
    trip_id: uuid.UUID
    trip_date: date
    old_rate_rule_id: uuid.UUID | None = None
    new_rate_rule_id: uuid.UUID
    old_amount: Decimal
    new_amount: Decimal
    delta: Decimal


class MileageRecalculationPreviewRead(BaseModel):
    preview_token: str
    affected_count: int
    old_total: Decimal
    new_total: Decimal
    delta: Decimal
    items: list[MileageRecalculationPreviewItem]
    total: int
    limit: int
    offset: int


class MileageRecalculationApplyRequest(BaseModel):
    preview_token: str = Field(min_length=1)


class MileageRecalculationApplyRead(BaseModel):
    affected_count: int
    old_total: Decimal
    new_total: Decimal
    delta: Decimal


MileageSortBy = Literal["trip_date", "created_at"]
