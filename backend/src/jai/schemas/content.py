"""Pydantic schemas for content templates / content blocks / note templates (M6 step 4).

Three "named library" CRUD schemas:
  DocumentTemplateLineWrite / DocumentTemplateLineRead
  DocumentTemplateWrite / DocumentTemplateRead
  ContentBlockWrite / ContentBlockRead
  NoteTemplateWrite / NoteTemplateRead

These schemas handle only the library management (CRUD).  Applying a template
to a document is a *front-end* concern – the front-end reads a template and
value-copies its lines into the invoice/quote editor.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from jai.models._enums import ContentBlockKind, DiscountType, DocumentTemplateScope

# ---------------------------------------------------------------------------
# Document Template Line
# ---------------------------------------------------------------------------


class DocumentTemplateLineWrite(BaseModel):
    """Single line within a document template write request."""

    name: str = Field(min_length=1)
    description: str | None = None
    quantity: Decimal = Field(gt=0, description="Must be > 0.")
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    unit_price: Decimal | None = Field(default=None, ge=0)
    discount_type: DiscountType = DiscountType.NONE
    discount_value: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Discount value. Ignored when discount_type=NONE.",
    )
    vat_rate_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("description")
    @classmethod
    def strip_description(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
        return v


class DocumentTemplateLineRead(BaseModel):
    """Single line returned as part of a DocumentTemplateRead."""

    id: uuid.UUID
    sort_order: int
    name: str
    description: str | None = None
    quantity: Decimal
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    unit_price: Decimal | None = None
    discount_type: DiscountType
    discount_value: Decimal
    vat_rate_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Document Template
# ---------------------------------------------------------------------------


class DocumentTemplateWrite(BaseModel):
    """Request body for POST / PUT /api/v1/document-templates."""

    name: str = Field(min_length=1)
    applies_to: DocumentTemplateScope
    lines: list[DocumentTemplateLineWrite] = Field(
        min_length=1, description="At least 1 line required."
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class DocumentTemplateRead(BaseModel):
    """Full document template with lines, returned by CRUD endpoints."""

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    applies_to: DocumentTemplateScope
    lines: list[DocumentTemplateLineRead] = []
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Content Block
# ---------------------------------------------------------------------------


class ContentBlockWrite(BaseModel):
    """Request body for POST / PUT /api/v1/content-blocks."""

    kind: ContentBlockKind
    name: str = Field(min_length=1)
    body: str = Field(min_length=1)
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("body")
    @classmethod
    def strip_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("body must not be blank")
        return v


class ContentBlockRead(BaseModel):
    """Full content block returned by CRUD endpoints."""

    id: uuid.UUID
    company_id: uuid.UUID
    kind: ContentBlockKind
    name: str
    body: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Note Template
# ---------------------------------------------------------------------------


class NoteTemplateWrite(BaseModel):
    """Request body for POST / PUT /api/v1/note-templates."""

    name: str = Field(min_length=1)
    body: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("body")
    @classmethod
    def strip_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("body must not be blank")
        return v


class NoteTemplateRead(BaseModel):
    """Full note template returned by CRUD endpoints."""

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    body: str
    created_at: datetime
    updated_at: datetime
