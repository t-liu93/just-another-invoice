"""Pydantic schemas for customer CRUD + list (M3 step 1 + step 2).

``CustomerWrite`` – request body for ``POST/PUT /api/v1/customers``.
``CustomerRead``  – response body for ``GET /api/v1/customers`` and ``POST/PUT``.
``CustomerListResponse`` – paginated list envelope.

Validation:
  - ``currency`` must be a valid ISO 4217 3-letter code (reuses company validator).
  - ``email`` must match a basic email pattern.
  - ``name`` must be non-empty after stripping whitespace.
  - ``addresses[].type`` must be unique within a single request (→ 422).
  - ``addresses[].country_code`` must be a valid ISO 3166-1 alpha-2 code
    (validated inside AddressWrite via AddressFields).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from jai.schemas.address import AddressRead, AddressWrite
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
    addresses: list[AddressWrite] = Field(default_factory=list)

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

    @model_validator(mode="after")
    def validate_address_types_unique(self) -> CustomerWrite:
        """Ensure no duplicate address types in a single request."""
        types = [a.type for a in self.addresses]
        if len(types) != len(set(types)):
            raise ValueError("Duplicate address types are not allowed.")
        return self


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
    addresses: list[AddressRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CustomerListResponse(BaseModel):
    """Paginated list envelope for ``GET /api/v1/customers``."""

    items: list[CustomerRead]
    total: int
