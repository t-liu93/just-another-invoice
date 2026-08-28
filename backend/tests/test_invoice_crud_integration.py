"""Integration tests for M5 step 3 – Invoice CRUD / list / status / product options.

Requires a running PostgreSQL instance (``pytest -m integration``).

Test coverage:
- Happy flow: create (LINE / DOCUMENT), list, get, update, delete
- Number allocation: deferred to DRAFT->SENT issue; create yields no number,
  jump sequence, delete-draft leaves no gap
- LINE mode: per-line taxes with correct amounts
- DOCUMENT mode: single document tax entry
- Incl-VAT: reverse calculation round-trip
- Line + document discount combo
- VAT treatment derivation snapshots stored correctly
- Cascade delete: invoice deletion removes lines/taxes
- Cross-company 404 / isolation
- Status transitions: DRAFT→SENT, DRAFT→CANCELLED, CANCELLED→DRAFT
- Invalid transitions: COMPLETED cannot be set manually; SENT→CANCELLED blocked
- CANCELLED/COMPLETED cannot be edited
- Customer / vat_rate / treatment FK constraints
- Product options endpoint: never leaks cost/margin/supplier
- Auth: unauthenticated → 401, no company → 400
"""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared helpers (same as calculate integration tests)
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


async def _setup_company(client: AsyncClient, country_code: str = "NL") -> dict:
    resp = await client.put(
        "/api/v1/company",
        json={
            "name": "Test Invoice Co",
            "base_currency": "EUR",
            "country_code": country_code,
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


async def _create_customer(
    client: AsyncClient,
    *,
    name: str = "Test Customer",
    vat_id: str | None = None,
    country_code: str = "NL",
    invoice_prefix: str | None = None,
) -> str:
    payload: dict = {
        "name": name,
        "vat_id": vat_id,
        "addresses": [
            {"type": "BILLING", "country_code": country_code, "city": "Amsterdam"},
        ],
    }
    if invoice_prefix:
        payload["invoice_prefix"] = invoice_prefix
    resp = await client.post("/api/v1/customers", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def _line_payload(
    rate_id: str,
    *,
    name: str = "Service",
    quantity: str = "1",
    unit_price: str = "100.000",
    discount: dict | None = None,
) -> dict:
    p: dict = {
        "name": name,
        "quantity": quantity,
        "unit_price": unit_price,
        "vat_rate_id": rate_id,
    }
    if discount:
        p["discount"] = discount
    return p


def _invoice_payload(
    customer_id: str,
    rate_id: str,
    *,
    tax_mode: str = "LINE",
    amounts_include_vat: bool = False,
    treatment_id: str | None = None,
    document_vat_rate_id: str | None = None,
    discount: dict | None = None,
    lines: list[dict] | None = None,
    invoice_date: str = "2026-06-10",
    due_date: str | None = None,
    notes: str | None = None,
) -> dict:
    if lines is None:
        lines = [_line_payload(rate_id)]
    payload: dict = {
        "customer_id": customer_id,
        "invoice_date": invoice_date,
        "tax_mode": tax_mode,
        "amounts_include_vat": amounts_include_vat,
        "lines": lines,
    }
    if treatment_id:
        payload["vat_treatment_id"] = treatment_id
    if document_vat_rate_id:
        payload["document_vat_rate_id"] = document_vat_rate_id
    if discount:
        payload["discount"] = discount
    if due_date:
        payload["due_date"] = due_date
    if notes:
        payload["notes"] = notes
    return payload


async def _issue_invoice(client: AsyncClient, invoice_id: str) -> dict:
    """Issue (DRAFT -> SENT) an invoice; the number is allocated on issue."""
    resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Happy flow: CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceCRUDHappyFlow:

    async def test_create_and_get_line_mode(self, db_client: AsyncClient) -> None:
        """Create a LINE-mode invoice, verify amounts, then GET it back."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        assert create_resp.status_code == 201
        inv = create_resp.json()

        assert inv["status"] == "DRAFT"
        assert inv["paid_status"] == "UNPAID"
        assert inv["tax_mode"] == "LINE"
        assert inv["subtotal_excl_vat"] == "100.000"
        assert inv["vat_total"] == "21.000"
        assert inv["total_incl_vat"] == "121.000"
        assert inv["due_amount"] == "121.000"
        # A fresh DRAFT carries no number; it is allocated only at issue.
        assert inv["invoice_number"] is None
        assert inv["sequence_number"] is None
        assert len(inv["lines"]) == 1
        assert len(inv["lines"][0]["line_taxes"]) == 1

        # Issuing the invoice allocates the legal number.
        issued = await _issue_invoice(db_client, inv["id"])
        assert issued["invoice_number"].startswith("INV-")
        assert issued["sequence_number"] == 1

        # Verify treatment snapshot
        snap = inv["vat_treatment_snapshot"]
        assert snap["code"] == "NL_DOMESTIC"
        assert snap["effect"] == "APPLY_RATE"

        # GET by id
        get_resp = await db_client.get(f"/api/v1/invoices/{inv['id']}")
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["id"] == inv["id"]
        assert fetched["total_incl_vat"] == "121.000"

    async def test_create_document_mode(self, db_client: AsyncClient) -> None:
        """DOCUMENT mode with 2 lines + 21% document rate."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        lines = [
            {"name": "A", "quantity": "1", "unit_price": "100"},
            {"name": "B", "quantity": "2", "unit_price": "50"},
        ]
        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21,
                lines=lines,
            ),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["tax_mode"] == "DOCUMENT"
        assert inv["subtotal_excl_vat"] == "200.000"
        assert inv["vat_total"] == "42.000"
        assert inv["total_incl_vat"] == "242.000"
        # Document-level taxes
        assert len(inv["taxes"]) == 1
        assert inv["taxes"][0]["vat_rate_percent"] == "21.000"

    async def test_update_invoice_preserves_number(self, db_client: AsyncClient) -> None:
        """PUT /invoices/{id} must preserve the numbering fields verbatim.

        Only DRAFT invoices are editable, and a DRAFT carries no number yet, so
        the preserved value is None: editing a draft must never accidentally
        allocate a number (that only happens at DRAFT -> SENT).
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        rate_9 = seeds["rates"]["NL reduced (9%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        created = create_resp.json()
        assert created["invoice_number"] is None
        inv_id = created["id"]

        update_resp = await db_client.put(
            f"/api/v1/invoices/{inv_id}",
            json=_invoice_payload(
                customer_id, rate_9,
                lines=[{
                    "name": "Updated Service",
                    "quantity": "2",
                    "unit_price": "50",
                    "vat_rate_id": rate_9,
                }],
            ),
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        # Editing a draft must not allocate a number.
        assert updated["invoice_number"] is None
        assert updated["sequence_number"] is None
        assert updated["vat_total"] == "9.000"  # 100 * 9%
        assert updated["total_incl_vat"] == "109.000"

    async def test_delete_invoice(self, db_client: AsyncClient) -> None:
        """DELETE /invoices/{id} removes invoice and returns 204."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        inv_id = create_resp.json()["id"]

        del_resp = await db_client.delete(f"/api/v1/invoices/{inv_id}")
        assert del_resp.status_code == 204

        get_resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
        assert get_resp.status_code == 404

    async def test_list_invoices(self, db_client: AsyncClient) -> None:
        """Create 3 invoices, verify list pagination and filtering."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        for _ in range(3):
            await db_client.post(
                "/api/v1/invoices",
                json=_invoice_payload(customer_id, rate_21),
            )

        list_resp = await db_client.get("/api/v1/invoices")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

        # Pagination
        page1 = await db_client.get("/api/v1/invoices?limit=2&offset=0")
        assert page1.json()["total"] == 3
        assert len(page1.json()["items"]) == 2

        page2 = await db_client.get("/api/v1/invoices?limit=2&offset=2")
        assert len(page2.json()["items"]) == 1

    async def test_list_filter_by_customer(self, db_client: AsyncClient) -> None:
        """List filters by customer_id."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        c1 = await _create_customer(db_client, name="Customer 1")
        c2 = await _create_customer(db_client, name="Customer 2")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post("/api/v1/invoices", json=_invoice_payload(c1, rate_21))
        await db_client.post("/api/v1/invoices", json=_invoice_payload(c1, rate_21))
        await db_client.post("/api/v1/invoices", json=_invoice_payload(c2, rate_21))

        resp = await db_client.get(f"/api/v1/invoices?customer_id={c1}")
        data = resp.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["customer_id"] == c1

    async def test_list_filter_by_status(self, db_client: AsyncClient) -> None:
        """List filters by status."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        inv_id = create_resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "SENT"}
        )
        await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )

        # Filter DRAFT
        draft_resp = await db_client.get("/api/v1/invoices?status=DRAFT")
        assert draft_resp.json()["total"] == 1

        # Filter SENT
        sent_resp = await db_client.get("/api/v1/invoices?status=SENT")
        assert sent_resp.json()["total"] == 1

    async def test_list_search_by_invoice_number(self, db_client: AsyncClient) -> None:
        """Search by invoice number prefix."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        # Numbers are only allocated on issue; issue first, then search by it.
        issued = await _issue_invoice(db_client, resp.json()["id"])
        number = issued["invoice_number"]

        search_resp = await db_client.get(f"/api/v1/invoices?q={number}")
        assert search_resp.json()["total"] == 1

        # Non-matching search
        no_resp = await db_client.get("/api/v1/invoices?q=XXXXNOTEXIST")
        assert no_resp.json()["total"] == 0

    async def test_list_customer_name_in_items(self, db_client: AsyncClient) -> None:
        """List items include customer_name from join."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, name="Acme Corp")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post("/api/v1/invoices", json=_invoice_payload(customer_id, rate_21))

        resp = await db_client.get("/api/v1/invoices")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["customer_name"] == "Acme Corp"

    async def test_list_search_by_customer_name(self, db_client: AsyncClient) -> None:
        """Search q= also matches customer name."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        c1 = await _create_customer(db_client, name="Searchable Corp")
        c2 = await _create_customer(db_client, name="Other Co")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post("/api/v1/invoices", json=_invoice_payload(c1, rate_21))
        await db_client.post("/api/v1/invoices", json=_invoice_payload(c2, rate_21))

        # Search by partial customer name
        search_resp = await db_client.get("/api/v1/invoices?q=Searchable")
        assert search_resp.json()["total"] == 1
        assert search_resp.json()["items"][0]["customer_name"] == "Searchable Corp"

        # Non-matching search
        no_resp = await db_client.get("/api/v1/invoices?q=XXXXNOTEXIST")
        assert no_resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Numbering: deferred to issue (DRAFT->SENT); delete-draft leaves no gap
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceNumbering:

    async def test_create_yields_no_number(self, db_client: AsyncClient) -> None:
        """A freshly created invoice (DRAFT) has no number/sequence yet."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["invoice_number"] is None
        assert inv["sequence_number"] is None

    async def test_first_invoice_gets_default_number_on_issue(
        self, db_client: AsyncClient
    ) -> None:
        """Issuing the first invoice (default config) allocates INV-000001."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        assert resp.status_code == 201
        issued = await _issue_invoice(db_client, resp.json()["id"])
        assert issued["invoice_number"] == "INV-000001"
        assert issued["sequence_number"] == 1

        # The company sequence advanced by exactly 1 (next is now 2).
        seq_resp = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert seq_resp.json()["next_sequence"] == 2

    async def test_sequential_numbering_on_issue(self, db_client: AsyncClient) -> None:
        """Two issued invoices get consecutive numbers (no duplicate)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp1 = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        resp2 = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        # Both are unnumbered drafts until issued.
        assert resp1.json()["invoice_number"] is None
        assert resp2.json()["invoice_number"] is None

        issued1 = await _issue_invoice(db_client, resp1.json()["id"])
        issued2 = await _issue_invoice(db_client, resp2.json()["id"])
        assert issued1["invoice_number"] == "INV-000001"
        assert issued2["invoice_number"] == "INV-000002"
        assert issued1["invoice_number"] != issued2["invoice_number"]

    async def test_delete_draft_leaves_no_gap(self, db_client: AsyncClient) -> None:
        """Core regression: deleting an unissued draft skips no number.

        Create a draft (no number) → delete it → create another draft → issue
        it. The issued invoice must receive INV-000001 (the very first number),
        because the deleted draft never consumed the company sequence.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Company sequence starts at 1 and has not moved.
        seq_before = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert seq_before.json()["next_sequence"] == 1

        resp1 = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv1_id = resp1.json()["id"]
        assert resp1.json()["invoice_number"] is None
        del_resp = await db_client.delete(f"/api/v1/invoices/{inv1_id}")
        assert del_resp.status_code == 204

        # The sequence has NOT advanced because of the deleted draft.
        seq_after_delete = await db_client.get(
            "/api/v1/settings/invoice-number-sequence"
        )
        assert seq_after_delete.json()["next_sequence"] == 1

        resp2 = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        issued2 = await _issue_invoice(db_client, resp2.json()["id"])
        # No gap inherited from the deleted draft: it gets the first number.
        assert issued2["invoice_number"] == "INV-000001"
        assert issued2["sequence_number"] == 1

    async def test_reissue_allocates_number_only_once(
        self, db_client: AsyncClient
    ) -> None:
        """DRAFT->CANCELLED->DRAFT->SENT allocates exactly one number.

        An invoice issued after a cancel/reactivate cycle still consumes only a
        single sequence value (idempotent on already-numbered invoices).
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]

        # DRAFT -> CANCELLED -> DRAFT (never numbered yet).
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "DRAFT"}
        )

        issued = await _issue_invoice(db_client, inv_id)
        assert issued["invoice_number"] == "INV-000001"
        assert issued["sequence_number"] == 1

        # Sequence advanced by exactly one (next is 2).
        seq_resp = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert seq_resp.json()["next_sequence"] == 2

    async def test_jump_sequence_with_settings(self, db_client: AsyncClient) -> None:
        """Set start=100 → first issued invoice is INV-000100; jump→200 issues INV-000200."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Set sequence_start = 100
        await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{SERIES:INV}}-{{SEQUENCE:6}}", "sequence_start": 100},
        )

        resp1 = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        issued1 = await _issue_invoice(db_client, resp1.json()["id"])
        assert issued1["invoice_number"] == "INV-000100"

        # Jump to 200
        await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 200},
        )
        resp2 = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        issued2 = await _issue_invoice(db_client, resp2.json()["id"])
        assert issued2["invoice_number"] == "INV-000200"


