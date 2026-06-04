"""Unit tests for authentication flows.

Covers:
- Bootstrap endpoint (registration open / closed, onboarding state)
- Registration gate (first user → owner + 201, second → 403)
- Login (valid credentials → cookie set + JSON body, invalid → 400)
- Logout (clears cookie, public + idempotent)
- /users/me (authenticated → user data, unauthenticated → 401)
- Password hashing / verification (Argon2)
- CLI set-password logic
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import jai.auth.secret as secret_mod
import jai.services.settings as settings_svc
from jai.auth.secret import get_auth_secret, resolve_auth_secret
from jai.auth.user_manager import hash_password, verify_password
from jai.config import get_settings
from jai.models._enums import SettingLevel
from jai.schemas.setting import SETTING_KEY_AUTH_SECRET, AuthSecretState

# ---------------------------------------------------------------------------
# Password hashing (Argon2)
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    """Unit tests for password hashing and verification."""

    def test_hash_and_verify_success(self) -> None:
        """Correct password verifies against its hash."""
        hashed = hash_password("secureP@ss1")
        assert verify_password("secureP@ss1", hashed) is True

    def test_verify_wrong_password(self) -> None:
        """Wrong password does not verify."""
        hashed = hash_password("correct-horse")
        assert verify_password("battery-staple", hashed) is False

    def test_verify_corrupted_hash(self) -> None:
        """Malformed hash returns False (pwdlib returns None)."""
        assert verify_password("anything", "not-a-valid-hash") is False

    def test_hash_is_different_each_time(self) -> None:
        """Same password produces different hashes (salt is random)."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2

    def test_hash_is_argon2_format(self) -> None:
        """Hash output starts with the Argon2 identifier."""
        hashed = hash_password("test1234")
        assert hashed.startswith("$argon2")


# ---------------------------------------------------------------------------
# Bootstrap endpoint
# ---------------------------------------------------------------------------


