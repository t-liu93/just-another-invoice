"""Pydantic schemas for estimate / costing (M6.5 step 1).

Step 1 schemas (calculation preview):
  EstimateLineInput / EstimateGroupInput / EstimateCalculationRequest
  EstimateLineCalculationRead / EstimateGroupCalculationRead / EstimateCalculationRead
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_str(v: object) -> str:
    """Strip whitespace from a string value, or raise ValueError for non-strings."""
    if not isinstance(v, str):
        raise ValueError("must be a string")
    return v.strip()


# ---------------------------------------------------------------------------
# Line / Group input schemas
# ---------------------------------------------------------------------------


class EstimateLineInput(BaseModel):
    """Single cost/margin line in an estimate (internal, owner-only).

    Labor / shipping / overhead are just lines with ``margin_rate = 0``.
    """

    product_id: uuid.UUID | None = None
    name: str = Field(min_length=1, description="Line name (internal, owner-only).")
    description: str | None = None
    unit_cost_excl_vat: Decimal = Field(
        ge=0,
        description="Cost per unit (excl. VAT). Must be >= 0.",
    )
    quantity: Decimal = Field(
        gt=0,
        description="Quantity. Must be > 0.",
    )
    margin_rate: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Markup-on-cost rate. Default 0 (labor/shipping/overhead).",
    )
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    group_ref: str | None = Field(
        default=None,
        description="Client-side key linking to a group ref.",
    )

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: object) -> str:
        return _strip_str(v)

    @field_validator("group_ref", mode="before")
    @classmethod
    def strip_group_ref(cls, v: object) -> str | None:
        if v is None:
            return None
        return _strip_str(v)


class EstimateGroupInput(BaseModel):
    """Group projection for estimate -> quote line."""

    ref: str = Field(
        min_length=1,
        description="Client-side unique key for this group.",
    )
    public_description: str = Field(
        min_length=1,
        description="The only text that enters the quote.",
    )
    vat_rate_id: uuid.UUID | None = Field(
        default=None,
        description="VAT rate for this group. Nullable (draft state).",
    )
    sort_order: int = Field(default=0, ge=0)

    @field_validator("ref", mode="before")
    @classmethod
    def strip_ref(cls, v: object) -> str:
        return _strip_str(v)

    @field_validator("public_description", mode="before")
    @classmethod
    def strip_public_description(cls, v: object) -> str:
        return _strip_str(v)


# ---------------------------------------------------------------------------
# Calculation request / response (step 1)
# ---------------------------------------------------------------------------


class EstimateCalculationRequest(BaseModel):
    """Request body for ``POST /api/v1/estimates/calculate``."""

    groups: list[EstimateGroupInput] = []
    lines: list[EstimateLineInput] = []

    @model_validator(mode="after")
    def validate_group_refs(self) -> EstimateCalculationRequest:
        """Ensure group refs are unique and all line group_refs resolve."""
        # Check for duplicate group refs
        seen: set[str] = set()
        for g in self.groups:
            if g.ref in seen:
                raise ValueError(f"Duplicate group ref: '{g.ref}'")
            seen.add(g.ref)

        # Check all non-null line group_refs match an existing group
        for i, line in enumerate(self.lines):
            if line.group_ref is not None and line.group_ref not in seen:
                raise ValueError(
                    f"Line {i + 1} ('{line.name}'): "
                    f"group_ref '{line.group_ref}' does not match any group."
                )

        return self


class EstimateLineCalculationRead(BaseModel):
    """Per-line calculation result (internal, owner-only)."""

    sort_order: int
    line_total: Decimal
    margin_amount: Decimal
    line_sell_excl_vat: Decimal
    group_ref: str | None = None


class EstimateGroupCalculationRead(BaseModel):
    """Per-group calculation result."""

    ref: str
    group_sell_excl_vat: Decimal


class EstimateCalculationRead(BaseModel):
    """Estimate costing preview result."""

    lines: list[EstimateLineCalculationRead]
    groups: list[EstimateGroupCalculationRead]
    total_margin: Decimal
    total_excl_vat: Decimal
    total_incl_vat_indicative: Decimal | None = None
    indicative_vat_rate_percent: Decimal | None = None
