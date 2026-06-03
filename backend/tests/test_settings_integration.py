"""Integration tests for settings service — real PostgreSQL required.

These tests verify actual SQL behaviour (upsert, unique index, CHECK
constraint, ``updated_at`` on upsert) against a running database.

Marked ``@pytest.mark.integration`` — skipped by default.
Run with ``pytest -m integration``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models._enums import SettingLevel
from jai.schemas.setting import SETTING_KEY_ONBOARDING_COMPLETED, OnboardingState
from jai.services.settings import _cache, get_setting, set_setting


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _cache.clear()
    yield
    _cache.clear()


@pytest.mark.integration
class TestSettingsIntegration:
    """Verify real DB behaviour: upsert, constraints, timestamps."""

    async def test_upsert_updates_value(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Writing the same key twice updates rather than errors."""
        async with db_session_maker() as session:
            await set_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=False),
                level=SettingLevel.GLOBAL,
            )
            await session.commit()

        async with db_session_maker() as session:
            await set_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=True),
                level=SettingLevel.GLOBAL,
            )
            await session.commit()

        async with db_session_maker() as session:
            result = await get_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )

        assert result is not None
        assert result.completed is True

    async def test_updated_at_advances_on_upsert(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """``updated_at`` should change on the second write."""
        import asyncio

        async with db_session_maker() as session:
            await set_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=False),
                level=SettingLevel.GLOBAL,
            )
            await session.commit()

            row = (
                await session.execute(
                    text(
                        "SELECT created_at, updated_at FROM setting "
                        "WHERE key = :key"
                    ),
                    {"key": SETTING_KEY_ONBOARDING_COMPLETED},
                )
            ).one()
            created, updated = row
            assert created == updated  # first write: same

        # Small delay so timestamps differ.
        await asyncio.sleep(0.05)

        async with db_session_maker() as session:
            await set_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=True),
                level=SettingLevel.GLOBAL,
            )
            await session.commit()

            row = (
                await session.execute(
                    text(
                        "SELECT created_at, updated_at FROM setting "
                        "WHERE key = :key"
                    ),
                    {"key": SETTING_KEY_ONBOARDING_COMPLETED},
                )
            ).one()
            created2, updated2 = row
            assert created2 == created  # created_at unchanged
            assert updated2 > updated  # updated_at advanced

    async def test_check_constraint_rejects_global_with_scope(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """DB CHECK should reject GLOBAL + scope_id."""
        from sqlalchemy.exc import IntegrityError

        async with db_session_maker() as session:
            with pytest.raises(IntegrityError, match="chk_setting_level_scope"):
                await session.execute(
                    text(
                        "INSERT INTO setting (id, level, scope_id, key, value) "
                        "VALUES (gen_random_uuid(), 'GLOBAL', :sid, 'test', '{}')"
                    ),
                    {"sid": uuid.uuid4()},
                )
                await session.commit()

    async def test_check_constraint_rejects_company_without_scope(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """DB CHECK should reject COMPANY + NULL scope_id."""
        from sqlalchemy.exc import IntegrityError

        async with db_session_maker() as session:
            with pytest.raises(IntegrityError, match="chk_setting_level_scope"):
                await session.execute(
                    text(
                        "INSERT INTO setting (id, level, scope_id, key, value) "
                        "VALUES (gen_random_uuid(), 'COMPANY', NULL, 'test', '{}')"
                    ),
                )
                await session.commit()

    async def test_unique_index_prevents_duplicate(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """NULLS NOT DISTINCT: two GLOBAL rows with same key violate unique."""
        from sqlalchemy.exc import IntegrityError

        async with db_session_maker() as session:
            await session.execute(
                text(
                    "INSERT INTO setting (id, level, scope_id, key, value) "
                    "VALUES (gen_random_uuid(), 'GLOBAL', NULL, 'dup', '{}')"
                ),
            )
            await session.commit()

        async with db_session_maker() as session:
            with pytest.raises(IntegrityError, match="uq_setting_level_scope_key"):
                await session.execute(
                    text(
                        "INSERT INTO setting (id, level, scope_id, key, value) "
                        "VALUES (gen_random_uuid(), 'GLOBAL', NULL, 'dup', '{}')"
                    ),
                )
                await session.commit()
