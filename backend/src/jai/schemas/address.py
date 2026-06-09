"""Pydantic schemas for address (M3 step 2).

``AddressFields`` – reusable base with structured European/Dutch fields + ISO 3166
                    country validation; also used by CompanyWrite/Read (step 3).
``AddressWrite``  – AddressFields + ``type`` (BILLING|SHIPPING); embedded in
                    CustomerWrite.addresses[].
``AddressRead``   – AddressWrite + ``id``; embedded in CustomerRead.addresses[].
"""

from __future__ import annotations

import uuid

import pycountry
from pydantic import BaseModel, field_validator

from jai.models._enums import AddressType


def _validate_country_code(v: str | None) -> str | None:
    """Validate ISO 3166-1 alpha-2 country code.

    Only accepts canonical 2-letter codes (not English names).
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


class AddressFields(BaseModel):
    """Structured European/Dutch address fields, reusable across entities."""

    street: str | None = None
    house_number: str | None = None
    house_number_addition: str | None = None
    postal_code: str | None = None
    city: str | None = None
    province: str | None = None
    country_code: str | None = None

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str | None) -> str | None:
        """Validate ISO 3166-1 alpha-2 country code."""
        return _validate_country_code(v)


class AddressWrite(AddressFields):
    """Address payload embedded in CustomerWrite.addresses[]."""

    type: AddressType


class AddressRead(AddressFields):
    """Address response embedded in CustomerRead.addresses[]."""

    id: uuid.UUID
    type: AddressType

    model_config = {"from_attributes": True}
