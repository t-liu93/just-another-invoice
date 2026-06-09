"""Pydantic schemas for VAT rate and VAT treatment CRUD (M4 step 1).

``VatRateWrite``      – request body for POST/PUT /api/v1/vat-rates
``VatRateRead``       – response body
``VatRateListResponse`` – list envelope

``VatTreatmentWrite``      – request body
``VatTreatmentRead``       – response body
``VatTreatmentListResponse`` – list envelope
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from jai.models._enums import VatTreatmentEffect, VatTreatmentSide


class VatRateWrite(BaseModel):
    """Request body for POST/PUT /api/v1/vat-rates."""

    label: str = Field(min_length=1)
    percent: Decimal = Field(ge=Decimal("0"))
    active: bool = True

    @field_validator("label")
    @classmethod
    def strip_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label must not be blank")
        return v


class VatRateRead(BaseModel):
    """Response body for VAT rate endpoints."""

    id: uuid.UUID
    label: str
    percent: Decimal
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VatRateListResponse(BaseModel):
    """List envelope for GET /api/v1/vat-rates."""

    items: list[VatRateRead]


class VatTreatmentWrite(BaseModel):
    """Request body for POST/PUT /api/v1/vat-treatments.

    ``report_box`` is intentionally absent: M4 keeps it NULL throughout;
    M10 will fill it collaboratively and add the field back here.
    """

    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    side: VatTreatmentSide
    effect: VatTreatmentEffect
    requires_icp: bool = False
    deductible: bool | None = None
    active: bool = True

    @field_validator("code", "label")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class VatTreatmentRead(BaseModel):
    """Response body for VAT treatment endpoints."""

    id: uuid.UUID
    code: str
    label: str
    side: VatTreatmentSide
    effect: VatTreatmentEffect
    report_box: str | None
    requires_icp: bool
    deductible: bool | None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VatTreatmentListResponse(BaseModel):
    """List envelope for GET /api/v1/vat-treatments."""

    items: list[VatTreatmentRead]
