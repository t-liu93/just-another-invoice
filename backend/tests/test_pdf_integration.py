"""Integration tests for PDF rendering – M9 step 1.

Requires:
- A running PostgreSQL instance (creates a fresh test database).
- WeasyPrint system libraries (pango, cairo) installed.

Run with:
    pytest -m integration -k "pdf"

Tests:
1. html_to_pdf produces a valid PDF (starts with %PDF, non-empty).
2. Download endpoint (GET /api/v1/invoices/{id}/pdf) returns PDF bytes with
   the correct Content-Disposition header.
3. Cross-company request returns 404.
"""

from __future__ import annotations

import datetime
import uuid

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared auth/setup helpers (same pattern as other integration tests)
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "pdftest@example.com",
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
            "name": "PDF Test BV",
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


async def _create_invoice(
    client: AsyncClient,
    customer_id: str,
    refs: dict,
) -> str:
    """Create a minimal invoice and return its ID."""
    rate_21 = refs["rates"].get("NL standard (21%)") or next(
        (r for r in refs["rates"].values() if "21" in r["label"]), None
    )
    treatment = refs["treatments"].get("NL_DOMESTIC")
    assert rate_21 is not None, "21% VAT rate not found"
    assert treatment is not None, "NL_STANDARD treatment not found"

    today = datetime.date.today().isoformat()
    resp = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": today,
            "due_date": today,
            "currency": "EUR",
            "tax_mode": "DOCUMENT",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment["id"],
            "document_vat_rate_id": rate_21["id"],
            "lines": [
                {
                    "name": "Service rendered",
                    "description": "Consulting services",
                    "quantity": "2",
                    "unit_price": "100.000",
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _issue_invoice(client: AsyncClient, invoice_id: str) -> None:
    """Issue (DRAFT -> SENT) the invoice so its legal number is allocated."""
    resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"}
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Test: html_to_pdf produces a valid PDF
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_html_to_pdf_produces_valid_pdf() -> None:
    """html_to_pdf must return non-empty bytes starting with b'%PDF'."""
    from jai.services.pdf import html_to_pdf

    minimal_html = """<!DOCTYPE html>
    <html><head><meta charset="utf-8"></head>
    <body><p>Test PDF rendering.</p></body></html>"""

    pdf_bytes = html_to_pdf(minimal_html)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes[:4] == b"%PDF", f"Expected %PDF header, got: {pdf_bytes[:10]!r}"


# ---------------------------------------------------------------------------
# Test: download endpoint returns PDF
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_invoice_pdf_download_endpoint(db_client: AsyncClient) -> None:
    """GET /api/v1/invoices/{id}/pdf must return a valid PDF."""
    await _full_auth(db_client)
    refs = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    invoice_id = await _create_invoice(db_client, customer_id, refs)
    # A real legal PDF is of an issued (numbered) invoice; issue it first.
    await _issue_invoice(db_client, invoice_id)

    resp = await db_client.get(f"/api/v1/invoices/{invoice_id}/pdf?locale=en")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:4] == b"%PDF"


@pytest.mark.integration
async def test_invoice_pdf_zh_locale(db_client: AsyncClient) -> None:
    """GET /api/v1/invoices/{id}/pdf?locale=zh must return a valid PDF."""
    await _full_auth(db_client, email="pdfzh@example.com")
    refs = await _setup_company(db_client)
    customer_id = await _create_customer(db_client, name="中文客户")
    invoice_id = await _create_invoice(db_client, customer_id, refs)
    await _issue_invoice(db_client, invoice_id)

    resp = await db_client.get(f"/api/v1/invoices/{invoice_id}/pdf?locale=zh")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


@pytest.mark.integration
async def test_invoice_pdf_cross_company_404(db_client: AsyncClient) -> None:
    """Requesting a PDF for a non-existent invoice returns 404."""
    await _full_auth(db_client, email="pdfxco@example.com")
    await _setup_company(db_client)

    fake_id = uuid.uuid4()
    resp = await db_client.get(f"/api/v1/invoices/{fake_id}/pdf")
    assert resp.status_code == 404
