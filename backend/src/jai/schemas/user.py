"""Pydantic schemas for user authentication and registration.

These schemas extend the fastapi-users base schemas with JAI-specific fields
(role, mfa_enabled, company_id) that are exposed in API responses.
"""

from __future__ import annotations

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """User response schema – returned by ``GET /users/me`` and auth endpoints."""

    role: str = "owner"
    mfa_enabled: bool = False
    company_id: uuid.UUID | None = None


class UserCreate(schemas.BaseUserCreate):
    """User creation schema – used by ``POST /auth/register``.

    Inherits ``email`` and ``password`` from the base.  Role is set server-side
    (first registrant becomes ``owner``, subsequent registrations are blocked).
    """

    pass
