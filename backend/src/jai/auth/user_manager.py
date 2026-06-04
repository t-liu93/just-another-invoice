"""Authentication database adapter and user manager for fastapi-users.

This module wires up:
- ``SQLAlchemyUserDatabase`` – adapts the ``User`` ORM model for fastapi-users.
- ``UserManager`` – handles password hashing (Argon2), registration gate
  (onboarding check), and user lifecycle hooks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi_users import BaseUserManager
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import InvalidPasswordException
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.secret import get_auth_secret
from jai.db import get_session
from jai.models.user import User
from jai.schemas.user import UserCreate

# ---------------------------------------------------------------------------
# Password hashing – Argon2 via pwdlib
# ---------------------------------------------------------------------------

_hasher = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2."""
    return _hasher.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against an Argon2 hash.

    Returns ``False`` for wrong passwords or unrecognisable hashes
    (e.g. corrupted data) instead of raising.
    """
    try:
        result = _hasher.verify(plain, hashed)
    except Exception:
        # pwdlib raises UnknownHashError for unrecognised hash formats.
        return False
    return result is True


# ---------------------------------------------------------------------------
# User manager
# ---------------------------------------------------------------------------


class UserManager(BaseUserManager[User, uuid.UUID]):
    """Custom user manager for JAI.

    Handles password hashing/verification and registration-time logic
    (role assignment, onboarding state).
    """

    reset_password_token_secret = property(lambda self: get_auth_secret())
    verification_token_secret = property(lambda self: get_auth_secret())

    def parse_id(self, value: str) -> uuid.UUID:
        """Parse a string value into a UUID user identifier."""
        return uuid.UUID(value)

    async def on_after_register(
        self, user: User, request: object | None = None
    ) -> None:
        """Hook called after successful user registration.

        The first user to register automatically receives ``role='owner'``
        (the registration endpoint enforces the gate).  Future M2 logic
        will also create the company record here.
        """

    async def validate_password(
        self,
        password: str,
        user: UserCreate | User,  # type: ignore[override]
    ) -> None:
        """Basic password validation – at least 8 characters."""
        if len(password) < 8:
            raise InvalidPasswordException(
                reason="Password must be at least 8 characters long."
            )


async def get_user_db(
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[SQLAlchemyUserDatabase[User, uuid.UUID]]:
    """FastAPI dependency that yields the user database adapter."""
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[User, uuid.UUID] = Depends(get_user_db),
) -> AsyncIterator[UserManager]:
    """FastAPI dependency that yields the ``UserManager``."""
    yield UserManager(user_db)
