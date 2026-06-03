"""Tests for ``jai.db`` – Base class, engine factories, and RLS placeholder hooks."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from jai.db import Base, get_engine, get_session_maker, reset_rls, set_rls_company

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class TestBase:
    """Verify the ORM base class is usable."""

    def test_base_is_declarative(self) -> None:
        """Base should have metadata (it's a DeclarativeBase)."""
        assert hasattr(Base, "metadata")

    def test_base_metadata_contains_models(self) -> None:
        """M1+ registers business tables in metadata."""
        # Alembic's alembic_version table is managed outside the ORM.
        assert "setting" in Base.metadata.tables


# ---------------------------------------------------------------------------
# Engine / session factory (no DB connection needed)
# ---------------------------------------------------------------------------


class TestEngineAndSessionFactory:
    """Verify the engine and session factory can be created from config."""

    def test_get_engine_returns_engine(self) -> None:
        """get_engine should return an AsyncEngine (created from config URL)."""
        engine = get_engine()
        assert engine is not None

    def test_get_session_maker_returns_factory(self) -> None:
        """get_session_maker should return a callable session factory."""
        maker = get_session_maker()
        assert callable(maker)


# ---------------------------------------------------------------------------
# RLS placeholder hooks
# ---------------------------------------------------------------------------


class TestRLSHooks:
    """RLS hooks are no-ops in M0; ensure they are callable without error."""

    async def test_set_rls_company_noop(self) -> None:
        """set_rls_company should not raise (placeholder)."""
        session = AsyncMock(spec=AsyncSession)
        await set_rls_company(session, uuid.uuid4())

    async def test_reset_rls_noop(self) -> None:
        """reset_rls should not raise (placeholder)."""
        session = AsyncMock(spec=AsyncSession)
        await reset_rls(session)