# ---------------------------------------------------------------------------
# Pricing: LINE / DOCUMENT / incl-VAT / discounts
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoicePricing:

    async def test_line_mode_per_rate_taxes(self, db_client: AsyncClient) -> None:
        """LINE mode with 21% + 9% lines produces correct tax amounts."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        rate_9 = seeds["rates"]["NL reduced (9%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                lines=[
                    _line_payload(rate_21, name="A", unit_price="100"),
                    _line_payload(rate_9, name="B", unit_price="100"),
                ],
            ),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["subtotal_excl_vat"] == "200.000"
        assert inv["vat_total"] == "30.000"
        assert inv["total_incl_vat"] == "230.000"
        # Taxes stored in lines
        assert len(inv["lines"]) == 2

    async def test_document_mode_single_tax(self, db_client: AsyncClient) -> None:
        """DOCUMENT mode stores one invoice_tax row, no line_taxes."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21,
                lines=[
                    {"name": "A", "quantity": "1", "unit_price": "100"},
                    {"name": "B", "quantity": "1", "unit_price": "100"},
                ],
            ),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert len(inv["taxes"]) == 1
        assert inv["taxes"][0]["tax_amount"] == "42.000"

    async def test_incl_vat_input(self, db_client: AsyncClient) -> None:
        """amounts_include_vat=True: €121 incl→ €100 excl + €21 VAT."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                amounts_include_vat=True,
                lines=[{"name": "X", "quantity": "1", "unit_price": "121", "vat_rate_id": rate_21}],
            ),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["taxable_amount"] == "100.000"
        assert inv["vat_total"] == "21.000"
        assert inv["total_incl_vat"] == "121.000"

    async def test_line_and_document_discount(self, db_client: AsyncClient) -> None:
        """Line 10% discount + document 10% discount cascade correctly."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                discount={"type": "PERCENTAGE", "value": "10"},
                lines=[_line_payload(
                    rate_21,
                    unit_price="100",
                    discount={"type": "PERCENTAGE", "value": "10"},
                )],
            ),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["line_discount_total"] == "10.000"
        assert inv["document_discount_amount"] == "9.000"
        assert inv["taxable_amount"] == "81.000"

    async def test_zero_rate_treatment_vat_is_zero(self, db_client: AsyncClient) -> None:
        """EU B2B reverse → 21% rate but effective VAT = 0."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, country_code="DE", vat_id="DE123456789")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["vat_total"] == "0.000"
        assert inv["total_incl_vat"] == "100.000"
        snap = inv["vat_treatment_snapshot"]
        assert snap["code"] == "EU_B2B_REVERSE"
        assert snap["requires_icp"] is True

    async def test_amounts_persist_after_refresh(self, db_client: AsyncClient) -> None:
        """Amounts loaded from DB match the create response (no drift)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21),
        )
        inv_id = create_resp.json()["id"]
        created = create_resp.json()

        get_resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
        fetched = get_resp.json()

        for field in ["subtotal_excl_vat", "vat_total", "total_incl_vat", "due_amount"]:
            assert fetched[field] == created[field], f"Field {field} drifted after refresh"


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceStatusTransitions:

    async def test_draft_to_sent(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]

        status_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "SENT"}
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "SENT"

    async def test_draft_to_cancelled(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]

        cancel_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "CANCELLED"

    async def test_sent_cannot_be_cancelled(self, db_client: AsyncClient) -> None:
        """SENT → CANCELLED is blocked; use a credit note instead (M9/M10)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "SENT"}
        )

        cancel_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )
        assert cancel_resp.status_code == 409
        assert cancel_resp.json()["detail"]["code"] == "INVOICE_LIFECYCLE_CONFLICT"

    async def test_cancelled_can_be_reactivated(self, db_client: AsyncClient) -> None:
        """CANCELLED → DRAFT is allowed when the invoice was never sent."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )

        reactivate_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "DRAFT"}
        )
        assert reactivate_resp.status_code == 200
        assert reactivate_resp.json()["status"] == "DRAFT"

    async def test_completed_cannot_be_set_manually(self, db_client: AsyncClient) -> None:
        """COMPLETED is M7-reserved; manual attempt is a lifecycle conflict."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]

        completed_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "COMPLETED"}
        )
        assert completed_resp.status_code == 409
        assert completed_resp.json()["detail"]["code"] == "INVOICE_LIFECYCLE_CONFLICT"
        assert "COMPLETED" in completed_resp.json()["detail"]["message"]

    async def test_draft_to_draft_invalid(self, db_client: AsyncClient) -> None:
        """DRAFT → DRAFT is invalid."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]

        bad_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "DRAFT"}
        )
        assert bad_resp.status_code == 409
        assert bad_resp.json()["detail"]["code"] == "INVOICE_LIFECYCLE_CONFLICT"

    async def test_cancelled_to_sent_invalid(self, db_client: AsyncClient) -> None:
        """CANCELLED → SENT is invalid."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )

        bad_resp = await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "SENT"}
        )
        assert bad_resp.status_code == 409
        assert bad_resp.json()["detail"]["code"] == "INVOICE_LIFECYCLE_CONFLICT"

    async def test_edit_cancelled_invoice_rejected(self, db_client: AsyncClient) -> None:
        """PUT on a cancelled invoice returns 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )

        update_resp = await db_client.put(
            f"/api/v1/invoices/{inv_id}",
            json=_invoice_payload(customer_id, rate_21),
        )
        assert update_resp.status_code == 422
        assert "cancelled" in update_resp.json()["detail"].lower()

    async def test_sent_invoice_cannot_be_edited(self, db_client: AsyncClient) -> None:
        """PUT on a SENT invoice returns 422 (SENT is a locked formal document)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "SENT"}
        )

        update_resp = await db_client.put(
            f"/api/v1/invoices/{inv_id}",
            json=_invoice_payload(customer_id, rate_21),
        )
        assert update_resp.status_code == 422
        assert "sent" in update_resp.json()["detail"].lower()

    async def test_sent_invoice_cannot_be_deleted(self, db_client: AsyncClient) -> None:
        """DELETE on a SENT invoice returns 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "SENT"}
        )

        del_resp = await db_client.delete(f"/api/v1/invoices/{inv_id}")
        assert del_resp.status_code == 422
        assert "sent" in del_resp.json()["detail"].lower()

    async def test_reactivated_invoice_can_be_edited(
        self, db_client: AsyncClient
    ) -> None:
        """DRAFT → CANCELLED → DRAFT allows full edit again."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv_id = resp.json()["id"]

        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "CANCELLED"}
        )
        await db_client.post(
            f"/api/v1/invoices/{inv_id}/status", json={"status": "DRAFT"}
        )

        update_resp = await db_client.put(
            f"/api/v1/invoices/{inv_id}",
            json=_invoice_payload(
                customer_id, rate_21,
                lines=[_line_payload(rate_21, unit_price="200.000")],
            ),
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["total_incl_vat"] == "242.000"


# ---------------------------------------------------------------------------
# Isolation / cross-company
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceIsolation:

    async def test_cross_company_get_404(self, db_client: AsyncClient) -> None:
        """GET with a random invoice ID returns 404 (not from this company)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        get_resp = await db_client.get(f"/api/v1/invoices/{uuid.uuid4()}")
        assert get_resp.status_code == 404

    async def test_cross_company_delete_404(self, db_client: AsyncClient) -> None:
        """DELETE with a random invoice ID returns 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        del_resp = await db_client.delete(f"/api/v1/invoices/{uuid.uuid4()}")
        assert del_resp.status_code == 404

    async def test_cross_company_customer_422(self, db_client: AsyncClient) -> None:
        """Customer ID not belonging to this company → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        fake_cust_id = str(uuid.uuid4())
        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(fake_cust_id, rate_21),
        )
        assert resp.status_code == 422
        assert "customer" in resp.json()["detail"].lower()

    async def test_cross_company_vat_rate_422(self, db_client: AsyncClient) -> None:
        """VAT rate not belonging to this company → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, str(uuid.uuid4())),
        )
        assert resp.status_code == 422

    async def test_wrong_currency_422(self, db_client: AsyncClient) -> None:
        """Invoice currency ≠ base_currency → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        payload = _invoice_payload(customer_id, rate_21)
        payload["currency"] = "USD"
        resp = await db_client.post("/api/v1/invoices", json=payload)
        assert resp.status_code == 422
        assert "base currency" in resp.json()["detail"].lower()

    async def test_base_amounts_equal_invoice_amounts_single_currency(
        self, db_client: AsyncClient
    ) -> None:
        """M5 single-currency: base_* must equal invoice amounts (exchange_rate=1)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )
        inv = resp.json()
        assert inv["base_total_incl_vat"] == inv["total_incl_vat"]
        assert inv["base_vat_total"] == inv["vat_total"]
        from decimal import Decimal
        assert Decimal(inv["exchange_rate"]) == Decimal("1")


# ---------------------------------------------------------------------------
# Cascade / FK constraints
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceCascadeAndFK:

    async def test_delete_invoice_cascades_lines_and_taxes(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting an invoice removes its lines and line taxes (CASCADE)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                lines=[
                    _line_payload(rate_21, name="L1"),
                    _line_payload(rate_21, name="L2"),
                ],
            ),
        )
        inv = create_resp.json()
        assert len(inv["lines"]) == 2
        inv_id = inv["id"]

        del_resp = await db_client.delete(f"/api/v1/invoices/{inv_id}")
        assert del_resp.status_code == 204

        # Invoice is gone
        get_resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
        assert get_resp.status_code == 404

    async def test_delete_customer_with_invoice_fails(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting a customer that has invoices is restricted (RESTRICT FK)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21)
        )

        del_resp = await db_client.delete(f"/api/v1/customers/{customer_id}")
        assert del_resp.status_code in (409, 422), (
            f"Expected RESTRICT error, got {del_resp.status_code}: {del_resp.text}"
        )

    async def test_delete_vat_rate_with_invoice_fails(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting a VAT rate referenced by an invoice should be restricted."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21_id = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post(
            "/api/v1/invoices", json=_invoice_payload(customer_id, rate_21_id)
        )

        del_resp = await db_client.delete(f"/api/v1/vat-rates/{rate_21_id}")
        assert del_resp.status_code in (409, 422), (
            f"Expected RESTRICT error, got {del_resp.status_code}"
        )

    async def test_delete_vat_treatment_with_invoice_fails(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting a VAT treatment referenced by an invoice should be restricted (409)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        domestic_treatment_id = seeds["treatments"]["NL_DOMESTIC"]["id"]

        # Create an invoice that snapshots this treatment
        create_resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21, treatment_id=domestic_treatment_id),
        )
        assert create_resp.status_code == 201
        inv_id = create_resp.json()["id"]

        del_resp = await db_client.delete(
            f"/api/v1/vat-treatments/{domestic_treatment_id}"
        )
        assert del_resp.status_code in (409, 422), (
            f"Expected RESTRICT error, got {del_resp.status_code}: {del_resp.text}"
        )

        # Invoice is still intact
        get_resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
        assert get_resp.status_code == 200


# ---------------------------------------------------------------------------
# Product options: never leaks cost/margin
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceProductOptions:

    async def test_returns_customer_safe_fields_only(self, db_client: AsyncClient) -> None:
        """GET /invoice-product-options must not include cost/margin/supplier/extra."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Create a product with cost/margin info
        await db_client.post(
            "/api/v1/products",
            json={
                "name": "Solar Panel",
                "purchase_cost_excl_vat": "100.000",
                "margin_rate": "0.3000",
                "default_vat_rate_id": rate_21,
                "supplier": "SolarCo",
                "extra": {"secret_code": "xyz"},
            },
        )

        resp = await db_client.get("/api/v1/invoice-product-options")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1

        for item in items:
            assert "purchase_cost_excl_vat" not in item
            assert "margin_rate" not in item
            assert "effective_margin_rate" not in item
            assert "supplier" not in item
            assert "extra" not in item
            assert "id" in item
            assert "name" in item

    async def test_search_filters_products(self, db_client: AsyncClient) -> None:
        """q= filter applies to product name."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/products", json={"name": "Solar Panel"})
        await db_client.post("/api/v1/products", json={"name": "Wind Turbine"})

        solar_resp = await db_client.get("/api/v1/invoice-product-options?q=Solar")
        solar_items = solar_resp.json()["items"]
        assert all("solar" in item["name"].lower() for item in solar_items)

        wind_resp = await db_client.get("/api/v1/invoice-product-options?q=Wind")
        wind_items = wind_resp.json()["items"]
        assert all("wind" in item["name"].lower() for item in wind_items)

    async def test_inactive_products_excluded(self, db_client: AsyncClient) -> None:
        """Inactive products are not shown in invoice product options."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/products", json={"name": "Active Gadget"}
        )
        product_id = create_resp.json()["id"]

        await db_client.put(
            f"/api/v1/products/{product_id}",
            json={"name": "Active Gadget", "active": False},
        )

        resp = await db_client.get("/api/v1/invoice-product-options")
        names = [item["name"] for item in resp.json()["items"]]
        assert "Active Gadget" not in names


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceAuth:

    async def test_unauthenticated_create_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.post(
            "/api/v1/invoices",
            json={
                "customer_id": str(uuid.uuid4()),
                "invoice_date": "2026-01-01",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "X", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert resp.status_code == 401

    async def test_unauthenticated_list_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/invoices")
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.post(
            "/api/v1/invoices",
            json={
                "customer_id": str(uuid.uuid4()),
                "invoice_date": "2026-01-01",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "X", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# FK ownership validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvoiceFKValidation:

    async def test_line_mode_document_vat_rate_not_persisted(
        self, db_client: AsyncClient
    ) -> None:
        """LINE-mode invoice: document_vat_rate_id must be NULL even if caller sends one."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Deliberately pass document_vat_rate_id in a LINE-mode request
        payload = _invoice_payload(customer_id, rate_21)
        payload["document_vat_rate_id"] = rate_21

        resp = await db_client.post("/api/v1/invoices", json=payload)
        assert resp.status_code == 201
        assert resp.json()["document_vat_rate_id"] is None

    async def test_nonexistent_product_id_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """Line with a non-existent product_id → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        line = _line_payload(rate_21)
        line["product_id"] = str(uuid.uuid4())  # does not exist

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21, lines=[line]),
        )
        assert resp.status_code == 422
        assert "product" in resp.json()["detail"].lower()

    async def test_nonexistent_unit_id_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """Line with a non-existent unit_id → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        line = _line_payload(rate_21)
        line["unit_id"] = str(uuid.uuid4())  # does not exist

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(customer_id, rate_21, lines=[line]),
        )
        assert resp.status_code == 422
        assert "unit" in resp.json()["detail"].lower()

    async def test_document_mode_line_vat_rate_nonexistent_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """DOCUMENT mode: line with a non-existent vat_rate_id → 422 (not 500)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        line = {"name": "Service", "quantity": "1", "unit_price": "100.000",
                "vat_rate_id": str(uuid.uuid4())}  # non-existent

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21,
                lines=[line],
            ),
        )
        assert resp.status_code == 422
        assert "vat rate" in resp.json()["detail"].lower()

    async def test_document_mode_line_vat_rate_valid_preserved(
        self, db_client: AsyncClient
    ) -> None:
        """DOCUMENT mode: line with a valid same-company vat_rate_id is preserved."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        rate_9 = seeds["rates"]["NL reduced (9%)"]["id"]

        line = {"name": "Service", "quantity": "1", "unit_price": "100.000",
                "vat_rate_id": rate_9}  # valid, belongs to same company

        resp = await db_client.post(
            "/api/v1/invoices",
            json=_invoice_payload(
                customer_id, rate_21,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21,
                lines=[line],
            ),
        )
        assert resp.status_code == 201
        # Per-line rate is preserved as draft default; does not affect calculated tax
        assert resp.json()["lines"][0]["vat_rate_id"] == rate_9
        # Tax is driven by document rate (21%), not line rate (9%)
        assert resp.json()["vat_total"] == "21.000"
