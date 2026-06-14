"""Integration tests for quote PDF rendering – M9 step 3.

Requires:
- A running PostgreSQL instance (creates a fresh test database).
- WeasyPrint system libraries (pango, cairo) installed.

Run with:
    pytest -m integration -k "quote or pdf"

Tests:
1. render_quote_pdf / download endpoint returns valid PDF bytes (starts with %PDF).
2. Quote PDF with locale=zh returns valid PDF.
3. Cross-company request (non-existent quote) returns 404.
"""

from __future__ import annotations

import datetime
import uuid

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared auth / setup helpers (same pattern as test_pdf_integration.py)
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "quotepdftest@example.com",
    password: str = "testpassword1",
) -> None:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200
    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": code})
    assert resp.status_code == 204


async def _setup_company(client: AsyncClient) -> dict:
    resp = await client.put(
        "/api/v1/company",
        json={
            "name": "Quote PDF Test BV",
            "base_currency": "EUR",
            "country_code": "NL",
        },
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = rates_resp.json()["items"]
    by_label = {r["label"]: r for r in rates}

    treatments_resp = await client.get("/api/v1/vat-treatments?side=SALES")
    treatments = treatments_resp.json()["items"]
    by_code = {t["code"]: t for t in treatments}

    return {"rates": by_label, "treatments": by_code}


async def _create_customer(client: AsyncClient, name: str = "Test Client BV") -> str:
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": name,
            "addresses": [
                {
                    "type": "BILLING",
                    "street": "Klantstraat",
                    "house_number": "1",
                    "postal_code": "1234 AB",
                    "city": "Amsterdam",
                    "country_code": "NL",
                }
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_quote(
    client: AsyncClient,
    customer_id: str,
    refs: dict,
) -> str:
    """Create a minimal quote and return its ID."""
    rate_21 = refs["rates"].get("NL standard (21%)") or next(
        (r for r in refs["rates"].values() if "21" in r["label"]), None
    )
    treatment = refs["treatments"].get("NL_DOMESTIC")
    assert rate_21 is not None, "21% VAT rate not found"
    assert treatment is not None, "NL_DOMESTIC treatment not found"

    today = datetime.date.today().isoformat()
    valid_until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()

    resp = await client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer_id,
            "quote_date": today,
            "valid_until": valid_until,
            "currency": "EUR",
            "tax_mode": "DOCUMENT",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment["id"],
            "document_vat_rate_id": rate_21["id"],
            "lines": [
                {
                    "name": "Consulting service",
                    "description": "Project consulting",
                    "quantity": "3",
                    "unit_price": "150.000",
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_quote_pdf_download_endpoint(db_client: AsyncClient) -> None:
    """GET /api/v1/quotes/{id}/pdf must return a valid PDF."""
    await _full_auth(db_client)
    refs = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    quote_id = await _create_quote(db_client, customer_id, refs)

    resp = await db_client.get(f"/api/v1/quotes/{quote_id}/pdf?locale=en")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:4] == b"%PDF"


@pytest.mark.integration
async def test_quote_pdf_zh_locale(db_client: AsyncClient) -> None:
    """GET /api/v1/quotes/{id}/pdf?locale=zh must return a valid PDF."""
    await _full_auth(db_client, email="quotepdfzh@example.com")
    refs = await _setup_company(db_client)
    customer_id = await _create_customer(db_client, name="中文客户")
    quote_id = await _create_quote(db_client, customer_id, refs)

    resp = await db_client.get(f"/api/v1/quotes/{quote_id}/pdf?locale=zh")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


@pytest.mark.integration
async def test_quote_pdf_cross_company_404(db_client: AsyncClient) -> None:
    """Requesting a PDF for a non-existent quote returns 404."""
    await _full_auth(db_client, email="quotepdfxco@example.com")
    await _setup_company(db_client)

    fake_id = uuid.uuid4()
    resp = await db_client.get(f"/api/v1/quotes/{fake_id}/pdf")
    assert resp.status_code == 404
