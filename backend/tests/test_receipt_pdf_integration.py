"""Integration tests for payment receipt PDF rendering – M9 step 4.

Requires:
- A running PostgreSQL instance (creates a fresh test database).
- WeasyPrint system libraries (pango, cairo) installed.

Run with:
    pytest -m integration -k "receipt or pdf"

Tests:
1. render_payment_receipt_pdf / download endpoint returns valid PDF (starts with %PDF).
2. Receipt PDF with locale=zh returns valid PDF.
3. Cross-company request (non-existent payment) returns 404.
"""

from __future__ import annotations

import datetime
import uuid

import pyotp
import pypdfium2
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared auth / setup helpers (same pattern as other integration test files)
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "receiptpdftest@example.com",
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
            "name": "Receipt PDF Test BV",
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


async def _create_and_send_invoice(
    client: AsyncClient,
    customer_id: str,
    refs: dict,
) -> str:
    """Create a minimal invoice, send it, and return its ID."""
    rate_21 = refs["rates"].get("NL standard (21%)") or next(
        (r for r in refs["rates"].values() if "21" in r["label"]), None
    )
    treatment = refs["treatments"].get("NL_DOMESTIC")
    assert rate_21 is not None, "21% VAT rate not found"
    assert treatment is not None, "NL_DOMESTIC treatment not found"

    today = datetime.date.today().isoformat()
    resp = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": today,
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
    invoice_id = resp.json()["id"]

    # Mark as SENT (required before recording payment)
    sent_resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/status",
        json={"status": "SENT"},
    )
    assert sent_resp.status_code == 200, sent_resp.text

    return invoice_id


async def _record_payment(client: AsyncClient, invoice_id: str, amount: str = "60.500") -> str:
    """Record a partial payment and return the payment ID."""
    resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"payment_date": datetime.date.today().isoformat(), "amount": amount},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["items"][-1]["id"]


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract visible text from the actual WeasyPrint output."""
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        return "\n".join(
            document[index].get_textpage().get_text_range()
            for index in range(len(document))
        )
    finally:
        document.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_receipt_pdf_download_endpoint(db_client: AsyncClient) -> None:
    """GET /api/v1/payments/{id}/receipt-pdf must return a valid PDF."""
    await _full_auth(db_client)
    refs = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    invoice_id = await _create_and_send_invoice(db_client, customer_id, refs)
    payment_id = await _record_payment(db_client, invoice_id, amount="60.500")

    resp = await db_client.get(f"/api/v1/payments/{payment_id}/receipt-pdf?locale=en")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content[:4] == b"%PDF"
    invoice = await db_client.get(f"/api/v1/invoices/{invoice_id}")
    assert invoice.status_code == 200, invoice.text
    text = _extract_pdf_text(resp.content)
    assert invoice.json()["invoice_number"] in text
    assert "Receipt" in text
    assert "NOT A VAT INVOICE" not in text
    for expected in ("242.00", "60.50", "181.50"):
        assert expected in text


@pytest.mark.integration
async def test_receipt_pdf_zh_locale(db_client: AsyncClient) -> None:
    """GET /api/v1/payments/{id}/receipt-pdf?locale=zh must return a valid PDF."""
    await _full_auth(db_client, email="receiptpdfzh@example.com")
    refs = await _setup_company(db_client)
    customer_id = await _create_customer(db_client, name="中文客户")
    invoice_id = await _create_and_send_invoice(db_client, customer_id, refs)
    payment_id = await _record_payment(db_client, invoice_id, amount="100.000")

    resp = await db_client.get(f"/api/v1/payments/{payment_id}/receipt-pdf?locale=zh")
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == b"%PDF"


@pytest.mark.integration
async def test_receipt_pdf_cross_company_404(db_client: AsyncClient) -> None:
    """Requesting a receipt PDF for a non-existent payment returns 404.

    A random UUID is indistinguishable from a cross-company payment under
    company_id scoping (red-line 2).
    """
    await _full_auth(db_client, email="receiptpdfxco@example.com")
    await _setup_company(db_client)

    fake_id = uuid.uuid4()
    resp = await db_client.get(f"/api/v1/payments/{fake_id}/receipt-pdf")
    assert resp.status_code == 404
