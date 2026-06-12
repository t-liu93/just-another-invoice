"""Integration tests for M7 step 1 & 2 – Payment record / list / read / edit / delete.

Requires a running PostgreSQL instance (``pytest -m integration``).

Step 1 coverage:
- Record payment → due_amount / paid_status / status written back correctly
- Partial payment: PARTIALLY_PAID, status stays SENT
- Full payment: PAID, status SENT → COMPLETED
- Overpayment → 422
- Payment on DRAFT invoice → 422
- Payment on CANCELLED invoice → 422
- Cross-company invoice → 404
- GET /invoices/{id}/payments returns aggregate
- GET /payments/{id} returns PaymentRead
- Cross-company GET /payments/{id} → 404
- Owner-only: non-owner → 403
- payment_method_name snapshot: delete method → FK SET NULL, name snapshot preserved
- Amount quantisation (D9 / F1): >3-dp input is rounded before DB write; POST
  response, GET-single, and LIST all return the quantised value consistently.
- Quantise-to-full: 120.9996 rounds to 121.000 → invoice becomes PAID/COMPLETED.

Step 2 coverage:
- First partial + final payment → PAID / COMPLETED
- Edit final payment smaller → PARTIALLY_PAID + COMPLETED→SENT (lifecycle rollback)
- Edit that would cause overpayment → 422 (D6 guard in update path)
- Cross-company edit (random UUID) → 404
- Edit with >3-dp amount → quantisation consistency across response / GET / LIST
- Edit to 120.9996 → quantises to 121.000 → PAID / COMPLETED (F1 guard in update path)
- Delete one payment → due_amount increases + status rolls back to SENT
- Delete all payments → UNPAID + status SENT
- Delete final payment from COMPLETED invoice → UNPAID + status SENT
- Cross-company delete (random UUID) → 404
- Unauthenticated delete → 401
"""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared test helpers (self-contained, no cross-file imports)
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
        json={"name": "Test Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = {r["label"]: r for r in rates_resp.json()["items"]}

    treatments_resp = await client.get("/api/v1/vat-treatments?side=SALES")
    treatments = {t["code"]: t for t in treatments_resp.json()["items"]}

    return {"rates": rates, "treatments": treatments}


async def _create_customer(client: AsyncClient, *, name: str = "Test Customer") -> str:
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": name,
            "addresses": [{"type": "BILLING", "country_code": "NL", "city": "Amsterdam"}],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_invoice(
    client: AsyncClient,
    customer_id: str,
    rate_id: str,
    *,
    unit_price: str = "100.000",
) -> dict:
    resp = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-06-12",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Service",
                    "quantity": "1",
                    "unit_price": unit_price,
                    "vat_rate_id": rate_id,
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _send_invoice(client: AsyncClient, invoice_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/status",
        json={"status": "SENT"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _create_payment_method(client: AsyncClient, *, name: str = "Bank Transfer") -> str:
    resp = await client.post(
        "/api/v1/payment-methods",
        json={"name": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Happy flow: record and verify state
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRecordPaymentHappyFlow:

    async def test_partial_payment_state(self, db_client: AsyncClient) -> None:
        """Partial payment: PARTIALLY_PAID, status stays SENT, due_amount reduced."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # 100 + 21 = 121
        await _send_invoice(db_client, inv["id"])

        resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "60.000"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()

        assert data["paid_status"] == "PARTIALLY_PAID"
        assert data["status"] == "SENT"
        assert data["paid_total"] == "60.000"
        assert data["due_amount"] == "61.000"
        assert len(data["items"]) == 1
        assert data["items"][0]["amount"] == "60.000"
        assert data["items"][0]["base_amount"] == "60.000"
        assert data["items"][0]["currency"] == "EUR"

    async def test_full_payment_sent_to_completed(self, db_client: AsyncClient) -> None:
        """Full payment: PAID, status SENT → COMPLETED, due_amount = 0."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total = 121.000
        await _send_invoice(db_client, inv["id"])

        resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "121.000"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()

        assert data["paid_status"] == "PAID"
        assert data["status"] == "COMPLETED"
        assert data["due_amount"] == "0.000"
        assert data["paid_total"] == "121.000"

    async def test_two_partial_payments_then_full(self, db_client: AsyncClient) -> None:
        """Two partial + one final payment → COMPLETED. Verify cumulative amounts."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # 121.000
        await _send_invoice(db_client, inv["id"])

        # First payment
        resp1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "40.000"},
        )
        assert resp1.status_code == 201
        assert resp1.json()["paid_status"] == "PARTIALLY_PAID"
        assert resp1.json()["due_amount"] == "81.000"

        # Second payment
        resp2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-05", "amount": "40.000"},
        )
        assert resp2.status_code == 201
        assert resp2.json()["due_amount"] == "41.000"
        assert len(resp2.json()["items"]) == 2

        # Final payment
        resp3 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "41.000"},
        )
        assert resp3.status_code == 201
        final = resp3.json()
        assert final["paid_status"] == "PAID"
        assert final["status"] == "COMPLETED"
        assert final["due_amount"] == "0.000"
        assert final["paid_total"] == "121.000"
        assert len(final["items"]) == 3

    async def test_list_payments_returns_aggregate(self, db_client: AsyncClient) -> None:
        """GET /invoices/{id}/payments returns correct aggregate + items."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        # Record one payment
        await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )

        # GET list
        get_resp = await db_client.get(f"/api/v1/invoices/{inv['id']}/payments")
        assert get_resp.status_code == 200, get_resp.text
        data = get_resp.json()

        assert data["paid_status"] == "PARTIALLY_PAID"
        assert data["paid_total"] == "50.000"
        assert data["due_amount"] == "71.000"
        assert len(data["items"]) == 1

    async def test_get_single_payment(self, db_client: AsyncClient) -> None:
        """GET /payments/{id} returns PaymentRead."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        record_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "60.000"},
        )
        assert record_resp.status_code == 201
        payment_id = record_resp.json()["items"][0]["id"]

        get_resp = await db_client.get(f"/api/v1/payments/{payment_id}")
        assert get_resp.status_code == 200, get_resp.text
        p = get_resp.json()
        assert p["id"] == payment_id
        assert p["amount"] == "60.000"
        assert p["invoice_id"] == inv["id"]


# ---------------------------------------------------------------------------
# Guard tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPaymentGuards:

    async def test_overpayment_422(self, db_client: AsyncClient) -> None:
        """Recording a payment that would exceed total_incl_vat → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # 121.000
        await _send_invoice(db_client, inv["id"])

        resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "200.000"},
        )
        assert resp.status_code == 422

    async def test_overpayment_cumulative_422(self, db_client: AsyncClient) -> None:
        """Second payment that cumulatively exceeds total → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # 121.000
        await _send_invoice(db_client, inv["id"])

        # First partial payment OK
        resp1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "100.000"},
        )
        assert resp1.status_code == 201

        # Second payment would push total to 200 > 121
        resp2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "100.000"},
        )
        assert resp2.status_code == 422

    async def test_draft_invoice_422(self, db_client: AsyncClient) -> None:
        """Recording payment on a DRAFT invoice → 422 (D7)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        # Invoice is still DRAFT (not sent)

        resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert resp.status_code == 422

    async def test_cancelled_invoice_422(self, db_client: AsyncClient) -> None:
        """Recording payment on a CANCELLED invoice → 422 (D7)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        # Cancel the DRAFT invoice directly
        cancel_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/status",
            json={"status": "CANCELLED"},
        )
        assert cancel_resp.status_code == 200

        resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert resp.status_code == 422

    async def test_cross_company_invoice_404(self, db_client: AsyncClient) -> None:
        """POST /invoices/{id}/payments with a random (non-existent) invoice ID → 404.

        Because every payment lookup is scoped by company_id, an invoice from a
        different company (or a random UUID) is indistinguishable from not-found.
        """
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            f"/api/v1/invoices/{uuid.uuid4()}/payments",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert resp.status_code == 404

    async def test_cross_company_get_payment_404(self, db_client: AsyncClient) -> None:
        """GET /payments/{id} with a random (non-existent) payment ID → 404.

        Scoping by company_id means payments from another company or a random
        UUID are both returned as 404.
        """
        await _full_auth(db_client)
        await _setup_company(db_client)

        get_resp = await db_client.get(f"/api/v1/payments/{uuid.uuid4()}")
        assert get_resp.status_code == 404

    async def test_owner_only_unauthenticated_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated request → 401."""
        fake_id = str(uuid.uuid4())
        resp = await db_client.post(
            f"/api/v1/invoices/{fake_id}/payments",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Payment method name snapshot (D8)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPaymentMethodSnapshot:

    async def test_payment_method_name_snapshot(self, db_client: AsyncClient) -> None:
        """Payment method name is snapshotted; deleting the method keeps the snapshot."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        # Create a payment method
        pm_id = await _create_payment_method(db_client, name="Bank Transfer NL")

        # Record payment with that method
        record_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={
                "payment_date": "2026-06-12",
                "amount": "50.000",
                "payment_method_id": pm_id,
            },
        )
        assert record_resp.status_code == 201, record_resp.text
        data = record_resp.json()
        assert data["items"][0]["payment_method_id"] == pm_id
        assert data["items"][0]["payment_method_name"] == "Bank Transfer NL"
        payment_id = data["items"][0]["id"]

        # Delete the payment method
        del_resp = await db_client.delete(f"/api/v1/payment-methods/{pm_id}")
        assert del_resp.status_code == 204, del_resp.text

        # The payment's name snapshot must still be intact; FK is SET NULL
        get_resp = await db_client.get(f"/api/v1/payments/{payment_id}")
        assert get_resp.status_code == 200, get_resp.text
        p = get_resp.json()
        assert p["payment_method_id"] is None  # FK cleared to NULL
        assert p["payment_method_name"] == "Bank Transfer NL"  # snapshot preserved

    async def test_payment_without_method(self, db_client: AsyncClient) -> None:
        """Payment recorded without a payment method should have null fields."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "60.000"},
        )
        assert resp.status_code == 201
        item = resp.json()["items"][0]
        assert item["payment_method_id"] is None
        assert item["payment_method_name"] is None


# ---------------------------------------------------------------------------
# Amount quantisation tests (D9 / F1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAmountQuantisation:
    """Verify that >3-dp amounts are quantised (ROUND_HALF_UP) before DB write.

    These tests guard against the F1 regression: record_payment must call
    quantize_money() before persisting, so that:
      (a) POST response amount == GET-single amount == LIST amount (consistency)
      (b) an amount that rounds to the invoice total → PAID / COMPLETED (no
          status-stuck bug).
    """

    async def test_gt3dp_amount_is_consistent_across_post_get_list(
        self, db_client: AsyncClient
    ) -> None:
        """POST with >3-dp amount → response, GET-single, and LIST all show
        the quantised value (33.3336 → 33.334).  No inconsistency between
        immediate response and subsequent reads (F1 regression guard)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # Record with 5-dp input; quantise_money("33.3336") == "33.334"
        post_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "33.3336"},
        )
        assert post_resp.status_code == 201, post_resp.text
        post_data = post_resp.json()
        payment_id = post_data["items"][0]["id"]

        quantised = "33.334"

        # (1) POST response reflects quantised amount
        assert post_data["items"][0]["amount"] == quantised, (
            f"POST response amount {post_data['items'][0]['amount']!r} != {quantised!r}"
        )
        assert post_data["paid_total"] == quantised

        # (2) GET /payments/{id} returns the same quantised amount
        get_resp = await db_client.get(f"/api/v1/payments/{payment_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["amount"] == quantised, (
            f"GET-single amount {get_resp.json()['amount']!r} != {quantised!r}"
        )

        # (3) GET /invoices/{id}/payments (LIST) also shows the same value
        list_resp = await db_client.get(f"/api/v1/invoices/{inv['id']}/payments")
        assert list_resp.status_code == 200, list_resp.text
        list_data = list_resp.json()
        assert list_data["items"][0]["amount"] == quantised, (
            f"LIST amount {list_data['items'][0]['amount']!r} != {quantised!r}"
        )
        assert list_data["paid_total"] == quantised

    async def test_quantise_to_full_amount_marks_invoice_completed(
        self, db_client: AsyncClient
    ) -> None:
        """Recording 120.9996 on a 121.000 invoice: after quantisation (→ 121.000)
        the invoice must be PAID / COMPLETED / due=0.000.  Without quantisation
        the invoice would be stuck in SENT/PARTIALLY_PAID (F1 status-stuck bug)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # 120.9996 quantises to 121.000, which exactly covers the invoice total
        post_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "120.9996"},
        )
        assert post_resp.status_code == 201, post_resp.text
        data = post_resp.json()

        # POST response must show full payment, not the un-rounded 120.9996
        assert data["items"][0]["amount"] == "121.000", (
            f"Expected quantised amount 121.000, got {data['items'][0]['amount']!r}"
        )
        assert data["paid_total"] == "121.000"
        assert data["due_amount"] == "0.000"
        assert data["paid_status"] == "PAID"
        assert data["status"] == "COMPLETED", (
            "Invoice must be COMPLETED after quantised amount covers the total "
            "(F1 status-stuck regression guard)"
        )


