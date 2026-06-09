"""Pydantic schemas for payment_method, expense_category, and unit CRUD (M4 step 2).

Three isomorphic schema triples:
  Write  – request body for POST / PUT
  Read   – response body
  List   – list envelope { items: [...] }
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Payment Method
# ---------------------------------------------------------------------------


class PaymentMethodWrite(BaseModel):
    """Request body for POST/PUT /api/v1/payment-methods."""

    name: str = Field(min_length=1)
    active: bool = True

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class PaymentMethodRead(BaseModel):
    """Response body for payment method endpoints."""

    id: uuid.UUID
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaymentMethodListResponse(BaseModel):
    """List envelope for GET /api/v1/payment-methods."""

    items: list[PaymentMethodRead]


# ---------------------------------------------------------------------------
# Expense Category
# ---------------------------------------------------------------------------


class ExpenseCategoryWrite(BaseModel):
    """Request body for POST/PUT /api/v1/expense-categories."""

    name: str = Field(min_length=1)
    description: str | None = None
    default_deductible: bool | None = None
    active: bool = True

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class ExpenseCategoryRead(BaseModel):
    """Response body for expense category endpoints."""

    id: uuid.UUID
    name: str
    description: str | None
    default_deductible: bool | None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExpenseCategoryListResponse(BaseModel):
    """List envelope for GET /api/v1/expense-categories."""

    items: list[ExpenseCategoryRead]


# ---------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------


class UnitWrite(BaseModel):
    """Request body for POST/PUT /api/v1/units."""

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    active: bool = True

    @field_validator("code", "name")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class UnitRead(BaseModel):
    """Response body for unit endpoints."""

    id: uuid.UUID
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UnitListResponse(BaseModel):
    """List envelope for GET /api/v1/units."""

    items: list[UnitRead]
