"""Authentication dependencies – current user extraction and helpers.

Provides:
- ``current_active_user``: FastAPI dependency that returns the authenticated
  user from the cookie session (or raises 401).
- ``current_mfa_user``: **MFA-aware** dependency – additionally requires
  ``mfa_enabled=True`` and a non-null ``totp_secret``.  All protected
  business endpoints must use this dependency (Finding 2 fix).
- ``is_registration_open``: Check whether public registration is allowed.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi_users import FastAPIUsers
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.backends import cookie_backend
from jai.auth.user_manager import get_user_manager
from jai.models._enums import SettingLevel
from jai.models.user import User
from jai.schemas.setting import SETTING_KEY_ONBOARDING_COMPLETED, OnboardingState
from jai.services.settings import get_setting

# ---------------------------------------------------------------------------
# FastAPIUsers central object
# ---------------------------------------------------------------------------

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [cookie_backend])

# Convenience dependency aliases.
current_active_user = fastapi_users.current_user(active=True)


# ---------------------------------------------------------------------------
# MFA-aware dependency (Finding 2 fix)
# ---------------------------------------------------------------------------


async def current_mfa_user(
    user: User = Depends(current_active_user),
) -> User:
    """Return the authenticated user **only if MFA is fully configured**.

    Use this for all protected business endpoints (``/users/me``, future
    invoices, settings, etc.).  Rejects sessions where MFA has not been
    completed or has been reset (``mfa_enabled=False`` or ``totp_secret``
    is ``None``).

    This also implicitly invalidates Step 2-era sessions that were issued
    before MFA was introduced — those users will need to log in again and
    complete the MFA flow.
    """
    if not user.mfa_enabled or not user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MFA verification required.",
        )
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def is_registration_open(session: AsyncSession) -> bool:
    """Return ``True`` if public registration is currently allowed.

    Registration is open when **no users exist** in the database (first-boot).
    Once the first owner registers, public registration is permanently closed.
    """
    stmt = select(func.count()).select_from(User)
    result = await session.execute(stmt)
    count = result.scalar_one()
    return count == 0


async def is_onboarding_completed(session: AsyncSession) -> bool:
    """Return ``True`` if the initial owner onboarding has been completed."""
    state = await get_setting(
        session,
        SETTING_KEY_ONBOARDING_COMPLETED,
        level=SettingLevel.GLOBAL,
        value_type=OnboardingState,
    )
    return state.completed if state is not None else False
