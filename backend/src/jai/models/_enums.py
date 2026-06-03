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
