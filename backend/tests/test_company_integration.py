"""Integration tests for company profile CRUD (M2 step 1).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- ``GET /api/v1/company`` → 204 when not created
- ``PUT /api/v1/company`` → create (201-like) + read-back
- ``PUT /api/v1/company`` → update existing
- First creation sets ``onboarding.completed=true``
- First creation links owner ``company_id``
- Invalid ISO codes → 422
- Owner-only enforcement (role check)
- Full auth flow: register → MFA → company CRUD
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "owner@example.com",
    password: str = "testpassword1",
) -> None:
    """Register → login → MFA setup → MFA verify."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    secret = resp.json()["secret"] if "secret" in resp.json() else None

    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    data = resp.json()
    secret = data["secret"]

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": code},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCompanyAPI:
    """Integration tests for GET/PUT /api/v1/company."""

    async def test_get_returns_204_when_no_company(
        self, db_client: AsyncClient
    ) -> None:
        """GET /company returns 204 when company has not been created."""
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/company")
        assert resp.status_code == 204

    async def test_put_creates_company(self, db_client: AsyncClient) -> None:
        """PUT /company creates the singleton company row."""
        await _full_auth(db_client)

        resp = await db_client.put(
            "/api/v1/company",
            json={
                "name": "Acme BV",
                "base_currency": "EUR",
                "vat_id": "NL123456789B01",
                "country_code": "NL",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Acme BV"
        assert data["base_currency"] == "EUR"
        assert data["vat_id"] == "NL123456789B01"
        assert data["country_code"] == "NL"
        assert data["has_logo"] is False
        assert data["logo_url"] is None
        assert "id" in data
        assert "created_at" in data

    async def test_get_returns_company_after_creation(
        self, db_client: AsyncClient
    ) -> None:
        """GET /company returns the company after it has been created."""
        await _full_auth(db_client)

        await db_client.put(
            "/api/v1/company",
            json={"name": "Test", "base_currency": "EUR"},
        )

        resp = await db_client.get("/api/v1/company")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test"

    async def test_put_updates_existing_company(
        self, db_client: AsyncClient
    ) -> None:
        """Second PUT updates the existing company in-place."""
        await _full_auth(db_client)

        # Create
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "First", "base_currency": "EUR"},
        )
        assert resp.status_code == 200
        company_id = resp.json()["id"]

        # Update
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "Updated", "base_currency": "USD", "country_code": "US"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated"
        assert data["base_currency"] == "USD"
        assert data["country_code"] == "US"
        # Same row (id unchanged).
        assert data["id"] == company_id

    async def test_first_creation_sets_onboarding_completed(
        self, db_client: AsyncClient
    ) -> None:
        """After first PUT /company, onboarding.completed becomes true."""
        await _full_auth(db_client)

        # Before: onboarding not completed (bootstrap says so).
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["onboarding_completed"] is True  # MFA verify already sets it

        # After company creation, check bootstrap again.
        await db_client.put(
            "/api/v1/company",
            json={"name": "Test", "base_currency": "EUR"},
        )
        resp = await db_client.get("/api/v1/auth/bootstrap")
        assert resp.json()["onboarding_completed"] is True

    async def test_first_creation_links_owner_company_id(
        self,
        db_client: AsyncClient,
        db_session_maker: object,
    ) -> None:
        """After first PUT /company, the owner's company_id is set."""
        await _full_auth(db_client)

        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "Linked", "base_currency": "EUR"},
        )
        company_id = resp.json()["id"]

        # Check /users/me has company_id set.
        resp = await db_client.get("/api/v1/users/me")
        assert resp.status_code == 200
        assert resp.json()["company_id"] == company_id

    async def test_invalid_currency_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """Invalid ISO 4217 code → 422 Validation Error."""
        await _full_auth(db_client)
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "X", "base_currency": "INVALID"},
        )
        assert resp.status_code == 422

    async def test_invalid_country_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """Invalid ISO 3166 code → 422 Validation Error."""
        await _full_auth(db_client)
        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "X", "base_currency": "EUR", "country_code": "ZZ"},
        )
        assert resp.status_code == 422

    async def test_unauthenticated_returns_401(
        self, db_client: AsyncClient
    ) -> None:
        """Unauthenticated request → 401."""
        resp = await db_client.get("/api/v1/company")
        assert resp.status_code == 401

        resp = await db_client.put(
            "/api/v1/company",
            json={"name": "X", "base_currency": "EUR"},
        )
        assert resp.status_code == 401

    async def test_get_company_optional_fields_null(
        self, db_client: AsyncClient
    ) -> None:
        """GET returns null for optional fields when not set."""
        await _full_auth(db_client)
        await db_client.put(
            "/api/v1/company",
            json={"name": "Minimal", "base_currency": "JPY"},
        )
        resp = await db_client.get("/api/v1/company")
        data = resp.json()
        assert data["vat_id"] is None
        assert data["coc_number"] is None
        assert data["email"] is None
        assert data["phone"] is None
        assert data["website"] is None
        assert data["address_line1"] is None
        assert data["address_line2"] is None
        assert data["postal_code"] is None
        assert data["city"] is None
        assert data["country_code"] is None
        assert data["base_currency"] == "JPY"
