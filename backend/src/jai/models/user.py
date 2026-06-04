"""``User`` ORM model – fastapi-users base with MFA / company / role columns.

Design (M1):
  - Inherits from ``SQLAlchemyBaseUserTableUUID`` which provides the standard
    fastapi-users columns: ``id``, ``email``, ``hashed_password``, ``is_active``,
    ``is_superuser``, ``is_verified``.
  - **Reserved for M2+**: ``company_id`` (FK to be added when ``company`` table
    lands), ``role`` (text, RBAC to be implemented later).
  - **MFA (M1 step 3)**: ``totp_secret`` and ``mfa_enabled`` columns are created
    here so the migration is a single step.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Boolean, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Application user with MFA and multi-tenant reserved columns."""

    # -- Reserved for M2: company relationship + RBAC -----------------------
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="FK to company table (added in M2).",
    )
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="owner",
        server_default="owner",
    )

    # -- MFA (TOTP) ----------------------------------------------------------
    totp_secret: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # -- Timestamps ----------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
