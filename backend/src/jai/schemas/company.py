"""Pydantic schemas for the company business profile.

``CompanyWrite`` – request body for ``PUT /api/v1/company``.
``CompanyRead``  – response body for ``GET /api/v1/company`` and ``PUT``.

Address fields are inherited from ``AddressFields`` (M3 step 3), aligning the
company address with the structured European/Dutch customer-address schema.

Validation:
  - ``base_currency`` must be a valid ISO 4217 3-letter code.
  - ``country_code`` (via AddressFields) must be a valid ISO 3166-1 alpha-2 code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import pycountry
from pydantic import BaseModel, StringConstraints, field_validator

from jai.schemas.address import AddressFields


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


class CompanyWrite(AddressFields):
    """Request body for ``PUT /api/v1/company``.

    Address fields (street / house_number / house_number_addition / postal_code /
    city / province / country_code) are inherited from ``AddressFields``.
    """

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    vat_id: str | None = None
    coc_number: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    base_currency: str

    @field_validator("base_currency")
    @classmethod
    def validate_base_currency(cls, v: str) -> str:
        return _validate_currency(v)


class CompanyRead(AddressFields):
    """Response body for ``GET /api/v1/company`` and ``PUT``.

    Address fields (street / house_number / house_number_addition / postal_code /
    city / province / country_code) are inherited from ``AddressFields``.
    """

    id: uuid.UUID
    name: str
    vat_id: str | None = None
    coc_number: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    base_currency: str
    has_logo: bool = False
    logo_url: str | None = None
    created_at: datetime
    updated_at: datetime


class CompanyLogoRead(BaseModel):
    """Response body for ``PUT /api/v1/company/logo``."""

    logo_url: str
    mime_type: str
    byte_size: int
