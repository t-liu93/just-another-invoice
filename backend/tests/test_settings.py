"""Tests for ``jai.services.settings`` – typed access, fallback, and caching.

All tests use mocks — no running PostgreSQL required.  The service logic
(fallback priority, cache, type parsing, upsert) is verified without
touching a real database.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from jai.models._enums import SettingLevel
from jai.schemas.setting import SETTING_KEY_ONBOARDING_COMPLETED, OnboardingState
from jai.services.settings import _cache, get_setting, set_setting

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear the settings cache before and after each test."""
    _cache.clear()
    yield
    _cache.clear()


def _make_fetch_mock(return_map: dict) -> AsyncMock:
    """Build an async mock for ``_fetch_row`` that returns values from a map.

    *return_map* keys are ``(level, scope_id)`` tuples; values are dicts
    (the JSONB payload).
    """

    async def _fetch(
        session: object,  # noqa: ARG001
        level: SettingLevel,
        scope_id: object,  # noqa: ARG001
        key: str,  # noqa: ARG001
    ) -> dict | None:  # type: ignore[type-arg]
        return return_map.get((level, scope_id))

    return AsyncMock(side_effect=_fetch)


# ---------------------------------------------------------------------------
# Basic typed read
# ---------------------------------------------------------------------------


class TestGetSetting:
    """Tests for ``get_setting``."""

    async def test_returns_parsed_value(self) -> None:
        """get_setting parses JSONB dict via Pydantic model."""
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.GLOBAL, None): {"completed": True}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )

        assert result is not None
        assert isinstance(result, OnboardingState)
        assert result.completed is True

    async def test_returns_none_when_missing(self) -> None:
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock({}),
        ):
            result = await get_setting(
                AsyncMock(),
                "nonexistent",
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )

        assert result is None

    async def test_returns_raw_dict_without_type(self) -> None:
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.GLOBAL, None): {"completed": False}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
            )

        assert isinstance(result, dict)
        assert result["completed"] is False

    async def test_default_value_parsed(self) -> None:
        """Pydantic default (completed=False) is applied when key absent."""
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.GLOBAL, None): {}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )

        assert result is not None
        assert result.completed is False


# ---------------------------------------------------------------------------
# Fallback priority: USER > COMPANY > GLOBAL
# ---------------------------------------------------------------------------


class TestFallback:
    """Verify the three-layer fallback chain."""

    async def test_user_overrides_company_and_global(self) -> None:
        user_id = uuid.uuid4()
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.USER, user_id): {"completed": False}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.USER,
                scope_id=user_id,
                value_type=OnboardingState,
            )

        assert result is not None
        assert result.completed is False  # USER-level value

    async def test_falls_back_to_company(self) -> None:
        user_id = uuid.uuid4()
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.COMPANY, None): {"completed": True}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.USER,
                scope_id=user_id,
                value_type=OnboardingState,
            )

        assert result is not None
        assert result.completed is True  # COMPANY-level fallback

    async def test_falls_back_to_global(self) -> None:
        user_id = uuid.uuid4()
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.GLOBAL, None): {"completed": True}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.USER,
                scope_id=user_id,
                value_type=OnboardingState,
            )

        assert result is not None
        assert result.completed is True  # GLOBAL-level fallback

    async def test_global_only_checks_global(self) -> None:
        """When level=GLOBAL, only the GLOBAL slot is checked."""
        user_id = uuid.uuid4()
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {(SettingLevel.USER, user_id): {"completed": True}}
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )

        assert result is None  # USER entry not checked

    async def test_all_levels_set_user_wins(self) -> None:
        """When all three levels have a value, USER takes priority."""
        user_id = uuid.uuid4()
        with patch(
            "jai.services.settings._fetch_row",
            new_callable=lambda: _make_fetch_mock(
                {
                    (SettingLevel.GLOBAL, None): {"completed": True},
                    (SettingLevel.COMPANY, None): {"completed": True},
                    (SettingLevel.USER, user_id): {"completed": False},
                }
            ),
        ):
            result = await get_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.USER,
                scope_id=user_id,
                value_type=OnboardingState,
            )

        assert result is not None
        assert result.completed is False  # USER wins


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class TestCache:
    """Verify in-process cache hit and write-time invalidation."""

    async def test_cache_hit_avoids_db_query(self) -> None:
        """Second get_setting uses cache without calling _fetch_row again."""
        call_count = 0

        async def _fetch(session: object, level: SettingLevel, scope_id: object, key: str) -> dict:  # type: ignore[type-arg]  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            return {"completed": True}

        with patch("jai.services.settings._fetch_row", side_effect=_fetch):
            session = AsyncMock()
            r1 = await get_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )
            assert call_count == 1
            assert r1 is not None

            r2 = await get_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )
            assert call_count == 1  # no additional fetch
            assert r2 is not None

    async def test_cache_invalidated_on_write(self) -> None:
        """set_setting invalidates cache; next get_setting re-fetches."""
        fetch_count = 0

        async def _fetch(session: object, level: SettingLevel, scope_id: object, key: str) -> dict:  # type: ignore[type-arg]  # noqa: ARG001
            nonlocal fetch_count
            fetch_count += 1
            return {"completed": True}

        with patch("jai.services.settings._fetch_row", side_effect=_fetch):
            session = AsyncMock()
            await get_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )
            assert fetch_count == 1

            # Write invalidates cache
            await set_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=True),
                level=SettingLevel.GLOBAL,
            )

            # Re-fetch
            await get_setting(
                session,
                SETTING_KEY_ONBOARDING_COMPLETED,
                level=SettingLevel.GLOBAL,
                value_type=OnboardingState,
            )
            assert fetch_count == 2


