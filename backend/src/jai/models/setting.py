"""``Setting`` ORM model – single-table, key-value with hierarchy level.

Design (M1):
  - Single table stores all settings across three levels: ``GLOBAL``,
    ``COMPANY``, and ``USER``.
  - ``scope_id`` is nullable: ``NULL`` for ``GLOBAL``, points to the
    company / user UUID for the other levels.  Foreign keys will be added
    in M2 when the ``company`` and ``user`` tables land.
  - ``value`` is JSONB so that structured settings (SMTP config, etc.) are
    stored alongside simple booleans without stringly-typed checks.
  - Unique constraint on ``(level, scope_id, key)`` guarantees at most one
    value per setting slot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from jai.db import Base
from jai.models._enums import SettingLevel


class Setting(Base):
    """A single configuration entry in the three-layer settings system."""

    __tablename__ = "setting"
    __table_args__ = (
        Index(
            "uq_setting_level_scope_key",
            "level",
            "scope_id",
            "key",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    level: Mapped[SettingLevel] = mapped_column(
        # Use VARCHAR/TEXT, not native PostgreSQL ENUM, so we don't need to
        # manage a separate DB enum type.  SQLAlchemy still converts between
        # Python SettingLevel values and their string representation.
        Enum(SettingLevel, native_enum=False),
        index=True,
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    key: Mapped[str] = mapped_column(
        index=True,
    )
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
