"""Integration tests for M6 step 3 – Convert quote→invoice + Reactivate + APScheduler expiry.

Requires a running PostgreSQL instance (``pytest -m integration``).

Test coverage:
- Convert happy flow: from SENT, ACCEPTED, EXPIRED (soft-expiry)
- Convert copies customer/lines/taxes/discount/treatment to new invoice
- Convert LINE mode: per-line taxes preserved in new invoice
- Convert DOCUMENT mode: document tax entry preserved
- Convert sets invoice status=DRAFT, paid_status=UNPAID, due_amount=total_incl_vat
- Convert sets original quote to ACCEPTED + converted_invoice_id backlink
- Convert already converted → 409
- Convert DRAFT quote → 422
- Convert not found → 404
- Convert unauthenticated → 401
- Reactivate happy flow: EXPIRED → SENT, valid_until extended by default days
- Reactivate with explicit valid_until
- Reactivate non-EXPIRED quote → 422
- Reactivate not found → 404
- expire_due_quotes_all: batch function works across all companies
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared test helpers (duplicated from test_quote_crud_integration.py to keep
# each test module self-contained and avoid cross-file import coupling)
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


def _line(rate_id: str, *, name: str = "Service", qty: str = "1", price: str = "100.000") -> dict:
    return {"name": name, "quantity": qty, "unit_price": price, "vat_rate_id": rate_id}


async def _create_quote(
    client: AsyncClient,
    customer_id: str,
    rate_id: str,
    *,
    tax_mode: str = "LINE",
    amounts_include_vat: bool = False,
    document_vat_rate_id: str | None = None,
    discount: dict | None = None,
    lines: list[dict] | None = None,
    valid_until: str | None = None,
    notes: str | None = None,
) -> dict:
    if lines is None:
        lines = [_line(rate_id)]
    payload: dict = {
        "customer_id": customer_id,
        "quote_date": "2026-06-11",
        "tax_mode": tax_mode,
        "amounts_include_vat": amounts_include_vat,
        "lines": lines,
    }
    if document_vat_rate_id:
        payload["document_vat_rate_id"] = document_vat_rate_id
    if discount:
        payload["discount"] = discount
    if valid_until:
        payload["valid_until"] = valid_until
    if notes:
        payload["notes"] = notes

    resp = await client.post("/api/v1/quotes", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _set_status(client: AsyncClient, quote_id: str, new_status: str) -> dict:
    resp = await client.post(f"/api/v1/quotes/{quote_id}/status", json={"status": new_status})
    assert resp.status_code == 200, f"set_status({new_status}) failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Convert quote → invoice
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestConvertQuote:

    async def test_convert_from_sent_happy_flow(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201, resp.text
        inv = resp.json()

        assert inv["status"] == "DRAFT"
        assert inv["paid_status"] == "UNPAID"
        # Converted invoice is an unnumbered DRAFT; the number is allocated only
        # when it is issued (DRAFT -> SENT), not at conversion time.
        assert inv["invoice_number"] is None
        assert inv["sequence_number"] is None

        issue_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/status", json={"status": "SENT"}
        )
        assert issue_resp.status_code == 200, issue_resp.text
        issued = issue_resp.json()
        assert issued["invoice_number"].startswith("INV-")

    async def test_convert_from_expired_soft_expiry(self, db_client: AsyncClient) -> None:
        """EXPIRED quotes must be convertible (soft-expiry rule)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q = await _create_quote(db_client, customer_id, rate_21, valid_until=yesterday)
        await _set_status(db_client, q["id"], "SENT")
        # Trigger read-time expiry
        await db_client.get(f"/api/v1/quotes/{q['id']}")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "DRAFT"

    async def test_convert_from_accepted(self, db_client: AsyncClient) -> None:
        """Manually ACCEPTED quote (not yet converted) can be converted."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")
        await _set_status(db_client, q["id"], "ACCEPTED")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "DRAFT"

    async def test_convert_line_mode_correct_amounts(self, db_client: AsyncClient) -> None:
        """Invoice amounts match original quote amounts (pricing re-run produces same result)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21, lines=[
            _line(rate_21, name="Widget A", qty="2", price="100"),
            _line(rate_21, name="Widget B", qty="1", price="50"),
        ])
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()

        assert inv["subtotal_excl_vat"] == q["subtotal_excl_vat"]
        assert inv["vat_total"] == q["vat_total"]
        assert inv["total_incl_vat"] == q["total_incl_vat"]
        assert len(inv["lines"]) == 2

    async def test_convert_document_mode(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        lines = [{"name": "A", "quantity": "1", "unit_price": "200"}]
        q = await _create_quote(
            db_client, customer_id, rate_21,
            tax_mode="DOCUMENT",
            document_vat_rate_id=rate_21,
            lines=lines,
        )
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["tax_mode"] == "DOCUMENT"
        assert len(inv["taxes"]) == 1
        assert inv["taxes"][0]["vat_rate_percent"] == "21.000"

    async def test_convert_sets_due_amount(self, db_client: AsyncClient) -> None:
        """Invoice due_amount must equal total_incl_vat (M5 contract, no payments yet)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["due_amount"] == inv["total_incl_vat"]

    async def test_convert_sets_quote_accepted_with_backlink(self, db_client: AsyncClient) -> None:
        """After convert, the original quote must be ACCEPTED with converted_invoice_id set."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")

        convert_resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert convert_resp.status_code == 201
        new_inv_id = convert_resp.json()["id"]

        quote_resp = await db_client.get(f"/api/v1/quotes/{q['id']}")
        assert quote_resp.status_code == 200
        quote_after = quote_resp.json()
        assert quote_after["status"] == "ACCEPTED"
        assert quote_after["converted_invoice_id"] == new_inv_id

    async def test_convert_copies_customer_and_vat_treatment(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["customer_id"] == customer_id
        assert inv["vat_treatment_snapshot"]["code"] == q["vat_treatment_snapshot"]["code"]

    async def test_convert_copies_discount(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(
            db_client, customer_id, rate_21,
            discount={"type": "FIXED", "value": "10"},
        )
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()
        assert inv["discount_type"] == "FIXED"
        assert inv["discount_value"] == "10.000"

    async def test_convert_copies_notes(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21, notes="Important note")
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        assert resp.json()["notes"] == "Important note"

    async def test_convert_already_converted_returns_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")

        # First convert succeeds
        r1 = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert r1.status_code == 201

        # Second convert must fail with 409
        r2 = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert r2.status_code == 409

    async def test_convert_draft_quote_returns_422(self, db_client: AsyncClient) -> None:
        """DRAFT quotes cannot be converted."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        assert q["status"] == "DRAFT"

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 422

    async def test_convert_not_found_returns_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(f"/api/v1/quotes/{uuid.uuid4()}/convert")
        assert resp.status_code == 404

    async def test_convert_unauthenticated_returns_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.post(f"/api/v1/quotes/{uuid.uuid4()}/convert")
        assert resp.status_code == 401

    async def test_convert_new_invoice_has_new_number(self, db_client: AsyncClient) -> None:
        """Issuing a converted invoice allocates a fresh invoice number.

        The number is independent of the quote number and is allocated only at
        the DRAFT -> SENT issue transition, not at conversion.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q["id"], "SENT")

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()
        # Converted invoice is unnumbered until issued.
        assert inv["invoice_number"] is None

        issue_resp = await db_client.post(
            f"/api/v1/invoices/{inv['id']}/status", json={"status": "SENT"}
        )
        assert issue_resp.status_code == 200, issue_resp.text
        issued = issue_resp.json()
        assert issued["invoice_number"] is not None
        assert issued["invoice_number"] != q["quote_number"]

    async def test_convert_snapshot_not_affected_by_vat_rate_change(
        self, db_client: AsyncClient
    ) -> None:
        """Invoice amounts must equal the quote snapshot, not re-computed from current VAT dict.

        Regression for P1: Convert previously re-ran compute_pricing() against the
        current vat_rate.percent, causing drift if the rate was edited after the
        quote was created.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        rate_label = seeds["rates"]["NL standard (21%)"]["label"]

        # Create and SEND quote; snapshot captures 21% at this moment.
        q = await _create_quote(db_client, customer_id, rate_21, lines=[
            _line(rate_21, name="Widget", qty="1", price="100"),
        ])
        await _set_status(db_client, q["id"], "SENT")

        # Mutate the VAT rate to a different percentage.
        r = await db_client.put(
            f"/api/v1/vat-rates/{rate_21}",
            json={"label": rate_label, "percent": "9", "active": True},
        )
        assert r.status_code == 200, r.text

        # Convert: invoice must still carry the original 21% snapshot, not 9%.
        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert resp.status_code == 201
        inv = resp.json()

        assert inv["subtotal_excl_vat"] == q["subtotal_excl_vat"]
        assert inv["vat_total"] == q["vat_total"]
        assert inv["total_incl_vat"] == q["total_incl_vat"]
        # Line-level snapshot must show the original rate
        assert len(inv["lines"]) == 1
        assert inv["lines"][0]["vat_rate_percent"] == q["lines"][0]["vat_rate_percent"]
        assert inv["vat_treatment_snapshot"]["code"] == q["vat_treatment_snapshot"]["code"]


# ---------------------------------------------------------------------------
# Reactivate expired quote
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReactivateQuote:

    async def _make_expired_quote(
        self,
        db_client: AsyncClient,
        customer_id: str,
        rate_id: str,
    ) -> dict:
        """Create a quote, mark it SENT with yesterday's valid_until, then trigger expiry."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q = await _create_quote(db_client, customer_id, rate_id, valid_until=yesterday)
        await _set_status(db_client, q["id"], "SENT")
        # Trigger read-time expiry via GET
        expired = await db_client.get(f"/api/v1/quotes/{q['id']}")
        assert expired.json()["status"] == "EXPIRED"
        return q

    async def test_reactivate_happy_flow(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await self._make_expired_quote(db_client, customer_id, rate_21)

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/reactivate", json={})
        assert resp.status_code == 200, resp.text
        activated = resp.json()
        assert activated["status"] == "SENT"
        assert activated["valid_until"] is not None
        # valid_until should be in the future (today + 30 days by default)
        new_valid_until = date.fromisoformat(activated["valid_until"])
        assert new_valid_until > date.today()

    async def test_reactivate_with_explicit_valid_until(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await self._make_expired_quote(db_client, customer_id, rate_21)

        future_date = (date.today() + timedelta(days=60)).isoformat()
        resp = await db_client.post(
            f"/api/v1/quotes/{q['id']}/reactivate",
            json={"valid_until": future_date},
        )
        assert resp.status_code == 200
        assert resp.json()["valid_until"] == future_date
        assert resp.json()["status"] == "SENT"

    async def test_reactivate_uses_company_default_days(self, db_client: AsyncClient) -> None:
        """valid_until falls back to today + company default valid days."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Set company default to 14 days
        await db_client.put(
            "/api/v1/settings/quote-default-valid-days",
            json={"default_valid_days": 14},
        )

        q = await self._make_expired_quote(db_client, customer_id, rate_21)

        resp = await db_client.post(f"/api/v1/quotes/{q['id']}/reactivate", json={})
        assert resp.status_code == 200
        expected = (date.today() + timedelta(days=14)).isoformat()
        assert resp.json()["valid_until"] == expected

    async def test_reactivate_with_past_valid_until_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """Passing a valid_until in the past must be rejected with 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await self._make_expired_quote(db_client, customer_id, rate_21)

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        resp = await db_client.post(
            f"/api/v1/quotes/{q['id']}/reactivate",
            json={"valid_until": yesterday},
        )
        assert resp.status_code == 422

    async def test_reactivate_with_today_valid_until_is_accepted(
        self, db_client: AsyncClient
    ) -> None:
        """valid_until = today is the minimum allowed value (not a past date)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await self._make_expired_quote(db_client, customer_id, rate_21)

        today = date.today().isoformat()
        resp = await db_client.post(
            f"/api/v1/quotes/{q['id']}/reactivate",
            json={"valid_until": today},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "SENT"
        assert resp.json()["valid_until"] == today

    async def test_reactivate_non_expired_returns_422(self, db_client: AsyncClient) -> None:
        """Reactivating a SENT or DRAFT quote must return 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # DRAFT quote
        q_draft = await _create_quote(db_client, customer_id, rate_21)
        resp = await db_client.post(f"/api/v1/quotes/{q_draft['id']}/reactivate", json={})
        assert resp.status_code == 422

        # SENT quote
        q_sent = await _create_quote(db_client, customer_id, rate_21)
        await _set_status(db_client, q_sent["id"], "SENT")
        resp = await db_client.post(f"/api/v1/quotes/{q_sent['id']}/reactivate", json={})
        assert resp.status_code == 422

    async def test_reactivate_not_found_returns_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(f"/api/v1/quotes/{uuid.uuid4()}/reactivate", json={})
        assert resp.status_code == 404

    async def test_reactivate_unauthenticated_returns_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.post(f"/api/v1/quotes/{uuid.uuid4()}/reactivate", json={})
        assert resp.status_code == 401

    async def test_reactivate_then_convert(self, db_client: AsyncClient) -> None:
        """Reactivated quote (SENT) can subsequently be converted."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q = await self._make_expired_quote(db_client, customer_id, rate_21)

        react_resp = await db_client.post(f"/api/v1/quotes/{q['id']}/reactivate", json={})
        assert react_resp.status_code == 200

        conv_resp = await db_client.post(f"/api/v1/quotes/{q['id']}/convert")
        assert conv_resp.status_code == 201
        assert conv_resp.json()["status"] == "DRAFT"


# ---------------------------------------------------------------------------
# expire_due_quotes_all (APScheduler job function)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpireDueQuotesAll:

    async def test_batch_expire_via_list_endpoint(self, db_client: AsyncClient) -> None:
        """list_quotes calls expire_due_quotes which mirrors expire_due_quotes_all.

        Verifies that a SENT quote with expired valid_until appears as EXPIRED
        in the first list request (batch expiry runs before filtering).
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q = await _create_quote(db_client, customer_id, rate_21, valid_until=yesterday)
        q_id = q["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        # First list call – batch-expiry must run before filtering
        list_resp = await db_client.get("/api/v1/quotes?status=EXPIRED")
        assert list_resp.status_code == 200
        ids = [item["id"] for item in list_resp.json()["items"]]
        assert q_id in ids

    async def test_expire_idempotent_via_list(self, db_client: AsyncClient) -> None:
        """Calling list multiple times for already-expired quotes does not create duplicates."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q = await _create_quote(db_client, customer_id, rate_21, valid_until=yesterday)
        q_id = q["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        # Two consecutive list calls
        r1 = await db_client.get("/api/v1/quotes?status=EXPIRED")
        r2 = await db_client.get("/api/v1/quotes?status=EXPIRED")

        count1 = r1.json()["total"]
        count2 = r2.json()["total"]
        assert count1 == count2, "Idempotent: second run must not change the count"
        assert count1 >= 1

    async def test_expire_due_quotes_all_service_directly(
        self, db_client: AsyncClient, db_session_maker: object
    ) -> None:
        """Call expire_due_quotes_all directly via the service layer."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from jai.services.quote import expire_due_quotes_all

        assert isinstance(db_session_maker, async_sessionmaker)
        session_maker: async_sessionmaker[AsyncSession] = db_session_maker  # type: ignore[assignment]

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q = await _create_quote(db_client, customer_id, rate_21, valid_until=yesterday)
        q_id = q["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        # Call the all-companies batch function directly
        async with session_maker() as session:
            count = await expire_due_quotes_all(session)
            await session.commit()

        assert count >= 1

        # A second run must flip nothing (idempotent)
        async with session_maker() as session:
            count2 = await expire_due_quotes_all(session)
            await session.commit()

        assert count2 == 0
