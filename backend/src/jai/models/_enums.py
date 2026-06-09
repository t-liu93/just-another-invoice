"""Enum definitions shared across ORM models.

This module will grow as business entities are introduced (M1+).
"""
from __future__ import annotations

import enum


class SettingLevel(enum.StrEnum):
    """Hierarchy level for the three-layer settings system.

    ``USER`` > ``COMPANY`` > ``GLOBAL`` — lookup falls back from the most
    specific level to the most general.
    """

    GLOBAL = "GLOBAL"
    COMPANY = "COMPANY"
    USER = "USER"


class AddressType(enum.StrEnum):
    """Address type for customer addresses (billing or shipping)."""

    BILLING = "BILLING"
    SHIPPING = "SHIPPING"


class VatTreatmentSide(enum.StrEnum):
    """Which side of a transaction a VAT treatment applies to."""

    SALES = "SALES"
    PURCHASE = "PURCHASE"


class VatTreatmentEffect(enum.StrEnum):
    """How a VAT treatment affects the tax amount on a line item."""

    APPLY_RATE = "APPLY_RATE"
    ZERO_REVERSE = "ZERO_REVERSE"
    ZERO_EXPORT = "ZERO_EXPORT"
    EXEMPT = "EXEMPT"
