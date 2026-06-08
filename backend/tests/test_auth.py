"""Unit tests for authentication and MFA flows.

Covers:
- Bootstrap endpoint (registration open / closed, onboarding state)
- Registration gate (first user → owner + 201, second → 403)
- Login (two-step: password → pre-auth cookie → {next: mfa_setup|mfa_verify})
- MFA setup (generate pending TOTP secret)
- MFA verify (first binding → persist TOTP secret, subsequent login → verify)
- Onboarding: MFA verify does NOT complete onboarding; company first save does
- Logout (clears both cookies, public + idempotent)
- /users/me (authenticated → user data, unauthenticated → 401)
- TOTP code verification (valid / invalid / time-window tolerance)
- Pre-auth isolation (pre-auth cookie cannot access /me)
- CLI set-password / reset-mfa logic
- Password hashing / verification (Argon2)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import jai.auth.secret as secret_mod
import jai.services.mfa as mfa_svc
import jai.services.settings as settings_svc
from jai.auth.secret import get_auth_secret, resolve_auth_secret
from jai.auth.user_manager import hash_password, verify_password
from jai.config import get_settings
from jai.models._enums import SettingLevel
from jai.schemas.setting import SETTING_KEY_AUTH_SECRET, AuthSecretState

# ---------------------------------------------------------------------------
# MFA state cleanup (between tests)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_mfa_pending() -> Iterator[None]:
    """Reset MFA pending-secret store around each test."""
    mfa_svc._clear_pending_secrets()
    yield
    mfa_svc._clear_pending_secrets()


# ---------------------------------------------------------------------------
# Helpers – full authentication flow
# ---------------------------------------------------------------------------


async def _register(
    client: AsyncClient,
    email: str = "user@example.com",
    password: str = "testpassword1",
) -> None:
    """Register the first (owner) user."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201


