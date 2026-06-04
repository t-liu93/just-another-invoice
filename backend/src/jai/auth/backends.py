"""Authentication backend configuration – Cookie transport + JWT strategy.

Two backends are defined:
- ``cookie_backend``: Full session (7 days) for normal authenticated access.
- ``pre_auth_backend``: Short-lived (5 minutes) for the MFA verification step.

Token isolation (Finding 1 fix):
  Each backend uses a **different JWT audience** so that tokens are not
  interchangeable.  A session token placed in the pre-auth cookie (or vice
  versa) will fail audience validation and be rejected.

Both use httpOnly cookies and ``SameSite=lax``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)

import jai.config as _cfg
from jai.auth.secret import get_auth_secret
from jai.models.user import User


def _get_cookie_transport(
    name: str,
    max_age: int,
) -> CookieTransport:
    """Create a ``CookieTransport`` with settings-driven security flags."""
    settings = _cfg.get_settings()
    return CookieTransport(
        cookie_name=name,
        cookie_max_age=max_age,
        cookie_secure=settings.cookie_secure,
        cookie_httponly=True,
        cookie_samesite="lax",
    )


def _get_jwt_strategy(
    lifetime_seconds: int,
    token_audience: list[str],
) -> JWTStrategy[User, uuid.UUID]:
    """Create a ``JWTStrategy`` with the app's resolved auth secret.

    The secret is read at call time so it reflects the value resolved during
    startup (env override or DB-generated); see :mod:`jai.auth.secret`.

    Parameters
    ----------
    lifetime_seconds:
        Token TTL in seconds.
    token_audience:
        JWT ``aud`` claim – **must differ** between session and pre-auth
        tokens to prevent cross-use (Finding 1 fix).
    """
    return JWTStrategy(
        secret=get_auth_secret(),
        lifetime_seconds=lifetime_seconds,
        token_audience=token_audience,
    )


# Session / pre-auth lifetimes are driven by config (env-overridable).
_settings = _cfg.get_settings()

# ---------------------------------------------------------------------------
# Full session backend (post-MFA)
# ---------------------------------------------------------------------------

_session_ttl = int(timedelta(days=_settings.session_ttl_days).total_seconds())

#: JWT audience for full session tokens.  Pre-auth tokens use a different
#: audience so the two are not interchangeable.
SESSION_AUDIENCE: list[str] = ["jai:session"]

cookie_backend = AuthenticationBackend(
    name="cookie",
    transport=_get_cookie_transport("jai_session", _session_ttl),
    get_strategy=lambda: _get_jwt_strategy(_session_ttl, SESSION_AUDIENCE),
)

# ---------------------------------------------------------------------------
# Pre-auth backend (post-password, awaiting MFA verification)
# ---------------------------------------------------------------------------

_pre_auth_ttl = int(timedelta(minutes=_settings.pre_auth_ttl_minutes).total_seconds())

PRE_AUTH_AUDIENCE: list[str] = ["jai:mfa"]

pre_auth_backend = AuthenticationBackend(
    name="pre_auth",
    transport=_get_cookie_transport("jai_pre_auth", _pre_auth_ttl),
    get_strategy=lambda: _get_jwt_strategy(_pre_auth_ttl, PRE_AUTH_AUDIENCE),
)
