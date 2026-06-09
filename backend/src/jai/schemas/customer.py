"""Pydantic schemas for customer CRUD + list (M3 step 1).

``CustomerWrite`` – request body for ``POST/PUT /api/v1/customers``.
``CustomerRead``  – response body for ``GET /api/v1/customers`` and ``POST/PUT``.
``CustomerListResponse`` – paginated list envelope.

Validation:
  - ``currency`` must be a valid ISO 4217 3-letter code (reuses company validator).
  - ``email`` must match a basic email pattern.
  - ``name`` must be non-empty after stripping whitespace.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator

from jai.schemas.company import _validate_currency

#: Simple email regex – not RFC 5322 complete, but catches obvious mistakes.
_BASIC_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CustomerWrite(BaseModel):
    """Request body for ``POST/PUT /api/v1/customers``."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    contact_name: str | None = None
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    vat_id: str | None = None
    currency: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        """Validate ISO 4217 alpha-3 code, reusing the company validator."""
        if v is None:
            return v
        return _validate_currency(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        """Basic email format check."""
        if v is None:
            return v
        v = v.strip()
        if not _BASIC_EMAIL_RE.match(v):
            raise ValueError(f"Invalid email format: {v!r}")
        return v


class CustomerRead(BaseModel):
    """Response body for customer endpoints (no ``company_id`` exposed)."""

    id: uuid.UUID
    name: str
    contact_name: str | None = None
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    vat_id: str | None = None
    currency: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    addresses: list[object] = Field(default_factory=list)  # placeholder for step 2
    created_at: datetime
    updated_at: datetime


class CustomerListResponse(BaseModel):
    """Paginated list envelope for ``GET /api/v1/customers``."""

    items: list[CustomerRead]
    total: int