async def _login(
    client: AsyncClient,
    email: str = "user@example.com",
    password: str = "testpassword1",
) -> None:
    """Login (password step) — sets pre-auth cookie on the client."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200


async def _mfa_setup(client: AsyncClient) -> str:
    """Call MFA setup and return the pending TOTP secret."""
    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    data = resp.json()
    assert "secret" in data
    assert "otpauth_uri" in data
    assert data["otpauth_uri"].startswith("otpauth://totp/")
    return data["secret"]


async def _mfa_verify(client: AsyncClient, code: str) -> int:
    """Call MFA verify and return the status code."""
    resp = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": code},
    )
    return resp.status_code


async def _full_auth(
    client: AsyncClient,
    email: str = "user@example.com",
    password: str = "testpassword1",
) -> str:
    """Register → login → MFA setup → MFA verify → return the TOTP secret."""
    await _register(client, email, password)
    await _login(client, email, password)
    secret = await _mfa_setup(client)
    code = pyotp.TOTP(secret).now()
    status_code = await _mfa_verify(client, code)
    assert status_code == 204
    return secret


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
# TOTP verification (unit)
# ---------------------------------------------------------------------------


class TestTotpVerification:
    """Unit tests for TOTP code generation and verification."""

    def test_valid_code_verifies(self) -> None:
        """A freshly generated code verifies against the same secret."""
        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        assert mfa_svc.verify_totp_code(secret, code) is True

    def test_invalid_code_fails(self) -> None:
        """A wrong code does not verify."""
        secret = pyotp.random_base32()
        assert mfa_svc.verify_totp_code(secret, "000000") is False

    def test_valid_window_one_accepts_adjacent(self) -> None:
        """With valid_window=1, the previous/next step codes are accepted."""
        import time

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        prev_code = totp.at(time.time() - 30)
        assert mfa_svc.verify_totp_code(secret, prev_code, valid_window=1) is True

    def test_valid_window_zero_rejects_adjacent(self) -> None:
        """With valid_window=0, only the exact current step is accepted."""
        import time

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        prev_code = totp.at(time.time() - 30)
        assert mfa_svc.verify_totp_code(secret, prev_code, valid_window=0) is False

    def test_totally_wrong_code(self) -> None:
        """A completely wrong 6-digit code fails."""
        secret = pyotp.random_base32()
        # Generate a code then change it
        code = pyotp.TOTP(secret).now()
        bad_code = str((int(code) + 123456) % 1000000).zfill(6)
        # It's theoretically possible they collide but extremely unlikely.
        assert mfa_svc.verify_totp_code(secret, bad_code) is False


# ---------------------------------------------------------------------------
# Pending secret store
# ---------------------------------------------------------------------------


class TestPendingSecretStore:
    """Tests for the in-memory pending TOTP secret store."""

    def test_store_and_get(self) -> None:
        mfa_svc.store_pending_secret("uid-1", "secret-abc")
        assert mfa_svc.get_pending_secret("uid-1") == "secret-abc"

    def test_get_nonexistent(self) -> None:
        assert mfa_svc.get_pending_secret("no-such-id") is None

    def test_pop_removes(self) -> None:
        mfa_svc.store_pending_secret("uid-2", "secret-xyz")
        assert mfa_svc.pop_pending_secret("uid-2") == "secret-xyz"
        assert mfa_svc.get_pending_secret("uid-2") is None

    def test_pop_nonexistent(self) -> None:
        assert mfa_svc.pop_pending_secret("nope") is None

    def test_get_does_not_remove(self) -> None:
        mfa_svc.store_pending_secret("uid-3", "secret-123")
        mfa_svc.get_pending_secret("uid-3")
        assert mfa_svc.get_pending_secret("uid-3") == "secret-123"

    def test_overwrite(self) -> None:
        mfa_svc.store_pending_secret("uid-4", "old")
        mfa_svc.store_pending_secret("uid-4", "new")
        assert mfa_svc.get_pending_secret("uid-4") == "new"


# ---------------------------------------------------------------------------
# Bootstrap endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
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
    async def test_bootstrap_after_mfa_onboarding_not_completed(
        self, db_client: AsyncClient
    ) -> None:
        """After full auth flow (register + MFA), registration is closed but
        onboarding is NOT completed — onboarding is only completed when the
        company profile is first saved (M2 step 4).
        """
        await _full_auth(db_client)

        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registration_open"] is False
        assert data["onboarding_completed"] is False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.integration
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
        await _register(db_client)

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
        uuid.UUID(data["id"])

    @pytest.mark.asyncio
    async def test_concurrent_first_registration_creates_one_owner(
        self, db_client: AsyncClient
    ) -> None:
        """Two concurrent first registrations must yield exactly one owner."""
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
# Login (two-step)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLogin:
    """Tests for ``POST /api/v1/auth/login`` (step 3: password → pre-auth)."""

    @pytest.mark.asyncio
    async def test_login_returns_mfa_setup_for_new_user(
        self, db_client: AsyncClient
    ) -> None:
        """New user (no MFA) gets {next: "mfa_setup"} and pre-auth cookie."""
        await _register(db_client)

        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["next"] == "mfa_setup"
        # Pre-auth cookie should be set.
        assert "jai_pre_auth" in resp.cookies

    @pytest.mark.asyncio
    async def test_login_returns_mfa_verify_after_binding(
        self, db_client: AsyncClient
    ) -> None:
        """User with MFA bound gets {next: "mfa_verify"}."""
        await _full_auth(db_client)
        # Logout to clear session.
        await db_client.post("/api/v1/auth/logout")

        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["next"] == "mfa_verify"

    @pytest.mark.asyncio
    async def test_login_wrong_password(
        self, db_client: AsyncClient
    ) -> None:
        """Wrong password returns 400."""
        await _register(db_client)

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
# MFA setup
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMfaSetup:
    """Tests for ``POST /api/v1/auth/mfa/setup``."""

    @pytest.mark.asyncio
    async def test_setup_returns_secret_and_uri(
        self, db_client: AsyncClient
    ) -> None:
        """Setup returns a valid secret and otpauth URI."""
        await _register(db_client)
        await _login(db_client)

        resp = await db_client.post("/api/v1/auth/mfa/setup")
        assert resp.status_code == 200
        data = resp.json()
        assert "secret" in data
        assert "otpauth_uri" in data
        assert data["otpauth_uri"].startswith("otpauth://totp/")
        assert "JAI" in data["otpauth_uri"]
        assert "user%40example.com" in data["otpauth_uri"]

    @pytest.mark.asyncio
    async def test_setup_without_pre_auth_returns_401(
        self, db_client: AsyncClient
    ) -> None:
        """Calling setup without pre-auth cookie returns 401."""
        await _register(db_client)
        # No login — no pre-auth cookie.

        resp = await db_client.post("/api/v1/auth/mfa/setup")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_setup_stores_pending_secret(
        self, db_client: AsyncClient
    ) -> None:
        """After setup, a pending secret is stored for the user."""
        await _register(db_client)
        await _login(db_client)

        resp = await db_client.post("/api/v1/auth/mfa/setup")
        secret = resp.json()["secret"]
        # There should be exactly one pending secret stored.
        assert len(mfa_svc._pending_totp_secrets) == 1
        stored = list(mfa_svc._pending_totp_secrets.values())[0]
        assert stored == secret


# ---------------------------------------------------------------------------
# MFA verify
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMfaVerify:
    """Tests for ``POST /api/v1/auth/mfa/verify``."""

    @pytest.mark.asyncio
    async def test_first_binding_persists_secret(
        self, db_client: AsyncClient
    ) -> None:
        """First MFA binding persists totp_secret and mfa_enabled.

        Onboarding is NOT completed by MFA verify — it is completed when the
        company profile is first saved (M2 step 4).
        """
        await _register(db_client)
        await _login(db_client)
        secret = await _mfa_setup(db_client)

        code = pyotp.TOTP(secret).now()
        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": code},
        )
        assert resp.status_code == 204
        # Pending secret should be consumed.
        assert len(mfa_svc._pending_totp_secrets) == 0

        # Session cookie should be set.
        assert "jai_session" in resp.cookies

        # /users/me should show mfa_enabled=True.
        me_resp = await db_client.get("/api/v1/users/me")
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["mfa_enabled"] is True

        # Onboarding should NOT be completed yet (only company save sets it).
        bootstrap = await db_client.get("/api/v1/auth/bootstrap")
        assert bootstrap.json()["onboarding_completed"] is False

    @pytest.mark.asyncio
    async def test_subsequent_login_with_valid_code(
        self, db_client: AsyncClient
    ) -> None:
        """Subsequent login verifies against stored secret."""
        secret = await _full_auth(db_client)
        # Logout.
        await db_client.post("/api/v1/auth/logout")

        # Login again → mfa_verify.
        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        assert resp.json()["next"] == "mfa_verify"

        # Verify with current code.
        code = pyotp.TOTP(secret).now()
        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": code},
        )
        assert resp.status_code == 204

        # Should be able to access /me.
        me_resp = await db_client.get("/api/v1/users/me")
        assert me_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_code_returns_400(
        self, db_client: AsyncClient
    ) -> None:
        """Wrong TOTP code returns 400."""
        await _register(db_client)
        await _login(db_client)
        await _mfa_setup(db_client)

        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "000000"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_without_pre_auth_returns_401(
        self, db_client: AsyncClient
    ) -> None:
        """Calling verify without pre-auth cookie returns 401."""
        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "123456"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_without_setup_returns_400(
        self, db_client: AsyncClient
    ) -> None:
        """Calling verify (first binding) without prior setup returns 400."""
        await _register(db_client)
        await _login(db_client)
        # No setup call — no pending secret.

        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "123456"},
        )
        assert resp.status_code == 400
        assert "pending" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_wrong_code_does_not_consume_pending_secret(
        self, db_client: AsyncClient
    ) -> None:
        """A wrong code does not remove the pending secret (allows retry)."""
        await _register(db_client)
        await _login(db_client)
        secret = await _mfa_setup(db_client)

        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": "000000"},
        )
        assert resp.status_code == 400
        # Pending secret should still be there.
        assert len(mfa_svc._pending_totp_secrets) == 1

        # Retry with correct code.
        code = pyotp.TOTP(secret).now()
        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": code},
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Pre-auth isolation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPreAuthIsolation:
    """Pre-auth cookie must not grant access to protected endpoints."""

    @pytest.mark.asyncio
    async def test_pre_auth_cannot_access_me(
        self, db_client: AsyncClient
    ) -> None:
        """Having only a pre-auth cookie does not grant access to /me."""
        await _register(db_client)
        await _login(db_client)

        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_session_cookie_does_not_work_for_mfa_setup(
        self, db_client: AsyncClient
    ) -> None:
        """A full session cookie cannot be used to call MFA setup."""
        await _full_auth(db_client)

        resp = await db_client.post("/api/v1/auth/mfa/setup")
        # MFA setup reads jai_pre_auth cookie, not jai_session.
        # Since we have a session cookie but no pre-auth cookie, it should 401.
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_pre_auth_token_in_session_cookie_is_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """Putting a pre-auth token into the session cookie must 401 (Finding 1).

        The two token types use different JWT audiences; swapping them must fail.
        """
        await _register(db_client)
        await _login(db_client)

        # Extract the pre-auth token from the cookie jar.
        transport_name = "jai_pre_auth"
        pre_auth_token = db_client.cookies.get(transport_name)
        assert pre_auth_token is not None, "Pre-auth cookie should be set after login."

        # Forge: put the pre-auth token into the session cookie slot.
        db_client.cookies.set("jai_session", pre_auth_token)
        # Remove the pre-auth cookie so only the forged session cookie is sent.
        db_client.cookies.delete(transport_name)

        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401, (
            "Pre-auth token must not be accepted as session token (audience isolation)."
        )

    @pytest.mark.asyncio
    async def test_session_token_in_pre_auth_cookie_is_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """Putting a session token into the pre-auth cookie must 401 (Finding 1)."""
        await _full_auth(db_client)

        # Extract the session token.
        session_token = db_client.cookies.get("jai_session")
        assert session_token is not None, "Session cookie should be set after full auth."

        # Forge: put the session token into the pre-auth cookie slot.
        db_client.cookies.set("jai_pre_auth", session_token)
        db_client.cookies.delete("jai_session")

        resp = await db_client.post("/api/v1/auth/mfa/setup")
        assert resp.status_code == 401, (
            "Session token must not be accepted as pre-auth token (audience isolation)."
        )


# ---------------------------------------------------------------------------
# Token audience isolation (Finding 1 – cross-use regression)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTokenAudienceIsolation:
    """Regression tests for Finding 1: pre-auth / session tokens must not be
    interchangeable.  Both directions must fail even though they share the
    same signing secret.
    """

    @pytest.mark.asyncio
    async def test_pre_auth_rejected_by_session_endpoint(
        self, db_client: AsyncClient
    ) -> None:
        """A pre-auth JWT used as a session cookie → 401 on /me."""
        await _register(db_client)
        await _login(db_client)

        pre_auth_token = db_client.cookies.get("jai_pre_auth")
        assert pre_auth_token is not None

        # Replace session cookie with the pre-auth token.
        db_client.cookies.set("jai_session", pre_auth_token)
        db_client.cookies.delete("jai_pre_auth")

        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_session_rejected_by_pre_auth_endpoint(
        self, db_client: AsyncClient
    ) -> None:
        """A session JWT used as a pre-auth cookie → 401 on /mfa/setup."""
        await _full_auth(db_client)

        session_token = db_client.cookies.get("jai_session")
        assert session_token is not None

        db_client.cookies.set("jai_pre_auth", session_token)
        db_client.cookies.delete("jai_session")

        resp = await db_client.post("/api/v1/auth/mfa/setup")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# MFA-aware dependency (Finding 2 – reset-mfa / step-2 legacy sessions)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMfaAwareDependency:
    """Regression tests for Finding 2: protected endpoints reject sessions
    where MFA is not fully configured.
    """

    @pytest.mark.asyncio
    async def test_session_without_mfa_rejected(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A valid session with mfa_enabled=False is rejected by /me.

        Simulates a Step 2-era session that was issued before MFA existed.
        """
        await _register(db_client)
        await _login(db_client)

        # Bypass MFA: directly create a session cookie via the strategy.
        from sqlalchemy import select

        from jai.auth.backends import cookie_backend
        from jai.models.user import User

        # Get the user from DB.
        async with db_session_maker() as session:
            stmt = select(User).where(User.email == "user@example.com")
            result = await session.execute(stmt)
            user = result.scalar_one()

        # Build a session token without going through MFA.
        strategy = cookie_backend.get_strategy()
        token = await strategy.write_token(user)
        db_client.cookies.set("jai_session", token)
        # Remove pre-auth if any.
        db_client.cookies.delete("jai_pre_auth")

        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401, (
            "Session without MFA completed must be rejected (Finding 2)."
        )

    @pytest.mark.asyncio
    async def test_session_after_reset_mfa_rejected(
        self, db_client: AsyncClient, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """After reset-mfa, an existing session cookie is rejected by /me."""
        await _full_auth(db_client)

        # Verify access works before reset.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 200

        # Simulate CLI reset-mfa.
        async with db_session_maker() as session:
            from sqlalchemy import select

            from jai.models.user import User

            stmt = select(User).where(User.email == "user@example.com")
            result = await session.execute(stmt)
            user = result.scalar_one()
            user.totp_secret = None
            user.mfa_enabled = False
            session.add(user)
            await session.commit()

        # The same session cookie should now be rejected.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401, (
            "Session must be rejected after MFA reset (Finding 2)."
        )


