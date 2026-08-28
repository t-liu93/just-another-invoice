"""Async database engine, session factory, and ORM base class.

The module exposes:

- ``Base`` – the SQLAlchemy declarative base that all ORM models inherit from.
- ``get_engine`` / ``get_session_maker`` – lazy singletons (created on first call).
- ``get_session`` – a FastAPI dependency that yields an ``AsyncSession``.
- ``set_rls_company`` / ``reset_rls`` – transaction-local tenant context for
  RLS-protected document snapshots.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

import jai.config as _cfg

if TYPE_CHECKING:
    import uuid


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Base class for all ORM models."""


# ---------------------------------------------------------------------------
# Lazy singletons – created on first access, not at import time.
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the async engine, creating it on first call."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        settings = _cfg.get_settings()
        url = settings.database_url
        assert url is not None  # guaranteed by model_validator
        _engine = create_async_engine(url, future=True)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, creating it on first call."""
    global _session_maker  # noqa: PLW0603
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_maker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an ``AsyncSession``."""
    async with get_session_maker()() as session:
        yield session


# ---------------------------------------------------------------------------
# RLS session context
# ---------------------------------------------------------------------------

async def set_rls_company(session: AsyncSession, company_id: uuid.UUID) -> None:
    """Set the current tenant for row-level-security.

    The setting is transaction-local, so pooled connections cannot leak a
    previous tenant into the next request.
    """
    await session.execute(
        text("SELECT set_config('jai.company_id', :company_id, true)"),
        {"company_id": str(company_id)},
    )


async def reset_rls(session: AsyncSession) -> None:
    """Reset the RLS context after a request.

    ``RESET`` deliberately leaves PostgreSQL's custom GUC as an empty string.
    The policies therefore use ``NULLIF(current_setting(...), '')``.  Reset is
    still useful for long-lived explicit transactions and documents the pool
    boundary; callers must set a company again for every transaction.
    """
    await session.execute(text("RESET jai.company_id"))