# ---------------------------------------------------------------------------
# set_setting
# ---------------------------------------------------------------------------


class TestSetSetting:
    """Tests for ``set_setting``."""

    async def test_calls_execute_and_flush(self) -> None:
        session = AsyncMock()

        await set_setting(
            session,
            SETTING_KEY_ONBOARDING_COMPLETED,
            OnboardingState(completed=True),
            level=SettingLevel.GLOBAL,
        )

        session.execute.assert_called_once()
        session.flush.assert_called_once()

    async def test_upsert_invalidates_cache(self) -> None:
        session = AsyncMock()

        # Pre-populate cache
        _cache[(SettingLevel.GLOBAL, None, SETTING_KEY_ONBOARDING_COMPLETED)] = OnboardingState(
            completed=False
        )

        await set_setting(
            session,
            SETTING_KEY_ONBOARDING_COMPLETED,
            OnboardingState(completed=True),
            level=SettingLevel.GLOBAL,
        )

        assert (SettingLevel.GLOBAL, None, SETTING_KEY_ONBOARDING_COMPLETED) not in _cache


# ---------------------------------------------------------------------------
# level / scope_id validation
# ---------------------------------------------------------------------------


class TestLevelScopeValidation:
    """Verify _validate_level_scope rejects invalid combinations."""

    async def test_global_rejects_scope_id(self) -> None:
        """GLOBAL level must have scope_id=None."""
        with pytest.raises(ValueError, match="GLOBAL"):
            await set_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=True),
                level=SettingLevel.GLOBAL,
                scope_id=uuid.uuid4(),
            )

    async def test_company_requires_scope_id(self) -> None:
        """COMPANY level must have a non-None scope_id."""
        with pytest.raises(ValueError, match="COMPANY"):
            await set_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=True),
                level=SettingLevel.COMPANY,
                scope_id=None,
            )

    async def test_user_requires_scope_id(self) -> None:
        """USER level must have a non-None scope_id."""
        with pytest.raises(ValueError, match="USER"):
            await set_setting(
                AsyncMock(),
                SETTING_KEY_ONBOARDING_COMPLETED,
                OnboardingState(completed=True),
                level=SettingLevel.USER,
                scope_id=None,
            )

    async def test_global_with_none_is_ok(self) -> None:
        """GLOBAL + scope_id=None should not raise."""
        session = AsyncMock()
        await set_setting(
            session,
            SETTING_KEY_ONBOARDING_COMPLETED,
            OnboardingState(completed=True),
            level=SettingLevel.GLOBAL,
        )
        session.execute.assert_called_once()

    async def test_user_with_scope_id_is_ok(self) -> None:
        """USER + scope_id=<uuid> should not raise."""
        session = AsyncMock()
        await set_setting(
            session,
            SETTING_KEY_ONBOARDING_COMPLETED,
            OnboardingState(completed=True),
            level=SettingLevel.USER,
            scope_id=uuid.uuid4(),
        )
        session.execute.assert_called_once()