@pytest.mark.integration
class TestLogout:
    """Tests for ``POST /api/v1/auth/logout``."""

    @pytest.mark.asyncio
    async def test_logout_clears_cookies(
        self, db_client: AsyncClient
    ) -> None:
        """Logout clears both session and pre-auth cookies."""
        await _full_auth(db_client)

        resp = await db_client.post("/api/v1/auth/logout")
        assert resp.status_code == 204

        # After logout, /me should return 401.
        me_resp = await db_client.get("/api/v1/users/me")
        assert me_resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_unauthenticated_is_idempotent(
        self, client: AsyncClient
    ) -> None:
        """Logout without a session still returns 204 and clears cookies."""
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 204
        # A cookie-clearing Set-Cookie (max-age=0) must be present.
        set_cookie = resp.headers.get("set-cookie", "")
        assert "jai_session=" in set_cookie
        assert "max-age=0" in set_cookie.lower().replace(" ", "")


# ---------------------------------------------------------------------------
# /users/me
# ---------------------------------------------------------------------------


@pytest.mark.integration
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
        """Authenticated request (after full MFA flow) returns the user."""
        await _full_auth(db_client)

        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "user@example.com"
        assert data["role"] == "owner"
        assert data["mfa_enabled"] is True

    @pytest.mark.asyncio
    async def test_me_after_logout(
        self, db_client: AsyncClient
    ) -> None:
        """After logout, /me returns 401."""
        await _full_auth(db_client)
        await db_client.post("/api/v1/auth/logout")

        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Reset MFA (simulates CLI reset-mfa)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestResetMfa:
    """Tests for the MFA reset behaviour (what the CLI does)."""

    @pytest.mark.asyncio
    async def test_reset_mfa_forces_rebinding(
        self, db_client: AsyncClient, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """After reset-mfa, the user must go through MFA setup again."""
        secret = await _full_auth(db_client)

        # Simulate CLI reset-mfa: clear totp_secret + mfa_enabled.
        async with db_session_maker() as session:
            from sqlalchemy import select

            from jai.models.user import User

            stmt = select(User).where(User.email == "user@example.com")
            result = await session.execute(stmt)
            user = result.scalar_one()
            user.totp_secret = None  # type: ignore[assignment]
            user.mfa_enabled = False  # type: ignore[assignment]
            session.add(user)
            await session.commit()

        # Logout and login again.
        await db_client.post("/api/v1/auth/logout")
        resp = await db_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "testpassword1"},
        )
        assert resp.json()["next"] == "mfa_setup"

        # Old secret should no longer work for verify (no pending secret).
        old_code = pyotp.TOTP(secret).now()
        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": old_code},
        )
        assert resp.status_code == 400  # No pending setup.

        # Must go through setup again.
        new_secret = await _mfa_setup(db_client)
        new_code = pyotp.TOTP(new_secret).now()
        resp = await db_client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": new_code},
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Auth secret resolution (DB-backed JWT signing secret)
# ---------------------------------------------------------------------------


