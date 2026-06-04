"""Authentication API routes – register, login, logout, bootstrap, /me.

The login flow is custom (JSON body instead of OAuth2 form) while leveraging
fastapi-users' ``JWTStrategy`` for token creation and ``CookieTransport``
parameters for cookie management.

Step 2 (this file):
  - Login: password correct → issue full session cookie directly.
  - Register: gated by ``is_registration_open`` (no users in DB).
  - Logout: clear session cookie.

Step 3 will change login to two-step (password → pre-auth → MFA verify → session).
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi_users.authentication.strategy.jwt import JWTStrategy
from fastapi_users.authentication.transport.cookie import CookieTransport
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.backends import cookie_backend
from jai.auth.deps import (
    current_active_user,
    is_onboarding_completed,
    is_registration_open,
)
from jai.auth.user_manager import UserManager, get_user_manager, verify_password
from jai.db import get_session
from jai.models.user import User
from jai.schemas.user import UserCreate, UserRead

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class BootstrapResponse(BaseModel):
    """Returned by ``GET /auth/bootstrap`` – drives frontend routing."""

    registration_open: bool
    onboarding_completed: bool


class RegisterRequest(BaseModel):
    """Body for ``POST /auth/register`` – only the fields a client may set.

    Defined locally (rather than reusing fastapi-users' ``BaseUserCreate``) so
    the public contract is exactly ``{email, password}`` and never exposes
    ``is_active`` / ``is_superuser`` / ``is_verified``.
    """

    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    """Body for ``POST /auth/login``."""

    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    """Returned by ``POST /auth/login``.

    Step 2 always returns ``{"next": "dashboard"}``.  Step 3 will narrow
    ``next`` to ``"mfa_setup" | "mfa_verify"`` once two-step login lands.
    """

    next: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

users_router = APIRouter(prefix="/api/v1/users", tags=["users"])

#: Stable advisory-lock key serialising the first-account registration gate.
#: Two concurrent first-boot registrations must not both pass the empty-DB
#: check and create two owners.
_REGISTRATION_LOCK_KEY = 4915_0001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_session_cookie(user: User, response: Response) -> str:
    """Create a JWT token and set it as a session cookie.

    Returns the raw token (rarely needed, but useful for testing).
    """
    strategy = cast(JWTStrategy[User, object], cookie_backend.get_strategy())
    token = await strategy.write_token(user)
    transport = cast(CookieTransport, cookie_backend.transport)
    response.set_cookie(
        transport.cookie_name,
        token,
        max_age=transport.cookie_max_age,
        path=transport.cookie_path,
        domain=transport.cookie_domain,
        secure=transport.cookie_secure,
        httponly=transport.cookie_httponly,
        samesite=transport.cookie_samesite,
    )
    return token


def _clear_session_cookie(response: Response) -> None:
    """Clear the session cookie."""
    transport = cast(CookieTransport, cookie_backend.transport)
    response.set_cookie(
        transport.cookie_name,
        "",
        max_age=0,
        path=transport.cookie_path,
        domain=transport.cookie_domain,
        secure=transport.cookie_secure,
        httponly=transport.cookie_httponly,
        samesite=transport.cookie_samesite,
    )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    session: AsyncSession = Depends(get_session),
) -> BootstrapResponse:
    """Return the app's bootstrap state for the frontend to decide routing."""
    return BootstrapResponse(
        registration_open=await is_registration_open(session),
        onboarding_completed=await is_onboarding_completed(session),
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    user_manager: UserManager = Depends(get_user_manager),
) -> UserRead:
    """Register a new user.  Only allowed when no users exist (first-boot)."""
    # Serialise the gate check + create against concurrent first registrations.
    # The transaction-scoped lock is released when ``user_manager.create``
    # commits (it shares this request's session), after which any waiter sees
    # the new owner and is rejected by the gate below.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)").bindparams(
            key=_REGISTRATION_LOCK_KEY
        )
    )
    if not await is_registration_open(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed.",
        )
    try:
        user = await user_manager.create(
            UserCreate(email=body.email, password=body.password), safe=True
        )
    except Exception as exc:
        # fastapi-users raises InvalidPasswordException / UserAlreadyExists
        # which we surface as 400.
        detail = getattr(exc, "reason", None) or str(exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from exc
    return UserRead.model_validate(user)


# ---------------------------------------------------------------------------
# Login (step 2: password → direct session cookie)
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(
    response: Response,
    body: LoginRequest,
    user_manager: UserManager = Depends(get_user_manager),
) -> LoginResponse:
    """Verify credentials and issue a full session cookie.

    Returns ``{"next": "dashboard"}`` on success (step 2 shortcut).
    Step 3 changes this to return ``{"next": "mfa_setup"|"mfa_verify"}``
    and issue a short-lived pre-auth cookie instead.
    """
    try:
        user = await user_manager.get_by_email(str(body.email))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials.",
        ) from None
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user.",
        )

    await _set_session_cookie(user, response)
    return LoginResponse(next="dashboard")


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clear the session cookie.

    Public and idempotent: it always returns a cookie-clearing response, even
    when the incoming cookie is missing, expired, or no longer verifiable
    (e.g. after a secret rotation).  Since the cookie is ``httpOnly`` the
    frontend cannot clear it on its own, so the server must do so
    unconditionally.
    """
    _clear_session_cookie(response)


# ---------------------------------------------------------------------------
# /users/me
# ---------------------------------------------------------------------------


@users_router.get("/me", response_model=UserRead)
async def me(
    user: Annotated[User, Depends(current_active_user)],
) -> UserRead:
    """Return the currently authenticated user."""
    return UserRead.model_validate(user)
