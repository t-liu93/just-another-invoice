"""Pydantic schemas for product_category and product CRUD (M4 step 3).

ProductCategory  – Write / Read / ListResponse
Product          – Write / Read / ListResponse (includes effective_margin_rate)

effective_margin_rate is a computed field in ProductRead:
  product.margin_rate if set, else category.default_margin_rate, else None.
It is NOT stored in the DB; the service layer resolves it at read time.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

# NUMERIC(6,4): max integer digits = 6-4 = 2, so max value = 99.9999
_NUMERIC_6_4_MAX = Decimal("99.9999")
# NUMERIC(14,3): max integer digits = 14-3 = 11, so max value = 99999999999.999
_NUMERIC_14_3_MAX = Decimal("99999999999.999")

# ---------------------------------------------------------------------------
# ProductCategory
# ---------------------------------------------------------------------------


class ProductCategoryWrite(BaseModel):
    """Request body for POST/PUT /api/v1/product-categories."""

    name: str = Field(min_length=1)
    default_margin_rate: Decimal | None = None
    active: bool = True

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("default_margin_rate")
    @classmethod
    def validate_margin(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("default_margin_rate must be >= 0")
        if v is not None and v > _NUMERIC_6_4_MAX:
            raise ValueError(f"default_margin_rate must be <= {_NUMERIC_6_4_MAX}")
        return v


class ProductCategoryRead(BaseModel):
    """Response body for product category endpoints."""

    id: uuid.UUID
    name: str
    default_margin_rate: Decimal | None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductCategoryListResponse(BaseModel):
    """List envelope for GET /api/v1/product-categories."""

    items: list[ProductCategoryRead]


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


class ProductWrite(BaseModel):
    """Request body for POST/PUT /api/v1/products."""

    name: str = Field(min_length=1)
    sku: str | None = None
    category_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    purchase_cost_excl_vat: Decimal | None = None
    margin_rate: Decimal | None = None
    default_vat_rate_id: uuid.UUID | None = None
    supplier: str | None = None
    extra: dict = Field(default_factory=dict)  # type: ignore[type-arg]
    active: bool = True

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("purchase_cost_excl_vat")
    @classmethod
    def validate_cost(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("purchase_cost_excl_vat must be >= 0")
        if v is not None and v > _NUMERIC_14_3_MAX:
            raise ValueError(f"purchase_cost_excl_vat must be <= {_NUMERIC_14_3_MAX}")
        return v

    @field_validator("margin_rate")
    @classmethod
    def validate_margin(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("margin_rate must be >= 0")
        if v is not None and v > _NUMERIC_6_4_MAX:
            raise ValueError(f"margin_rate must be <= {_NUMERIC_6_4_MAX}")
        return v


class ProductRead(BaseModel):
    """Response body for product endpoints.

    ``effective_margin_rate`` is resolved by the service layer:
    product.margin_rate if set, else category.default_margin_rate, else None.
    """

    id: uuid.UUID
    name: str
    sku: str | None
    category_id: uuid.UUID | None
    unit_id: uuid.UUID | None
    purchase_cost_excl_vat: Decimal | None
    margin_rate: Decimal | None
    effective_margin_rate: Decimal | None
    default_vat_rate_id: uuid.UUID | None
    supplier: str | None
    extra: dict  # type: ignore[type-arg]
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    """Paginated list envelope for GET /api/v1/products."""

    items: list[ProductRead]
    total: int


# ---------------------------------------------------------------------------
# Product import (step 4)
# ---------------------------------------------------------------------------


class ProductImportRow(BaseModel):
    """One row in a bulk import request (POST /api/v1/products/import).

    Numeric fields accept str so bad values are caught per-row in the service
    layer instead of 422-ing the whole request at parse time.
    """

    name: str = ""
    sku: str | None = None
    category_name: str | None = None
    unit_code: str | None = None
    purchase_cost_excl_vat: str | Decimal | None = None
    margin_rate: str | Decimal | None = None
    vat_percent: str | Decimal | None = None


class ProductImportRowError(BaseModel):
    """A single per-row validation/lookup failure returned by the import endpoint."""

    row: int
    message: str


class ProductImportResult(BaseModel):
    """Response from POST /api/v1/products/import."""

    created: int
    updated: int = 0
    errors: list[ProductImportRowError]


class ProductImportRequest(BaseModel):
    """Request body for POST /api/v1/products/import."""

    rows: list[ProductImportRow]