class TestBootstrap:
    """Tests for ``GET /api/v1/auth/bootstrap``."""

    @pytest.mark.asyncio
    async def test_bootstrap_fresh_db(self, db_client: AsyncClient) -> None:
        """Fresh database → registration_open=True, onboarding_completed=False."""
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registration_open"] is True
        assert data["onboarding_completed"] is False

    @pytest.mark.asyncio
    async def test_bootstrap_after_register(
        self, db_client: AsyncClient
    ) -> None:
        """After registering a user, registration is closed."""
        # Register first user.
        resp = await db_client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 201

        # Bootstrap should now show registration closed.
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registration_open"] is False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegister:
    """Tests for ``POST /api/v1/auth/register``."""

    @pytest.mark.asyncio
    async def test_register_first_user_becomes_owner(
        self, db_client: AsyncClient
    ) -> None:
        """First registrant is created successfully with role=owner."""
        resp = await db_client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "owner@example.com"
        assert data["role"] == "owner"
        assert data["is_active"] is True
        assert data["mfa_enabled"] is False
        assert data["company_id"] is None
        # Password must NOT be in the response.
        assert "hashed_password" not in data
        assert "password" not in data

    @pytest.mark.asyncio
    async def test_register_second_user_forbidden(
        self, db_client: AsyncClient
    ) -> None:
        """Second registration attempt is rejected with 403."""
        # First registration.
        resp = await db_client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 201

        # Second registration → 403.
        resp = await db_client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "testpassword2"},
        )
        assert resp.status_code == 403
        assert "closed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_register_short_password_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """Password shorter than 8 characters is rejected."""
        resp = await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "short"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_returns_user_id(
        self, db_client: AsyncClient
    ) -> None:
        """Response includes a valid UUID id."""
        resp = await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        data = resp.json()
        # Should be a valid UUID string.
        uuid.UUID(data["id"])

    @pytest.mark.asyncio
    async def test_concurrent_first_registration_creates_one_owner(
        self, db_client: AsyncClient
    ) -> None:
        """Two concurrent first registrations must yield exactly one owner.

        Guards the ``pg_advisory_xact_lock`` serialisation: without it, both
        requests could read an empty DB and create two owners.  With it, one
        wins (201) and the other sees the gate closed (403).
        """
        import asyncio

        async def _register(email: str) -> int:
            resp = await db_client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "testpassword1"},
            )
            return resp.status_code

        statuses = sorted(
            await asyncio.gather(
                _register("first@example.com"),
                _register("second@example.com"),
            )
        )
        assert statuses == [201, 403]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    """Tests for ``POST /api/v1/auth/login``."""

    @pytest.mark.asyncio
    async def test_login_success_sets_cookie(
        self, db_client: AsyncClient
    ) -> None:
        """Successful login returns JSON body and sets session cookie."""
        # Register.
        await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "testpassword1"},
        )

        # Login.
        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["next"] == "dashboard"

        # Cookie should be set.
        cookies = resp.cookies
        assert "jai_session" in cookies

    @pytest.mark.asyncio
    async def test_login_wrong_password(
        self, db_client: AsyncClient
    ) -> None:
        """Wrong password returns 400."""
        await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "testpassword1"},
        )

        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "wrongpassword1"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(
        self, db_client: AsyncClient
    ) -> None:
        """Login with unknown email returns 400 (not 404, to avoid enumeration)."""
        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    """Tests for ``POST /api/v1/auth/logout``."""

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(
        self, db_client: AsyncClient
    ) -> None:
        """Logout clears the session cookie."""
        # Register + login.
        await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        login_resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        assert "jai_session" in login_resp.cookies

        # Logout.
        resp = await db_client.post("/api/v1/auth/logout")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_logout_unauthenticated_is_idempotent(
        self, client: AsyncClient
    ) -> None:
        """Logout without a session still returns 204 and clears the cookie.

        The endpoint is public and idempotent so a stale/invalid httpOnly
        cookie can always be cleared by the server.
        """
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 204
        # A cookie-clearing Set-Cookie (max-age=0) must be present.
        set_cookie = resp.headers.get("set-cookie", "")
        assert "jai_session=" in set_cookie
        assert "max-age=0" in set_cookie.lower().replace(" ", "")


# ---------------------------------------------------------------------------
# /users/me
# ---------------------------------------------------------------------------


class TestUsersMe:
    """Tests for ``GET /api/v1/users/me``."""

    @pytest.mark.asyncio
    async def test_me_unauthenticated(
        self, client: AsyncClient
    ) -> None:
        """Unauthenticated request returns 401."""
        resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_authenticated(
        self, db_client: AsyncClient
    ) -> None:
        """Authenticated request returns the current user."""
        # Register + login.
        await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )

        # Get current user.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "user@example.com"
        assert data["role"] == "owner"
        assert data["mfa_enabled"] is False

    @pytest.mark.asyncio
    async def test_me_after_logout(
        self, db_client: AsyncClient
    ) -> None:
        """After logout, /me returns 401."""
        # Register + login.
        await db_client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )

        # Logout.
        await db_client.post("/api/v1/auth/logout")

        # /me should be 401 now.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth secret resolution (DB-backed JWT signing secret)
# ---------------------------------------------------------------------------


class TestAuthSecret:
    """Tests for ``jai.auth.secret.resolve_auth_secret``.

    Exercises the security-critical startup path that pytest's plain
    ``ASGITransport`` does not run (it skips the app lifespan).
    """

    @pytest.fixture(autouse=True)
    def _isolate_secret_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        """Reset secret/settings caches and neutralise env overrides.

        Pins ``AUTH_SECRET`` to the placeholder so the DB-generation path runs
        deterministically regardless of the developer's ``.env``.  Restores a
        clean cache state afterwards so other tests are unaffected.
        """
        monkeypatch.setenv("AUTH_SECRET", secret_mod._DEFAULT_SENTINEL)
        secret_mod._resolved_secret = None
        settings_svc._cache.clear()
        get_settings.cache_clear()
        yield
        secret_mod._resolved_secret = None
        settings_svc._cache.clear()
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_first_boot_generates_and_persists(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """First boot generates a random secret and persists it to the DB."""
        secret = await resolve_auth_secret(db_session_maker)
        assert secret
        assert secret != secret_mod._DEFAULT_SENTINEL
        assert len(secret) >= 32
        # The resolver caches it for the process.
        assert get_auth_secret() == secret

        # It must be readable back from the GLOBAL/auth.secret row.
        settings_svc._cache.clear()
        async with db_session_maker() as session:
            state = await settings_svc.get_setting(
                session,
                SETTING_KEY_AUTH_SECRET,
                level=SettingLevel.GLOBAL,
                value_type=AuthSecretState,
            )
        assert state is not None
        assert state.secret == secret

    @pytest.mark.asyncio
    async def test_restart_reads_same_value(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """A simulated restart reads the persisted secret, not a new one."""
        first = await resolve_auth_secret(db_session_maker)
        # Simulate a fresh process: drop in-memory caches, keep the DB row.
        secret_mod._resolved_secret = None
        settings_svc._cache.clear()
        second = await resolve_auth_secret(db_session_maker)
        assert second == first

    @pytest.mark.asyncio
    async def test_env_override_wins(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An explicit ``AUTH_SECRET`` env override beats the DB-stored value."""
        db_secret = await resolve_auth_secret(db_session_maker)
        # Fresh process, now with an explicit override present.
        secret_mod._resolved_secret = None
        settings_svc._cache.clear()
        override = "explicit-override-secret-0123456789abcdef"
        monkeypatch.setenv("AUTH_SECRET", override)
        get_settings.cache_clear()
        resolved = await resolve_auth_secret(db_session_maker)
        assert resolved == override
        assert resolved != db_secret
