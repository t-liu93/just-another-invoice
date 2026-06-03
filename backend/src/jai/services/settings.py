"""Three-layer settings service with typed access and in-process cache.

Public API
----------
- ``get_setting`` – retrieve a typed setting with ``user → company → global``
  fallback.
- ``set_setting`` – persist a Pydantic model as the setting value (upsert).

Design (red-line 5)
-------------------
All values go through Pydantic validation.  No raw ``'YES'/'NO'`` string
checks in calling code — the typed schemas guarantee the correct Python
types (``bool``, ``int``, nested models, …).

Cache
-----
An in-process ``dict`` cache stores *already-parsed* Pydantic objects keyed
by ``(level, scope_id, key)``.  Writes invalidate the affected entry so
subsequent reads always reflect the latest value.
"""

from __future__ import annotations

import uuid
from typing import Any, overload

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import SettingLevel
from jai.models.setting import Setting

# Fallback order: most specific first.
_FALLBACK_ORDER: list[SettingLevel] = [
    SettingLevel.USER,
    SettingLevel.COMPANY,
    SettingLevel.GLOBAL,
]

# In-process cache: (level, scope_id, key) → parsed Pydantic object.
# ``None`` is never cached (absent settings return ``None``).
_cache: dict[tuple[SettingLevel, uuid.UUID | None, str], Any] = {}


def _cache_key(
    level: SettingLevel,
    scope_id: uuid.UUID | None,
    key: str,
) -> tuple[SettingLevel, uuid.UUID | None, str]:
    return (level, scope_id, key)


@overload
async def get_setting[T: BaseModel](
    session: AsyncSession,
    key: str,
    *,
    level: SettingLevel,
    scope_id: uuid.UUID | None = None,
    value_type: type[T],
) -> T | None: ...


@overload
async def get_setting(
    session: AsyncSession,
    key: str,
    *,
    level: SettingLevel,
    scope_id: uuid.UUID | None = None,
    value_type: None = None,
) -> dict[str, Any] | None: ...


async def get_setting[T: BaseModel](
    session: AsyncSession,
    key: str,
    *,
    level: SettingLevel,
    scope_id: uuid.UUID | None = None,
    value_type: type[T] | None = None,
) -> T | dict[str, Any] | None:
    """Retrieve a typed setting, with level-based fallback.

    When *level* is ``USER`` or ``COMPANY``, the function walks the fallback
    chain (``USER → COMPANY → GLOBAL``) until a value is found.  When
    *level* is ``GLOBAL``, only the global slot is checked.

    Parameters
    ----------
    session:
        Database session.
    key:
        Setting key (e.g. ``"onboarding.completed"``).
    level:
        Most-specific level to start the lookup from.
    scope_id:
        Entity UUID for ``COMPANY`` / ``USER`` levels; ``None`` for ``GLOBAL``.
    value_type:
        Pydantic model class to parse the raw JSONB dict into.  If ``None``,
        the raw dict is returned.

    Returns
    -------
    Parsed Pydantic model instance or ``None`` if no setting found.
    """
    # Determine which levels to probe (in fallback order).
    start_idx = _FALLBACK_ORDER.index(level)
    levels_to_check = _FALLBACK_ORDER[start_idx:]

    for lvl in levels_to_check:
        # For levels other than the starting one, scope_id is None (GLOBAL)
        # or needs to be resolved (M2 will add the logic).  For now we only
        # probe GLOBAL when falling back beyond the starting level.
        sid = scope_id if lvl == level else None

        cache_hit = _cache.get(_cache_key(lvl, sid, key))
        if cache_hit is not None:
            return cache_hit  # type: ignore[no-any-return]

        row = await _fetch_row(session, lvl, sid, key)
        if row is not None:
            raw: dict[str, Any] = row
            parsed = value_type.model_validate(raw) if value_type is not None else raw
            _cache[_cache_key(lvl, sid, key)] = parsed
            return parsed

    return None


async def set_setting(
    session: AsyncSession,
    key: str,
    value: BaseModel,
    *,
    level: SettingLevel,
    scope_id: uuid.UUID | None = None,
) -> None:
    """Persist a typed setting value (upsert).

    Uses PostgreSQL ``INSERT … ON CONFLICT … DO UPDATE`` so that the unique
    constraint ``(level, scope_id, key)`` is never violated.

    The in-process cache entry is invalidated immediately so that subsequent
    ``get_setting`` calls will load the fresh value.

    Parameters
    ----------
    session:
        Database session.
    key:
        Setting key.
    value:
        A Pydantic ``BaseModel`` instance; ``model_dump()`` is stored as
        JSONB.
    level:
        Setting hierarchy level.
    scope_id:
        Entity UUID for non-global levels.

    Raises
    ------
    ValueError
        If ``level``/``scope_id`` combination is invalid.
    """
    _validate_level_scope(level, scope_id)
    raw = value.model_dump()
    stmt = pg_insert(Setting).values(
        level=level,
        scope_id=scope_id,
        key=key,
        value=raw,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["level", "scope_id", "key"],
        set_={"value": stmt.excluded.value, "updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.flush()

    # Invalidate cache for this slot.
    _cache.pop(_cache_key(level, scope_id, key), None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_level_scope(level: SettingLevel, scope_id: uuid.UUID | None) -> None:
    """Ensure ``level``/``scope_id`` combination is valid.

    Rules (mirrors the DB CHECK constraint ``chk_setting_level_scope``):
    - ``GLOBAL``  → ``scope_id`` must be ``None``.
    - ``COMPANY`` / ``USER`` → ``scope_id`` must not be ``None``.
    """
    if level == SettingLevel.GLOBAL:
        if scope_id is not None:
            msg = f"GLOBAL settings must have scope_id=None, got {scope_id}"
            raise ValueError(msg)
    elif scope_id is None:
        msg = f"{level.value} settings require a scope_id"
        raise ValueError(msg)


async def _fetch_row(
    session: AsyncSession,
    level: SettingLevel,
    scope_id: uuid.UUID | None,
    key: str,
) -> dict[str, Any] | None:
    """Load a single setting row's ``value`` column."""
    stmt = select(Setting.value).where(
        Setting.level == level,
        Setting.scope_id == scope_id if scope_id is not None else Setting.scope_id.is_(None),  # noqa: E501
        Setting.key == key,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row