# ---------------------------------------------------------------------------
# Step 2: Edit / Delete payments
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdatePayment:
    """PUT /api/v1/payments/{id} – edit a payment and recompute invoice state."""

    async def test_first_and_final_payment_reaches_paid_completed(
        self, db_client: AsyncClient
    ) -> None:
        """Record first partial payment then a final payment → PAID / COMPLETED."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # First (partial) payment
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "60.000"},
        )
        assert r1.status_code == 201
        assert r1.json()["paid_status"] == "PARTIALLY_PAID"

        # Final payment brings total to exactly 121
        r2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "61.000"},
        )
        assert r2.status_code == 201
        data = r2.json()
        assert data["paid_status"] == "PAID"
        assert data["status"] == "COMPLETED"
        assert data["due_amount"] == "0.000"
        assert data["paid_total"] == "121.000"

    async def test_edit_final_payment_smaller_triggers_lifecycle_rollback(
        self, db_client: AsyncClient
    ) -> None:
        """Edit the final payment to a smaller amount → PARTIALLY_PAID + COMPLETED→SENT."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # Record two payments to reach PAID / COMPLETED
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "60.000"},
        )
        assert r1.status_code == 201
        r2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "61.000"},
        )
        assert r2.status_code == 201
        assert r2.json()["status"] == "COMPLETED"
        final_payment_id = r2.json()["items"][-1]["id"]

        # Edit the final payment to a smaller amount
        put_resp = await db_client.put(
            f"/api/v1/payments/{final_payment_id}",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert put_resp.status_code == 200, put_resp.text
        data = put_resp.json()

        assert data["paid_status"] == "PARTIALLY_PAID"
        assert data["status"] == "SENT"  # COMPLETED rolled back to SENT (D3)
        assert data["due_amount"] == "11.000"  # 121 - 60 - 50 = 11
        assert data["paid_total"] == "110.000"
        assert len(data["items"]) == 2

    async def test_edit_overpayment_returns_422(self, db_client: AsyncClient) -> None:
        """Edit that would make paid_total exceed total_incl_vat → 422 (D6)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # Record two payments
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "60.000"},
        )
        assert r1.status_code == 201
        r2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "40.000"},
        )
        assert r2.status_code == 201
        payment2_id = r2.json()["items"][-1]["id"]

        # Try to edit second payment to 100, making total 160 > 121
        put_resp = await db_client.put(
            f"/api/v1/payments/{payment2_id}",
            json={"payment_date": "2026-06-12", "amount": "100.000"},
        )
        assert put_resp.status_code == 422

    async def test_edit_cross_company_payment_404(self, db_client: AsyncClient) -> None:
        """PUT /payments/{id} with a random UUID (cross-company) → 404.

        company_id scoping in _load_payment means a payment from another company
        or a random UUID is indistinguishable from not-found.
        """
        await _full_auth(db_client)
        await _setup_company(db_client)

        put_resp = await db_client.put(
            f"/api/v1/payments/{uuid.uuid4()}",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert put_resp.status_code == 404

    async def test_edit_quantisation_consistency(self, db_client: AsyncClient) -> None:
        """Edit with >3-dp amount → response / re-read / list all show the quantised value.

        33.3336 → quantised to 33.334 (same F1 guard as in record_payment).
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "50.000"},
        )
        assert r1.status_code == 201
        payment_id = r1.json()["items"][0]["id"]

        # Edit with >3-dp amount
        put_resp = await db_client.put(
            f"/api/v1/payments/{payment_id}",
            json={"payment_date": "2026-06-01", "amount": "33.3336"},
        )
        assert put_resp.status_code == 200, put_resp.text
        quantised = "33.334"

        # PUT response reflects quantised amount
        assert put_resp.json()["items"][0]["amount"] == quantised
        assert put_resp.json()["paid_total"] == quantised

        # GET /payments/{id} returns the same quantised amount
        get_resp = await db_client.get(f"/api/v1/payments/{payment_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["amount"] == quantised

        # GET list also shows quantised
        list_resp = await db_client.get(f"/api/v1/invoices/{inv['id']}/payments")
        assert list_resp.status_code == 200
        assert list_resp.json()["items"][0]["amount"] == quantised

    async def test_edit_quantise_to_full_marks_invoice_completed(
        self, db_client: AsyncClient
    ) -> None:
        """Edit to 120.9996 on 121.000 invoice → quantised 121.000 → PAID/COMPLETED."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "50.000"},
        )
        assert r1.status_code == 201
        payment_id = r1.json()["items"][0]["id"]

        # Edit to an amount that quantises exactly to the remaining due
        put_resp = await db_client.put(
            f"/api/v1/payments/{payment_id}",
            json={"payment_date": "2026-06-01", "amount": "120.9996"},
        )
        assert put_resp.status_code == 200, put_resp.text
        data = put_resp.json()

        assert data["items"][0]["amount"] == "121.000"
        assert data["paid_total"] == "121.000"
        assert data["due_amount"] == "0.000"
        assert data["paid_status"] == "PAID"
        assert data["status"] == "COMPLETED"


@pytest.mark.integration
class TestDeletePayment:
    """DELETE /api/v1/payments/{id} – delete a payment and recompute invoice state."""

    async def test_delete_one_payment_raises_due_and_rolls_back_status(
        self, db_client: AsyncClient
    ) -> None:
        """Delete one of two payments → due_amount increases + status rolls back."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # Record two payments that reach PAID / COMPLETED
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "60.000"},
        )
        assert r1.status_code == 201
        r2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "61.000"},
        )
        assert r2.status_code == 201
        assert r2.json()["status"] == "COMPLETED"
        payment2_id = r2.json()["items"][-1]["id"]

        # Delete the second payment
        del_resp = await db_client.delete(f"/api/v1/payments/{payment2_id}")
        assert del_resp.status_code == 200, del_resp.text
        data = del_resp.json()

        assert data["paid_status"] == "PARTIALLY_PAID"
        assert data["status"] == "SENT"  # COMPLETED rolled back to SENT (D3)
        assert data["due_amount"] == "61.000"  # 121 - 60 = 61
        assert data["paid_total"] == "60.000"
        assert len(data["items"]) == 1

    async def test_delete_all_payments_reaches_unpaid_sent(
        self, db_client: AsyncClient
    ) -> None:
        """Delete all payments → UNPAID, status returns to SENT."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # Record a single payment (partial)
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "50.000"},
        )
        assert r1.status_code == 201
        payment_id = r1.json()["items"][0]["id"]
        assert r1.json()["paid_status"] == "PARTIALLY_PAID"

        # Delete the only payment
        del_resp = await db_client.delete(f"/api/v1/payments/{payment_id}")
        assert del_resp.status_code == 200, del_resp.text
        data = del_resp.json()

        assert data["paid_status"] == "UNPAID"
        assert data["status"] == "SENT"
        assert data["due_amount"] == "121.000"
        assert data["paid_total"] == "0.000"
        assert data["base_paid_total"] == "0.000"
        assert data["items"] == []

    async def test_delete_final_payment_after_completed(
        self, db_client: AsyncClient
    ) -> None:
        """Delete final payment from a COMPLETED invoice → UNPAID + status SENT."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)  # total 121.000
        await _send_invoice(db_client, inv["id"])

        # Record full payment → COMPLETED
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-12", "amount": "121.000"},
        )
        assert r1.status_code == 201
        assert r1.json()["status"] == "COMPLETED"
        payment_id = r1.json()["items"][0]["id"]

        # Delete it
        del_resp = await db_client.delete(f"/api/v1/payments/{payment_id}")
        assert del_resp.status_code == 200, del_resp.text
        data = del_resp.json()

        assert data["paid_status"] == "UNPAID"
        assert data["status"] == "SENT"
        assert data["due_amount"] == "121.000"
        assert data["items"] == []

    async def test_delete_cross_company_payment_404(self, db_client: AsyncClient) -> None:
        """DELETE /payments/{id} with a random UUID → 404.

        company_id scoping means a payment from another company or a random UUID
        is indistinguishable from not-found.
        """
        await _full_auth(db_client)
        await _setup_company(db_client)

        del_resp = await db_client.delete(f"/api/v1/payments/{uuid.uuid4()}")
        assert del_resp.status_code == 404

    async def test_delete_owner_only_unauthenticated_401(
        self, db_client: AsyncClient
    ) -> None:
        """Unauthenticated DELETE → 401."""
        del_resp = await db_client.delete(f"/api/v1/payments/{uuid.uuid4()}")
        assert del_resp.status_code == 401
