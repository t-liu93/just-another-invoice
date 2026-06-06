"""Pydantic schemas for the company business profile (M2 step 1).

``CompanyWrite`` – request body for ``PUT /api/v1/company``.
``CompanyRead``  – response body for ``GET /api/v1/company`` and ``PUT``.

Validation:
  - ``base_currency`` must be a valid ISO 4217 3-letter code.
  - ``country_code`` must be a valid ISO 3166-1 alpha-2 code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import pycountry
from pydantic import BaseModel, StringConstraints, field_validator


def _validate_currency(v: str) -> str:
    """Ensure *v* is a valid ISO 4217 alpha-3 currency code.

    Uses ``get(alpha_3=...)`` instead of ``lookup()`` so that English names
    like ``"Euro"`` are **not** accepted — only the canonical 3-letter code.
    """
    code = v.strip().upper()
    if len(code) != 3:
        raise ValueError(
            f"Invalid ISO 4217 currency code: {v!r} (must be exactly 3 letters)"
        )
    if pycountry.currencies.get(alpha_3=code) is None:
        raise ValueError(f"Invalid ISO 4217 currency code: {v!r}")
    return code


def _validate_country_code(v: str | None) -> str | None:
    """Ensure *v* is a valid ISO 3166-1 alpha-2 country code.

    Uses ``get(alpha_2=...)`` instead of ``lookup()`` so that English names
    like ``"Netherlands"`` are **not** accepted.
    """
    if v is None:
        return v
    code = v.strip().upper()
    if len(code) != 2:
        raise ValueError(
            f"Invalid ISO 3166-1 alpha-2 country code: {v!r} (must be exactly 2 letters)"
        )
    if pycountry.countries.get(alpha_2=code) is None:
        raise ValueError(f"Invalid ISO 3166-1 alpha-2 country code: {v!r}")
    return code


class CompanyWrite(BaseModel):
    """Request body for ``PUT /api/v1/company``."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    vat_id: str | None = None
    coc_number: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country_code: str | None = None
    base_currency: str

    @field_validator("base_currency")
    @classmethod
    def validate_base_currency(cls, v: str) -> str:
        return _validate_currency(v)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str | None) -> str | None:
        return _validate_country_code(v)


class CompanyRead(BaseModel):
    """Response body for ``GET /api/v1/company`` and ``PUT``."""

    id: uuid.UUID
    name: str
    vat_id: str | None = None
    coc_number: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country_code: str | None = None
    base_currency: str
    has_logo: bool = False
    logo_url: str | None = None
    created_at: datetime
    updated_at: datetime
