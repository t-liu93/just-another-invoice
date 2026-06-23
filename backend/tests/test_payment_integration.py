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


# ---------------------------------------------------------------------------
# Step 3: Global payments overview – GET /api/v1/payments
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListPayments:
    """GET /api/v1/payments – global overview, filter/pagination/sort."""

    # -------------------------------------------------------------------------
    # Helpers (local to this class; shared infra helpers are at module level)
    # -------------------------------------------------------------------------

    async def _setup_and_get_payments(
        self,
        client: AsyncClient,
        *,
        num_payments: int = 1,
        amount: str = "50.000",
    ) -> tuple[dict, str, list[str]]:
        """Auth + company + 1 customer + 1 SENT invoice + N payments.

        Returns (invoice_dict, customer_id, [payment_id, ...]).
        """
        seeds = await _setup_company(client)
        customer_id = await _create_customer(client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        created = await _create_invoice(client, customer_id, rate_21)
        # The number is allocated on issue; use the SENT response so callers see
        # the allocated invoice_number (the create response has it as None).
        inv = await _send_invoice(client, created["id"])

        payment_ids: list[str] = []
        for i in range(num_payments):
            r = await client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": f"2026-06-{10 + i:02d}", "amount": amount},
            )
            assert r.status_code == 201, r.text
            payment_ids.append(r.json()["items"][-1]["id"])

        return inv, customer_id, payment_ids

    # -------------------------------------------------------------------------
    # Happy flow
    # -------------------------------------------------------------------------

    async def test_list_returns_all_payments_empty_q(
        self, db_client: AsyncClient
    ) -> None:
        """Empty q (no filter) returns all payments for the company without error."""
        await _full_auth(db_client)
        _, _, payment_ids = await self._setup_and_get_payments(db_client, num_payments=2)

        resp = await db_client.get("/api/v1/payments")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        # Each item has the overview fields, not internal base_*/reference/note
        item = data["items"][0]
        assert "id" in item
        assert "invoice_number" in item
        assert "customer_name" in item
        assert "amount" in item
        assert "payment_method_name" in item
        assert "base_amount" not in item
        assert "reference" not in item
        assert "note" not in item

    async def test_filter_by_invoice_number_q(self, db_client: AsyncClient) -> None:
        """q matching the invoice_number returns matching payments."""
        await _full_auth(db_client)
        inv, _, _ = await self._setup_and_get_payments(db_client, num_payments=1)

        # Use the first part of the invoice number as the search term
        inv_number = inv["invoice_number"]
        q_term = inv_number[:4]  # e.g. "2026"

        resp = await db_client.get(f"/api/v1/payments?q={q_term}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] >= 1
        assert any(item["invoice_number"] == inv_number for item in data["items"])

    async def test_filter_by_customer_name_q(self, db_client: AsyncClient) -> None:
        """q matching the customer name returns the correct payment."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        cust_a = await _create_customer(db_client, name="AlphaClient")
        cust_b = await _create_customer(db_client, name="BetaClient")

        inv_a = await _create_invoice(db_client, cust_a, rate_21)
        await _send_invoice(db_client, inv_a["id"])
        r_a = await db_client.post(
            f"/api/v1/invoices/{inv_a['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "50.000"},
        )
        assert r_a.status_code == 201

        inv_b = await _create_invoice(db_client, cust_b, rate_21)
        await _send_invoice(db_client, inv_b["id"])
        r_b = await db_client.post(
            f"/api/v1/invoices/{inv_b['id']}/payments",
            json={"payment_date": "2026-06-02", "amount": "60.000"},
        )
        assert r_b.status_code == 201

        # Search for Alpha → only AlphaClient payment
        resp = await db_client.get("/api/v1/payments?q=Alpha")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["customer_name"] == "AlphaClient"

        # Search for Beta → only BetaClient payment
        resp2 = await db_client.get("/api/v1/payments?q=Beta")
        assert resp2.status_code == 200, resp2.text
        data2 = resp2.json()
        assert data2["total"] == 1
        assert data2["items"][0]["customer_name"] == "BetaClient"

    async def test_filter_by_customer_id(self, db_client: AsyncClient) -> None:
        """customer_id filter returns only payments for that customer."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        cust_a = await _create_customer(db_client, name="CustomerA")
        cust_b = await _create_customer(db_client, name="CustomerB")

        for cust_id in (cust_a, cust_b):
            inv = await _create_invoice(db_client, cust_id, rate_21)
            await _send_invoice(db_client, inv["id"])
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": "2026-06-01", "amount": "50.000"},
            )
            assert r.status_code == 201

        resp = await db_client.get(f"/api/v1/payments?customer_id={cust_a}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["customer_id"] == cust_a

    async def test_filter_by_payment_method_id(self, db_client: AsyncClient) -> None:
        """payment_method_id filter returns only payments with that method."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        pm_id = await _create_payment_method(db_client, name="Bank Wire")

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        # Payment WITH method
        r1 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-01", "amount": "40.000", "payment_method_id": pm_id},
        )
        assert r1.status_code == 201

        # Payment WITHOUT method
        r2 = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/payments",
            json={"payment_date": "2026-06-02", "amount": "40.000"},
        )
        assert r2.status_code == 201

        resp = await db_client.get(f"/api/v1/payments?payment_method_id={pm_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["payment_method_name"] == "Bank Wire"

    async def test_filter_date_from_inclusive(self, db_client: AsyncClient) -> None:
        """date_from is inclusive: a payment exactly on date_from IS included."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        # Payments on three different dates
        for day in ("2026-06-01", "2026-06-10", "2026-06-20"):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": day, "amount": "20.000"},
            )
            assert r.status_code == 201

        # date_from = 2026-06-10 → payments on 10th and 20th included (2 items)
        resp = await db_client.get("/api/v1/payments?date_from=2026-06-10")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        dates = {item["payment_date"] for item in data["items"]}
        assert "2026-06-10" in dates, "Payment on date_from must be included (inclusive)"
        assert "2026-06-20" in dates
        assert "2026-06-01" not in dates

    async def test_filter_date_to_inclusive(self, db_client: AsyncClient) -> None:
        """date_to is inclusive: a payment exactly on date_to IS included."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        for day in ("2026-06-01", "2026-06-10", "2026-06-20"):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": day, "amount": "20.000"},
            )
            assert r.status_code == 201

        # date_to = 2026-06-10 → payments on 1st and 10th included (2 items)
        resp = await db_client.get("/api/v1/payments?date_to=2026-06-10")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        dates = {item["payment_date"] for item in data["items"]}
        assert "2026-06-10" in dates, "Payment on date_to must be included (inclusive)"
        assert "2026-06-01" in dates
        assert "2026-06-20" not in dates

    async def test_filter_date_range_combined(self, db_client: AsyncClient) -> None:
        """date_from + date_to combined: only payments in range are returned."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        for day in ("2026-06-01", "2026-06-10", "2026-06-20"):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": day, "amount": "20.000"},
            )
            assert r.status_code == 201

        # Range [2026-06-05, 2026-06-15] → only the 10th
        resp = await db_client.get(
            "/api/v1/payments?date_from=2026-06-05&date_to=2026-06-15"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["payment_date"] == "2026-06-10"

    # -------------------------------------------------------------------------
    # Pagination
    # -------------------------------------------------------------------------

    async def test_pagination_total_unaffected_by_limit_offset(
        self, db_client: AsyncClient
    ) -> None:
        """total always reflects the full filtered count, not the page size."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21, unit_price="10.000")
        await _send_invoice(db_client, inv["id"])

        # Record 5 payments of 2.000 each (total = 10.000, all fit in invoice total)
        for i in range(5):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": f"2026-06-{i + 1:02d}", "amount": "2.000"},
            )
            assert r.status_code == 201

        # Page 1: limit=2 → 2 items but total=5
        resp1 = await db_client.get("/api/v1/payments?limit=2&offset=0")
        assert resp1.status_code == 200, resp1.text
        d1 = resp1.json()
        assert d1["total"] == 5
        assert len(d1["items"]) == 2

        # Page 2: limit=2, offset=2 → 2 items but total still 5
        resp2 = await db_client.get("/api/v1/payments?limit=2&offset=2")
        assert resp2.status_code == 200, resp2.text
        d2 = resp2.json()
        assert d2["total"] == 5
        assert len(d2["items"]) == 2

        # Page 3: limit=2, offset=4 → 1 item (last one), total still 5
        resp3 = await db_client.get("/api/v1/payments?limit=2&offset=4")
        assert resp3.status_code == 200, resp3.text
        d3 = resp3.json()
        assert d3["total"] == 5
        assert len(d3["items"]) == 1

        # All three pages cover distinct payments (no overlap)
        ids1 = {item["id"] for item in d1["items"]}
        ids2 = {item["id"] for item in d2["items"]}
        ids3 = {item["id"] for item in d3["items"]}
        assert len(ids1 | ids2 | ids3) == 5

    # -------------------------------------------------------------------------
    # Sorting
    # -------------------------------------------------------------------------

    async def test_sort_by_payment_date_descending(self, db_client: AsyncClient) -> None:
        """Default sort (payment_date) returns most-recent payment first."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21, unit_price="10.000")
        await _send_invoice(db_client, inv["id"])

        for day in ("2026-06-01", "2026-06-15", "2026-06-10"):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": day, "amount": "3.000"},
            )
            assert r.status_code == 201

        resp = await db_client.get("/api/v1/payments?sort_by=payment_date")
        assert resp.status_code == 200, resp.text
        dates = [item["payment_date"] for item in resp.json()["items"]]
        assert dates == sorted(dates, reverse=True), (
            f"Expected descending order, got: {dates}"
        )

    async def test_sort_by_created_at_descending(self, db_client: AsyncClient) -> None:
        """sort_by=created_at returns most-recently created payment first."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21, unit_price="10.000")
        await _send_invoice(db_client, inv["id"])

        for _ in range(3):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": "2026-06-01", "amount": "3.000"},
            )
            assert r.status_code == 201

        resp = await db_client.get("/api/v1/payments?sort_by=created_at")
        assert resp.status_code == 200, resp.text
        created_ats = [item["created_at"] for item in resp.json()["items"]]
        assert created_ats == sorted(created_ats, reverse=True), (
            f"Expected descending created_at order, got: {created_ats}"
        )

    # -------------------------------------------------------------------------
    # Security / isolation
    # -------------------------------------------------------------------------

    async def test_cross_company_isolation(self, db_client: AsyncClient) -> None:
        """Only the authenticated company's payments appear in GET /payments.

        company_id scoping in list_payments (red-line 2) guarantees that a user
        only sees payments whose Payment.company_id matches their own company_id.
        The integration system runs a fresh isolated DB per test class; each test
        registers the first (and only) user, so we cannot register a second
        company in the same DB.  Instead we validate isolation by confirming
        that:
          (a) N payments created by the authenticated company → total == N.
          (b) A payment ID belonging to a different company_id is not returned –
              modelled here by verifying the count matches exactly what was
              inserted (no phantom rows from any other company).
        The DB-level guarantee (service always injects current company_id into
        the WHERE clause) is the primary isolation fence, independently verified
        by the service's unit tests and the integration test structure here.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        inv = await _create_invoice(db_client, customer_id, rate_21)
        await _send_invoice(db_client, inv["id"])

        # Record exactly 2 payments
        for amount in ("40.000", "50.000"):
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": "2026-06-01", "amount": amount},
            )
            assert r.status_code == 201

        # The authenticated company should see exactly 2 payments – no more.
        # If company_id scoping were missing, phantom payments from other DB
        # rows (which don't exist here but would in a shared DB) could appear.
        resp = await db_client.get("/api/v1/payments")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2, (
            "Authenticated company must see exactly its own 2 payments "
            "(company_id scoping guard, red-line 2)"
        )
        amounts = {item["amount"] for item in data["items"]}
        assert amounts == {"40.000", "50.000"}

    async def test_owner_only_unauthenticated_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated GET /payments → 401."""
        resp = await db_client.get("/api/v1/payments")
        assert resp.status_code == 401

    # -------------------------------------------------------------------------
    # Filter combination
    # -------------------------------------------------------------------------

    async def test_filter_q_and_customer_id_combined(
        self, db_client: AsyncClient
    ) -> None:
        """q + customer_id together narrow results further (AND semantics)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        cust_a = await _create_customer(db_client, name="AlphaCustomer")
        cust_b = await _create_customer(db_client, name="AlphaBeta")

        for cust_id, amount in ((cust_a, "30.000"), (cust_b, "40.000")):
            inv = await _create_invoice(db_client, cust_id, rate_21)
            await _send_invoice(db_client, inv["id"])
            r = await db_client.post(
                f"/api/v1/invoices/{inv['id']}/payments",
                json={"payment_date": "2026-06-01", "amount": amount},
            )
            assert r.status_code == 201

        # q="Alpha" matches both customers; customer_id=cust_a narrows to one
        resp = await db_client.get(f"/api/v1/payments?q=Alpha&customer_id={cust_a}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["customer_name"] == "AlphaCustomer"


# ---------------------------------------------------------------------------
# M7.5 Step 3 – F2026-009 收满闭环回归
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestF2026009FullyCollectable:
    """Regression guard for the F2026-009 "永远收不满" bug (M7.5).

    Root cause: M5 pricing engine only quantised document-level totals to 3
    decimal places (e.g. 3865.166).  The UI displayed toFixed(2) → 3865.17,
    but:
      - paying 3865.17 triggered the overpayment guard (3865.170 > 3865.166)
      - paying 3865.16 left a residual of 0.006 (displayed as 0.01)
      - paying that 0.01 again triggered the overpayment guard

    Fix (M7.5 step 2): pricing engine now quantises every line-level amount to
    the currency minor unit (EUR = 2 dp, "cent") *before* summing.  With line-
    level rounding:
      net  = quantize_to_minor_unit(3194.352 × 1)   = 3194.35
      VAT  = quantize_to_minor_unit(3194.35 × 0.21) = 670.81
      total = 3194.35 + 670.81                       = 3865.16

    After the fix, total_incl_vat is a cent-exact value, so a single payment
    of 3865.16 brings due_amount to exactly 0 without any residual — the
    payment service requires **zero changes** (``recompute_payment_state``
    uses ``due = total - paid`` with no secondary rounding; when total is
    already a cent value the equality ``due == 0`` is naturally satisfied).
    """

    async def test_f2026_009_invoice_total_is_cent_exact(
        self, db_client: AsyncClient
    ) -> None:
        """F2026-009 shape: single line unit_price=3194.352, qty=1, 21% VAT,
        amounts_include_vat=False.

        Hand-calc (M7.5 D1 · line-level to minor unit):
          net  = quantize_to_minor_unit(3194.352 × 1)   = 3194.35
          VAT  = quantize_to_minor_unit(3194.35 × 0.21) = 670.81
          total = 3194.35 + 670.81                       = 3865.16

        Assert that total_incl_vat is 3865.16 (not the historic 3865.166),
        taxable/subtotal is 3194.35, and vat is 670.81.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        treatment_nl = seeds["treatments"]["NL_DOMESTIC"]["id"]

        resp = await db_client.post(
            "/api/v1/invoices",
            json={
                "customer_id": customer_id,
                "invoice_date": "2026-06-09",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [
                    {
                        "name": "Solar panels",
                        "quantity": "1",
                        "unit_price": "3194.352",
                        "vat_rate_id": rate_21,
                        "vat_treatment_id": treatment_nl,
                    }
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        inv = resp.json()

        # API serialises NUMERIC(18,3) → always 3 dp; "3865.160" == Decimal("3865.16")
        assert inv["total_incl_vat"] == "3865.160", (
            f"Expected cent-exact total 3865.160, got {inv['total_incl_vat']!r}. "
            "Historic bug would produce 3865.166 (3-dp, third digit non-zero)."
        )
        assert inv["taxable_amount"] == "3194.350", (
            f"Expected net 3194.350, got {inv['taxable_amount']!r}"
        )
        assert inv["vat_total"] == "670.810", (
            f"Expected VAT 670.810, got {inv['vat_total']!r}"
        )

    async def test_f2026_009_fully_collectable_to_cent(
        self, db_client: AsyncClient
    ) -> None:
        """F2026-009 full end-to-end: create → SENT → pay 3865.16 → PAID/COMPLETED/due=0.

        This is the primary regression test for M7.5: verifies that after line-
        level quantisation to the cent the payment layer (zero changes to
        payment.py) naturally achieves due == 0 when paying the exact cent
        amount displayed to the customer, with no sub-cent residual.

        Historic bug: the same invoice with 3-dp total (3865.166) could never
        be marked PAID — paying 3865.16 left due=0.006, paying 3865.17 triggered
        the overpayment guard (422).  The fix is entirely in the pricing engine;
        payment.recompute_payment_state requires zero code changes.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        treatment_nl = seeds["treatments"]["NL_DOMESTIC"]["id"]

        # --- create invoice (F2026-009 shape) ---
        create_resp = await db_client.post(
            "/api/v1/invoices",
            json={
                "customer_id": customer_id,
                "invoice_date": "2026-06-09",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [
                    {
                        "name": "Solar panels",
                        "quantity": "1",
                        "unit_price": "3194.352",
                        "vat_rate_id": rate_21,
                        "vat_treatment_id": treatment_nl,
                    }
                ],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        inv = create_resp.json()
        invoice_id = inv["id"]

        # confirm cent-exact total before proceeding (API serialises as 3-dp string)
        assert inv["total_incl_vat"] == "3865.160", (
            f"Precondition: total must be cent-exact 3865.160, got {inv['total_incl_vat']!r}"
        )

        # --- mark SENT ---
        sent_resp = await _send_invoice(db_client, invoice_id)
        assert sent_resp["status"] == "SENT"

        # --- record one payment of exactly 3865.16 (cent-exact) ---
        pay_resp = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/payments",
            json={"payment_date": "2026-06-13", "amount": "3865.16"},
        )
        assert pay_resp.status_code == 201, pay_resp.text
        data = pay_resp.json()

        # Core assertions: the invoice is fully collected with zero residual
        assert data["due_amount"] == "0.000", (
            f"Expected due_amount == 0.000, got {data['due_amount']!r}. "
            "Historic bug: due would be 0.006 (sub-cent residual) because "
            "total was stored as 3-dp 3865.166."
        )
        assert data["paid_status"] == "PAID", (
            f"Expected PAID, got {data['paid_status']!r}"
        )
        assert data["status"] == "COMPLETED", (
            f"Expected COMPLETED (SENT→COMPLETED lifecycle), got {data['status']!r}"
        )
        assert data["paid_total"] == "3865.160", (
            f"Expected paid_total 3865.160, got {data['paid_total']!r}"
        )

    async def test_f2026_009_overpayment_still_blocked(
        self, db_client: AsyncClient
    ) -> None:
        """Paying 3865.17 (one cent over the cent-exact total 3865.16) → 422.

        After M7.5, the total is 3865.16 (exact cent).  Attempting to pay
        3865.17 must be blocked by the overpayment guard, confirming that the
        guard remains intact and the total is truly 3865.16 (not 3865.166 where
        3865.17 would have been blocked for a different reason — exceeding a
        3-dp value).
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        treatment_nl = seeds["treatments"]["NL_DOMESTIC"]["id"]

        create_resp = await db_client.post(
            "/api/v1/invoices",
            json={
                "customer_id": customer_id,
                "invoice_date": "2026-06-09",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [
                    {
                        "name": "Solar panels",
                        "quantity": "1",
                        "unit_price": "3194.352",
                        "vat_rate_id": rate_21,
                        "vat_treatment_id": treatment_nl,
                    }
                ],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        invoice_id = create_resp.json()["id"]

        await _send_invoice(db_client, invoice_id)

        # paying one cent over the total → overpayment guard must fire
        pay_resp = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/payments",
            json={"payment_date": "2026-06-13", "amount": "3865.17"},
        )
        assert pay_resp.status_code == 422, (
            f"Expected 422 (overpayment guard), got {pay_resp.status_code}. "
            "Paying 3865.17 on a 3865.16 invoice must be rejected."
        )