@pytest.mark.integration
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


# ---------------------------------------------------------------------------
# Onboarding flow (M2 step 4)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOnboardingFlow:
    """Tests for the onboarding flow: MFA verify does NOT complete onboarding;
    company first save does.

    This verifies the M2 step 4 change: the ``onboarding.completed`` flag's
    "set-true" point moved from MFA first binding to company first save.
    """

    @pytest.mark.asyncio
    async def test_mfa_verify_does_not_set_onboarding_completed(
        self, db_client: AsyncClient
    ) -> None:
        """After MFA first binding, onboarding_completed is still False."""
        await _full_auth(db_client)

        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["onboarding_completed"] is False

    @pytest.mark.asyncio
    async def test_company_first_save_sets_onboarding_completed(
        self, db_client: AsyncClient
    ) -> None:
        """After first company save, onboarding_completed becomes True."""
        await _full_auth(db_client)

        # Before company save: onboarding not completed.
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["onboarding_completed"] is False

        # First company save.
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "Acme BV", "base_currency": "EUR"},
        )
        assert resp.status_code == 200

        # After company save: onboarding completed.
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["onboarding_completed"] is True

    @pytest.mark.asyncio
    async def test_onboarding_completed_idempotent_on_company_update(
        self, db_client: AsyncClient
    ) -> None:
        """Updating company after first save keeps onboarding_completed True."""
        await _full_auth(db_client)

        # First save.
        await db_client.put(
            "/api/v1/company",
            json={"name": "First", "base_currency": "EUR"},
        )

        # Update.
        await db_client.put(
            "/api/v1/company",
            json={"name": "Updated", "base_currency": "USD"},
        )

        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["onboarding_completed"] is True

    @pytest.mark.asyncio
    async def test_company_first_save_links_owner(
        self, db_client: AsyncClient
    ) -> None:
        """First company save links the owner's company_id."""
        await _full_auth(db_client)

        # Before: no company_id.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.json()["company_id"] is None

        # First save.
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "Acme BV", "base_currency": "EUR"},
        )
        company_id = resp.json()["id"]

        # After: company_id set.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.json()["company_id"] == company_id

    @pytest.mark.asyncio
    async def test_full_onboarding_journey(
        self, db_client: AsyncClient
    ) -> None:
        """End-to-end: register → MFA → company save → onboarding completed.

        This mirrors the actual user journey through the onboarding wizard:
        1. Register (bootstrap: registration_open=True, onboarding_completed=False)
        2. Login → MFA setup → MFA verify
        3. Company profile save (onboarding_completed becomes True)
        4. Bootstrap reflects the completed state
        """
        # Step 1: Fresh boot.
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json() == {
            "registration_open": True,
            "onboarding_completed": False,
        }

        # Step 2: Register.
        await _register(db_client)
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["registration_open"] is False
        assert resp.json()["onboarding_completed"] is False

        # Step 3: Login + MFA.
        await _login(db_client)
        secret = await _mfa_setup(db_client)
        code = pyotp.TOTP(secret).now()
        status_code = await _mfa_verify(db_client, code)
        assert status_code == 204

        # MFA done, but onboarding NOT completed.
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["onboarding_completed"] is False

        # Step 4: Save company.
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "Onboarding Co", "base_currency": "EUR"},
        )
        assert resp.status_code == 200

        # Now onboarding is completed.
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json() == {
            "registration_open": False,
            "onboarding_completed": True,
        }
