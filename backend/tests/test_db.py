"""Tests for ``jai.db`` – engine, session, Base, and RLS placeholder hooks."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.db import Base, get_engine, get_session_maker, reset_rls, set_rls_company

SessionMaker = async_sessionmaker[AsyncSession]

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class TestBase:
    """Verify the ORM base class is usable."""

    def test_base_is_declarative(self) -> None:
        """Base should have metadata (it's a DeclarativeBase)."""
        assert hasattr(Base, "metadata")

    def test_base_metadata_empty(self) -> None:
        """M0 has no business tables, so metadata should be empty."""
        # Alembic's alembic_version table is managed outside the ORM.
        assert len(Base.metadata.tables) == 0


# ---------------------------------------------------------------------------
# Engine / session integration (requires running Postgres)
# ---------------------------------------------------------------------------


class TestEngineAndSession:
    """Integration tests against a real PostgreSQL instance."""

    async def test_get_engine_returns_engine(self) -> None:
        """get_engine should return an AsyncEngine."""
        engine = get_engine()
        assert engine is not None

    async def test_get_session_maker_returns_factory(self) -> None:
        """get_session_maker should return a callable session factory."""
        maker = get_session_maker()
        assert callable(maker)

    async def test_select_one(self, db_session_maker: SessionMaker) -> None:
        """A session should be able to execute a trivial SQL query."""
        async with db_session_maker() as session:
            result = await session.execute(text("SELECT 1 AS val"))
            row = result.scalar_one()
            assert row == 1

    async def test_session_is_async(self, db_session_maker: SessionMaker) -> None:
        """Session should be an AsyncSession instance."""
        async with db_session_maker() as session:
            assert isinstance(session, AsyncSession)

    async def test_current_database(self, db_session_maker: SessionMaker) -> None:
        """Connected database should be 'jai' or a test database."""
        async with db_session_maker() as session:
            result = await session.execute(text("SELECT current_database()"))
            db_name = result.scalar_one()
            assert "jai" in db_name

    async def test_postgres_version(self, db_session_maker: SessionMaker) -> None:
        """Should be connected to PostgreSQL."""
        async with db_session_maker() as session:
            result = await session.execute(text("SELECT version()"))
            version = result.scalar_one()
            assert "PostgreSQL" in version


# ---------------------------------------------------------------------------
# RLS placeholder hooks
# ---------------------------------------------------------------------------


class TestRLSHooks:
    """RLS hooks are no-ops in M0; ensure they are callable without error."""

    async def test_set_rls_company_noop(self, db_session_maker: SessionMaker) -> None:
        """set_rls_company should not raise (placeholder)."""
        async with db_session_maker() as session:
            await set_rls_company(session, uuid.uuid4())

    async def test_reset_rls_noop(self, db_session_maker: SessionMaker) -> None:
        """reset_rls should not raise (placeholder)."""
        async with db_session_maker() as session:
            await reset_rls(session)
