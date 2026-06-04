"""Resolution of the JWT signing secret.

Priority (high → low):
  1. An explicit ``AUTH_SECRET`` environment variable — for operators who
     manage secrets externally (e.g. a secret manager).
  2. An auto-generated secret persisted in the ``setting`` table
     (``GLOBAL`` / ``auth.secret``).  Generated on first boot, unique per
     deployment, so self-hosters need zero configuration.

The resolved value is cached at process level.  It is populated once during
the application lifespan (see :func:`resolve_auth_secret`).  Until then —
and in unit tests / the CLI, which do not run the lifespan — callers fall
back to the configured :attr:`Settings.auth_secret` default so behaviour
stays consistent within a single process.

Losing or rotating this secret only invalidates existing session cookies;
users simply log in again.  No application data depends on it.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.config import get_settings
from jai.models._enums import SettingLevel
from jai.schemas.setting import SETTING_KEY_AUTH_SECRET, AuthSecretState
from jai.services.settings import get_setting, set_setting

logger = logging.getLogger("jai.auth.secret")

#: The placeholder shipped in code/``.env.example``; treated as "not set"
#: so it never silently signs production tokens.
_DEFAULT_SENTINEL = "change-me-in-production-use-at-least-32-chars"

#: Number of random bytes for an auto-generated secret (well above the
#: 32 bytes HMAC-SHA256 needs).
_SECRET_BYTES = 48

#: Process-level cache of the resolved secret.
_resolved_secret: str | None = None


def _env_override() -> str | None:
    """Return an explicit ``AUTH_SECRET`` override, or ``None`` if unset."""
    value = get_settings().auth_secret
    if value and value != _DEFAULT_SENTINEL:
        return value
    return None


def get_auth_secret() -> str:
    """Return the resolved JWT signing secret.

    Returns the value cached by :func:`resolve_auth_secret` when available;
    otherwise falls back to an explicit env override or the configured
    default (the path taken by unit tests and the CLI, which sign nothing
    that must survive across processes).
    """
    if _resolved_secret is not None:
        return _resolved_secret
    return _env_override() or get_settings().auth_secret


async def resolve_auth_secret(
    session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Resolve the auth secret once and cache it for the process.

    Called from the application lifespan after the migration check.  If an
    ``AUTH_SECRET`` env override is present it wins; otherwise the stored
    ``GLOBAL`` / ``auth.secret`` setting is used, generating and persisting a
    fresh random secret on first boot.
    """
    global _resolved_secret  # noqa: PLW0603

    override = _env_override()
    if override is not None:
        _resolved_secret = override
        logger.info("Auth secret loaded from AUTH_SECRET environment override.")
        return _resolved_secret

    async with session_maker() as session:
        state = await get_setting(
            session,
            SETTING_KEY_AUTH_SECRET,
            level=SettingLevel.GLOBAL,
            value_type=AuthSecretState,
        )
        if state is not None:
            _resolved_secret = state.secret
            logger.info("Auth secret loaded from database.")
            return _resolved_secret

        generated = secrets.token_urlsafe(_SECRET_BYTES)
        await set_setting(
            session,
            SETTING_KEY_AUTH_SECRET,
            AuthSecretState(secret=generated),
            level=SettingLevel.GLOBAL,
        )
        await session.commit()
        _resolved_secret = generated
        logger.info("Auth secret generated and persisted (first boot).")
        return _resolved_secret
