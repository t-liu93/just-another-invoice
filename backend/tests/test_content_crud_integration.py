"""Integration tests for M6 step 4 – Content libraries (document templates,
content blocks, note templates) CRUD + invoice/quote content text snapshots.

Requires a running PostgreSQL instance (``pytest -m integration``).

Test coverage:
- Document template: CRUD roundtrip (with lines), random-ID 404,
  unique name constraint, applies_to filter, FK validation (unit_id, vat_rate_id)
- Content block: CRUD roundtrip, is_default partial unique, kind filter,
  random-ID 404
- Note template: CRUD roundtrip, unique name constraint, random-ID 404
- Auth: unauthenticated → 401, no-company → 400
- Invoice/Quote: write snapshots content block text + notes, read echoes back,
  convert copies content text to invoice, delete template/block does not affect
  persisted document snapshots
- Empty/null: content text fields return null when not provided
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "owner@example.com",
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
        json={"name": "Test Content Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = rates_resp.json()["items"]
    by_label = {r["label"]: r for r in rates}

    treatments_resp = await client.get("/api/v1/vat-treatments?side=SALES")
    treatments = treatments_resp.json()["items"]
    by_code = {t["code"]: t for t in treatments}

    return {"rates": by_label, "treatments": by_code}


async def _create_customer(client: AsyncClient, *, name: str = "Test Customer") -> str:
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": name,
            "addresses": [
                {"type": "BILLING", "country_code": "NL", "city": "Amsterdam"},
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _line(rate_id: str, *, name: str = "Service", qty: str = "1", price: str = "100.000") -> dict:
    return {"name": name, "quantity": qty, "unit_price": price, "vat_rate_id": rate_id}


# ---------------------------------------------------------------------------
# Document Template Tests
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.asyncio


async def test_document_template_crud_roundtrip(db_client: AsyncClient) -> None:
    """Create → get → list → update → delete a document template with lines."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]

    # Create
    resp = await db_client.post(
        "/api/v1/document-templates",
        json={
            "name": "Charging Station Base",
            "applies_to": "QUOTE",
            "lines": [
                {
                    "name": "Installation",
                    "quantity": "1",
                    "unit_price": "1500.000",
                    "vat_rate_id": rate21,
                },
                {
                    "name": "Cable",
                    "description": "5m Type 2 cable",
                    "quantity": "1",
                    "unit_price": "350.000",
                    "discount_type": "PERCENTAGE",
                    "discount_value": "10",
                },
            ],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    tpl_id = data["id"]
    assert data["name"] == "Charging Station Base"
    assert data["applies_to"] == "QUOTE"
    assert len(data["lines"]) == 2
    assert data["lines"][0]["name"] == "Installation"
    assert data["lines"][0]["sort_order"] == 0
    assert data["lines"][1]["sort_order"] == 1
    assert data["lines"][1]["discount_type"] == "PERCENTAGE"
    assert Decimal(data["lines"][1]["discount_value"]) == Decimal("10")

    # Get by ID
    resp = await db_client.get(f"/api/v1/document-templates/{tpl_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Charging Station Base"
    assert len(resp.json()["lines"]) == 2

    # List
    resp = await db_client.get("/api/v1/document-templates")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert any(t["id"] == tpl_id for t in items)

    # Update
    resp = await db_client.put(
        f"/api/v1/document-templates/{tpl_id}",
        json={
            "name": "Charging Station Intermediate",
            "applies_to": "BOTH",
            "lines": [
                {
                    "name": "Installation Pro",
                    "quantity": "1",
                    "unit_price": "2500.000",
                    "vat_rate_id": rate21,
                },
            ],
        },
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "Charging Station Intermediate"
    assert updated["applies_to"] == "BOTH"
    assert len(updated["lines"]) == 1
    assert updated["lines"][0]["name"] == "Installation Pro"

    # Delete
    resp = await db_client.delete(f"/api/v1/document-templates/{tpl_id}")
    assert resp.status_code == 204

    # Verify gone
    resp = await db_client.get(f"/api/v1/document-templates/{tpl_id}")
    assert resp.status_code == 404


async def test_document_template_applies_to_filter(db_client: AsyncClient) -> None:
    """applies_to filter returns matching templates + BOTH."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    # Create three templates with different scopes
    for name, scope in [("Q1", "QUOTE"), ("I1", "INVOICE"), ("B1", "BOTH")]:
        resp = await db_client.post(
            "/api/v1/document-templates",
            json={
                "name": name,
                "applies_to": scope,
                "lines": [{"name": "Line", "quantity": "1", "unit_price": "100.000"}],
            },
        )
        assert resp.status_code == 201

    # Filter QUOTE: should get Q1 + B1
    resp = await db_client.get("/api/v1/document-templates?applies_to=QUOTE")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert "Q1" in names
    assert "B1" in names
    assert "I1" not in names

    # Filter INVOICE: should get I1 + B1
    resp = await db_client.get("/api/v1/document-templates?applies_to=INVOICE")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert "I1" in names
    assert "B1" in names
    assert "Q1" not in names

    # No filter: all three
    resp = await db_client.get("/api/v1/document-templates")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert names == {"Q1", "I1", "B1"}


async def test_document_template_unique_name(db_client: AsyncClient) -> None:
    """Cannot create two templates with the same name in one company."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    payload = {
        "name": "Unique Name",
        "applies_to": "QUOTE",
        "lines": [{"name": "L", "quantity": "1", "unit_price": "10.000"}],
    }
    resp = await db_client.post("/api/v1/document-templates", json=payload)
    assert resp.status_code == 201

    resp = await db_client.post("/api/v1/document-templates", json=payload)
    assert resp.status_code == 422
    assert "already exists" in resp.json()["detail"]


async def test_document_template_cross_company_404(db_client: AsyncClient) -> None:
    """Template with random ID returns 404 (not from this company)."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    resp = await db_client.get(f"/api/v1/document-templates/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = await db_client.delete(f"/api/v1/document-templates/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = await db_client.put(
        f"/api/v1/document-templates/{uuid.uuid4()}",
        json={
            "name": "X",
            "applies_to": "QUOTE",
            "lines": [{"name": "L", "quantity": "1", "unit_price": "10.000"}],
        },
    )
    assert resp.status_code == 404


async def test_document_template_fk_validation(db_client: AsyncClient) -> None:
    """unit_id / vat_rate_id must belong to this company."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    # Invalid vat_rate_id
    resp = await db_client.post(
        "/api/v1/document-templates",
        json={
            "name": "Bad FK",
            "applies_to": "QUOTE",
            "lines": [
                {
                    "name": "L",
                    "quantity": "1",
                    "unit_price": "10.000",
                    "vat_rate_id": str(uuid.uuid4()),
                },
            ],
        },
    )
    assert resp.status_code == 422

    # Invalid unit_id
    resp = await db_client.post(
        "/api/v1/document-templates",
        json={
            "name": "Bad Unit",
            "applies_to": "QUOTE",
            "lines": [
                {
                    "name": "L",
                    "quantity": "1",
                    "unit_price": "10.000",
                    "unit_id": str(uuid.uuid4()),
                },
            ],
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Content Block Tests
# ---------------------------------------------------------------------------


async def test_content_block_crud_roundtrip(db_client: AsyncClient) -> None:
    """Create → get → list → update → delete a content block."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    # Create
    resp = await db_client.post(
        "/api/v1/content-blocks",
        json={
            "kind": "WARRANTY",
            "name": "Standard Warranty",
            "body": "2 years warranty on all parts and labor.",
            "is_default": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    block_id = data["id"]
    assert data["kind"] == "WARRANTY"
    assert data["name"] == "Standard Warranty"
    assert data["body"] == "2 years warranty on all parts and labor."
    assert data["is_default"] is True

    # Get by ID
    resp = await db_client.get(f"/api/v1/content-blocks/{block_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Standard Warranty"

    # List all
    resp = await db_client.get("/api/v1/content-blocks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # List by kind
    resp = await db_client.get("/api/v1/content-blocks?kind=WARRANTY")
    assert resp.status_code == 200
    assert all(b["kind"] == "WARRANTY" for b in resp.json())

    # Update
    resp = await db_client.put(
        f"/api/v1/content-blocks/{block_id}",
        json={
            "kind": "WARRANTY",
            "name": "Extended Warranty",
            "body": "5 years warranty.",
            "is_default": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Extended Warranty"
    assert resp.json()["body"] == "5 years warranty."

    # Delete
    resp = await db_client.delete(f"/api/v1/content-blocks/{block_id}")
    assert resp.status_code == 204

    resp = await db_client.get(f"/api/v1/content-blocks/{block_id}")
    assert resp.status_code == 404


async def test_content_block_is_default_partial_unique(db_client: AsyncClient) -> None:
    """At most one default per kind; setting a new default clears the old one."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    # Create first default for TERMS
    resp = await db_client.post(
        "/api/v1/content-blocks",
        json={
            "kind": "TERMS",
            "name": "Terms A",
            "body": "Terms A body",
            "is_default": True,
        },
    )
    assert resp.status_code == 201
    first_id = resp.json()["id"]
    assert resp.json()["is_default"] is True

    # Create second default for same kind → first should be auto-cleared
    resp = await db_client.post(
        "/api/v1/content-blocks",
        json={
            "kind": "TERMS",
            "name": "Terms B",
            "body": "Terms B body",
            "is_default": True,
        },
    )
    assert resp.status_code == 201

    # Verify first is no longer default
    resp = await db_client.get(f"/api/v1/content-blocks/{first_id}")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is False

    # Different kind has independent default
    resp = await db_client.post(
        "/api/v1/content-blocks",
        json={
            "kind": "BANK",
            "name": "Bank Info",
            "body": "NL91ABNA0417164300",
            "is_default": True,
        },
    )
    assert resp.status_code == 201

    # Update: setting is_default=True on existing should clear other default.
    # Create a non-default block first.
    resp = await db_client.post(
        "/api/v1/content-blocks",
        json={
            "kind": "TERMS",
            "name": "Terms C",
            "body": "Terms C body",
            "is_default": False,
        },
    )
    assert resp.status_code == 201
    third_id = resp.json()["id"]

    # Now set Terms C as default → Terms B should lose default
    resp = await db_client.put(
        f"/api/v1/content-blocks/{third_id}",
        json={
            "kind": "TERMS",
            "name": "Terms C Updated",
            "body": "Terms C body updated",
            "is_default": True,
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
    assert resp.json()["is_default"] is True


async def test_content_block_cross_company_404(db_client: AsyncClient) -> None:
    """Content block with random ID returns 404."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    resp = await db_client.get(f"/api/v1/content-blocks/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = await db_client.delete(f"/api/v1/content-blocks/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = await db_client.put(
        f"/api/v1/content-blocks/{uuid.uuid4()}",
        json={"kind": "WARRANTY", "name": "X", "body": "Y", "is_default": False},
    )
    assert resp.status_code == 404


async def test_content_block_unique_kind_name(db_client: AsyncClient) -> None:
    """Cannot create two blocks with same kind + name in one company."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    payload = {
        "kind": "PAYMENT_TERMS",
        "name": "Standard",
        "body": "Pay within 30 days",
        "is_default": False,
    }
    resp = await db_client.post("/api/v1/content-blocks", json=payload)
    assert resp.status_code == 201

    resp = await db_client.post("/api/v1/content-blocks", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Note Template Tests
# ---------------------------------------------------------------------------


async def test_note_template_crud_roundtrip(db_client: AsyncClient) -> None:
    """Create → get → list → update → delete a note template."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    # Create
    resp = await db_client.post(
        "/api/v1/note-templates",
        json={"name": "Thank You Note", "body": "Thank you for your business!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    tpl_id = data["id"]
    assert data["name"] == "Thank You Note"
    assert data["body"] == "Thank you for your business!"

    # Get
    resp = await db_client.get(f"/api/v1/note-templates/{tpl_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Thank You Note"

    # List
    resp = await db_client.get("/api/v1/note-templates")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Update
    resp = await db_client.put(
        f"/api/v1/note-templates/{tpl_id}",
        json={"name": "Thank You Updated", "body": "Updated note text"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Thank You Updated"
    assert resp.json()["body"] == "Updated note text"

    # Delete
    resp = await db_client.delete(f"/api/v1/note-templates/{tpl_id}")
    assert resp.status_code == 204

    resp = await db_client.get(f"/api/v1/note-templates/{tpl_id}")
    assert resp.status_code == 404


async def test_note_template_unique_name(db_client: AsyncClient) -> None:
    """Cannot create two note templates with the same name."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    payload = {"name": "Unique Note", "body": "Body text"}
    resp = await db_client.post("/api/v1/note-templates", json=payload)
    assert resp.status_code == 201

    resp = await db_client.post("/api/v1/note-templates", json=payload)
    assert resp.status_code == 422


async def test_note_template_cross_company_404(db_client: AsyncClient) -> None:
    """Note template with random ID returns 404."""
    await _full_auth(db_client)
    await _setup_company(db_client)

    resp = await db_client.get(f"/api/v1/note-templates/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = await db_client.delete(f"/api/v1/note-templates/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = await db_client.put(
        f"/api/v1/note-templates/{uuid.uuid4()}",
        json={"name": "X", "body": "Y"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


async def test_content_endpoints_require_auth(db_client: AsyncClient) -> None:
    """All content endpoints return 401 without authentication."""
    for path in [
        "/api/v1/document-templates",
        "/api/v1/content-blocks",
        "/api/v1/note-templates",
    ]:
        resp = await db_client.get(path)
        assert resp.status_code == 401, f"GET {path} should require auth"


async def test_content_endpoints_require_company(db_client: AsyncClient) -> None:
    """All content endpoints return 400 for user with no company."""
    await _full_auth(db_client)
    # User has no company_id, so _require_company_id will fail with 400
    for path in [
        "/api/v1/document-templates",
        "/api/v1/content-blocks",
        "/api/v1/note-templates",
    ]:
        resp = await db_client.get(path)
        assert resp.status_code == 400, f"GET {path} should fail without company"


# ---------------------------------------------------------------------------
# Invoice / Quote content text snapshots
# ---------------------------------------------------------------------------


async def test_invoice_content_text_snapshot(db_client: AsyncClient) -> None:
    """Invoice write snapshots content block text and notes; read echoes back."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]
    treatment_id = ctx["treatments"]["NL_DOMESTIC"]["id"]
    customer_id = await _create_customer(db_client)

    # Create invoice with content text
    resp = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "notes": "Please pay on time",
            "warranty_text": "2 years warranty",
            "terms_text": "Net 30 days",
            "bank_text": "NL91ABNA0417164300",
            "payment_terms_text": "Pay within 14 days for 2% discount",
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    inv_id = data["id"]

    assert data["notes"] == "Please pay on time"
    assert data["warranty_text"] == "2 years warranty"
    assert data["terms_text"] == "Net 30 days"
    assert data["bank_text"] == "NL91ABNA0417164300"
    assert data["payment_terms_text"] == "Pay within 14 days for 2% discount"

    # GET echoes back
    resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
    assert resp.status_code == 200
    assert resp.json()["warranty_text"] == "2 years warranty"
    assert resp.json()["terms_text"] == "Net 30 days"

    # Update changes content text
    resp = await db_client.put(
        f"/api/v1/invoices/{inv_id}",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "notes": "Updated notes",
            "warranty_text": "Updated warranty",
            "terms_text": None,
            "bank_text": None,
            "payment_terms_text": None,
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Updated notes"
    assert resp.json()["warranty_text"] == "Updated warranty"
    assert resp.json()["terms_text"] is None
    assert resp.json()["bank_text"] is None


async def test_quote_content_text_snapshot(db_client: AsyncClient) -> None:
    """Quote write snapshots content block text and notes; read echoes back."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]
    treatment_id = ctx["treatments"]["NL_DOMESTIC"]["id"]
    customer_id = await _create_customer(db_client)

    # Create quote with content text
    resp = await db_client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer_id,
            "quote_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "notes": "Quote notes here",
            "warranty_text": "5 years warranty",
            "terms_text": "Quote terms",
            "bank_text": "BANK123",
            "payment_terms_text": "50% upfront",
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 201
    data = resp.json()

    assert data["notes"] == "Quote notes here"
    assert data["warranty_text"] == "5 years warranty"
    assert data["terms_text"] == "Quote terms"
    assert data["bank_text"] == "BANK123"
    assert data["payment_terms_text"] == "50% upfront"


async def test_convert_copies_content_text(db_client: AsyncClient) -> None:
    """Converting quote → invoice copies content block text snapshots."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]
    treatment_id = ctx["treatments"]["NL_DOMESTIC"]["id"]
    customer_id = await _create_customer(db_client)

    # Create quote with content text
    resp = await db_client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer_id,
            "quote_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "notes": "Original quote notes",
            "warranty_text": "3 years",
            "terms_text": "T&C apply",
            "bank_text": "NLBANK",
            "payment_terms_text": "30 days",
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 201
    quote_id = resp.json()["id"]

    # Mark SENT
    resp = await db_client.post(
        f"/api/v1/quotes/{quote_id}/status",
        json={"status": "SENT"},
    )
    assert resp.status_code == 200

    # Convert
    resp = await db_client.post(f"/api/v1/quotes/{quote_id}/convert")
    assert resp.status_code == 201
    inv = resp.json()

    assert inv["notes"] == "Original quote notes"
    assert inv["warranty_text"] == "3 years"
    assert inv["terms_text"] == "T&C apply"
    assert inv["bank_text"] == "NLBANK"
    assert inv["payment_terms_text"] == "30 days"


async def test_delete_template_does_not_affect_document(db_client: AsyncClient) -> None:
    """Deleting a template/content block/note template does not affect persisted
    document snapshots (value copy, not reference)."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]
    treatment_id = ctx["treatments"]["NL_DOMESTIC"]["id"]
    customer_id = await _create_customer(db_client)

    # Create a document template
    resp = await db_client.post(
        "/api/v1/document-templates",
        json={
            "name": "Disposable Template",
            "applies_to": "BOTH",
            "lines": [
                {"name": "Setup", "quantity": "1", "unit_price": "500.000", "vat_rate_id": rate21},
            ],
        },
    )
    assert resp.status_code == 201
    tpl_id = resp.json()["id"]

    # Create a content block
    resp = await db_client.post(
        "/api/v1/content-blocks",
        json={
            "kind": "WARRANTY",
            "name": "Temp Warranty",
            "body": "90 days warranty",
            "is_default": False,
        },
    )
    assert resp.status_code == 201
    block_id = resp.json()["id"]

    # Create a note template
    resp = await db_client.post(
        "/api/v1/note-templates",
        json={"name": "Temp Note", "body": "Temporary note text"},
    )
    assert resp.status_code == 201
    note_id = resp.json()["id"]

    # Create an invoice that uses the content text (simulating front-end value copy)
    warranty_body = "90 days warranty"
    note_body = "Temporary note text"
    resp = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "warranty_text": warranty_body,
            "notes": note_body,
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 201
    inv_id = resp.json()["id"]

    # Delete all templates
    resp = await db_client.delete(f"/api/v1/document-templates/{tpl_id}")
    assert resp.status_code == 204
    resp = await db_client.delete(f"/api/v1/content-blocks/{block_id}")
    assert resp.status_code == 204
    resp = await db_client.delete(f"/api/v1/note-templates/{note_id}")
    assert resp.status_code == 204

    # Invoice still has its snapshot text
    resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
    assert resp.status_code == 200
    assert resp.json()["warranty_text"] == warranty_body
    assert resp.json()["notes"] == note_body


async def test_invoice_content_text_null(db_client: AsyncClient) -> None:
    """Invoice with no content text fields returns nulls."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]
    treatment_id = ctx["treatments"]["NL_DOMESTIC"]["id"]
    customer_id = await _create_customer(db_client)

    resp = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["notes"] is None
    assert data["warranty_text"] is None
    assert data["terms_text"] is None
    assert data["bank_text"] is None
    assert data["payment_terms_text"] is None


async def test_quote_content_text_null(db_client: AsyncClient) -> None:
    """Quote with no content text fields returns nulls."""
    await _full_auth(db_client)
    ctx = await _setup_company(db_client)
    rate21 = ctx["rates"]["NL standard (21%)"]["id"]
    treatment_id = ctx["treatments"]["NL_DOMESTIC"]["id"]
    customer_id = await _create_customer(db_client)

    resp = await db_client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer_id,
            "quote_date": "2026-06-11",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "lines": [_line(rate21)],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["notes"] is None
    assert data["warranty_text"] is None
    assert data["terms_text"] is None
    assert data["bank_text"] is None
    assert data["payment_terms_text"] is None
