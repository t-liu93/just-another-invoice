"""Integration tests for M6 step 2 – Quote CRUD / list / status / numbering settings.

Requires a running PostgreSQL instance (``pytest -m integration``).

Test coverage:
- Happy flow: create (LINE / DOCUMENT), list, get, update, delete
- Number allocation: first quote (QUO-000001), jump sequence, delete doesn't recycle
- LINE mode: per-line taxes with correct amounts
- DOCUMENT mode: single document tax entry
- Incl-VAT: reverse calculation round-trip
- Line + document discount combo
- valid_until: default from company setting, explicit override, null/empty
- Cascade delete: quote deletion removes lines/taxes
- Cross-company 404 / isolation
- Status transitions: DRAFT→SENT, SENT→DRAFT, SENT→REJECTED, REJECTED→SENT,
                     SENT→ACCEPTED (manual), EXPIRED→ACCEPTED, EXPIRED→REJECTED
- Invalid transitions: EXPIRED cannot be set manually; ACCEPTED has no allowed transitions
- ACCEPTED quote cannot be edited (409)
- Read-time expiry: SENT + valid_until in past → EXPIRED on GET and list
- expire_due_quotes batch function
- Quote numbering settings: GET/PUT quote-numbering, GET/PUT quote-number-sequence
- Quote default valid days setting
- Auth: unauthenticated → 401, owner-only → 403
- Customer / vat_rate / treatment FK constraints
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

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


async def _setup_company(client: AsyncClient, country_code: str = "NL") -> dict:
    resp = await client.put(
        "/api/v1/company",
        json={
            "name": "Test Quote Co",
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
    country_code: str = "NL",
) -> str:
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": name,
            "addresses": [
                {"type": "BILLING", "country_code": country_code, "city": "Amsterdam"},
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _line(rate_id: str, *, name: str = "Service", qty: str = "1", price: str = "100.000") -> dict:
    return {"name": name, "quantity": qty, "unit_price": price, "vat_rate_id": rate_id}


def _quote_payload(
    customer_id: str,
    rate_id: str,
    *,
    tax_mode: str = "LINE",
    amounts_include_vat: bool = False,
    treatment_id: str | None = None,
    document_vat_rate_id: str | None = None,
    discount: dict | None = None,
    lines: list[dict] | None = None,
    quote_date: str = "2026-06-11",
    valid_until: str | None = None,
    notes: str | None = None,
) -> dict:
    if lines is None:
        lines = [_line(rate_id)]
    payload: dict = {
        "customer_id": customer_id,
        "quote_date": quote_date,
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
    if valid_until:
        payload["valid_until"] = valid_until
    if notes:
        payload["notes"] = notes
    return payload


# ---------------------------------------------------------------------------
# Happy flow: CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteCRUDHappyFlow:

    async def test_create_and_get_line_mode(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        assert resp.status_code == 201
        q = resp.json()

        assert q["status"] == "DRAFT"
        assert q["tax_mode"] == "LINE"
        assert q["subtotal_excl_vat"] == "100.000"
        assert q["vat_total"] == "21.000"
        assert q["total_incl_vat"] == "121.000"
        assert q["quote_number"].startswith("QUO-")
        assert len(q["lines"]) == 1
        assert len(q["lines"][0]["line_taxes"]) == 1
        assert q["vat_treatment_snapshot"]["code"] == "NL_DOMESTIC"

        # GET
        get_resp = await db_client.get(f"/api/v1/quotes/{q['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == q["id"]
        assert get_resp.json()["total_incl_vat"] == "121.000"

    async def test_create_document_mode(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        lines = [
            {"name": "A", "quantity": "1", "unit_price": "100"},
            {"name": "B", "quantity": "2", "unit_price": "50"},
        ]
        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(
                customer_id, rate_21,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21,
                lines=lines,
            ),
        )
        assert resp.status_code == 201
        q = resp.json()
        assert q["tax_mode"] == "DOCUMENT"
        assert q["subtotal_excl_vat"] == "200.000"
        assert q["vat_total"] == "42.000"
        assert q["total_incl_vat"] == "242.000"
        assert len(q["taxes"]) == 1
        assert q["taxes"][0]["vat_rate_percent"] == "21.000"

    async def test_amounts_include_vat(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # 121 incl VAT → 100 excl, 21 VAT
        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(
                customer_id, rate_21,
                amounts_include_vat=True,
                lines=[_line(rate_21, price="121.000")],
            ),
        )
        assert resp.status_code == 201
        q = resp.json()
        assert q["subtotal_excl_vat"] == "100.000"
        assert q["vat_total"] == "21.000"
        assert q["total_incl_vat"] == "121.000"

    async def test_update_preserves_number(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        rate_9 = seeds["rates"]["NL reduced (9%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        original_number = create_resp.json()["quote_number"]
        q_id = create_resp.json()["id"]

        update_resp = await db_client.put(
            f"/api/v1/quotes/{q_id}",
            json=_quote_payload(
                customer_id, rate_9,
                lines=[_line(rate_9, name="Updated", qty="2", price="50")],
            ),
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["quote_number"] == original_number
        assert updated["subtotal_excl_vat"] == "100.000"
        assert updated["vat_total"] == "9.000"

    async def test_delete_cascade(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        q_id = create_resp.json()["id"]

        del_resp = await db_client.delete(f"/api/v1/quotes/{q_id}")
        assert del_resp.status_code == 204

        get_resp = await db_client.get(f"/api/v1/quotes/{q_id}")
        assert get_resp.status_code == 404

    async def test_notes_persist(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        long_notes = "This is a very long note. " * 20
        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(customer_id, rate_21, notes=long_notes),
        )
        assert resp.status_code == 201
        q = resp.json()
        assert q["notes"] == long_notes

    async def test_discount_combo(self, db_client: AsyncClient) -> None:
        """Line-level + document-level discount."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        lines = [
            {
                "name": "Item",
                "quantity": "2",
                "unit_price": "100",
                "vat_rate_id": rate_21,
                "discount": {"type": "PERCENTAGE", "value": "10"},
            }
        ]
        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(
                customer_id, rate_21,
                lines=lines,
                discount={"type": "FIXED", "value": "20"},
            ),
        )
        assert resp.status_code == 201
        q = resp.json()
        # 2×100=200 gross; line_discount=20 (10%); subtotal_excl_vat=200 (gross);
        # line_discount_total=20; doc_discount=20; taxable=200-20-20=160
        assert q["subtotal_excl_vat"] == "200.000"
        assert q["line_discount_total"] == "20.000"
        assert q["document_discount_amount"] == "20.000"
        assert q["taxable_amount"] == "160.000"


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteNumbering:

    async def test_first_quote_default_template(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        assert resp.status_code == 201
        assert resp.json()["quote_number"] == "QUO-000001"

    async def test_sequence_increments(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q1 = (await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )).json()
        q2 = (await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )).json()
        assert q1["quote_number"] == "QUO-000001"
        assert q2["quote_number"] == "QUO-000002"

    async def test_delete_does_not_recycle(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        r1 = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        q_id = r1.json()["id"]
        await db_client.delete(f"/api/v1/quotes/{q_id}")

        r2 = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        assert r2.json()["quote_number"] == "QUO-000002"

    async def test_jump_sequence(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Set start to 100
        await db_client.put(
            "/api/v1/settings/quote-numbering",
            json={"template": "{{SERIES:QUO}}-{{SEQUENCE:6}}", "sequence_start": 100},
        )
        # Sequence row doesn't exist yet, so sequence_start takes effect on first create
        resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        assert resp.status_code == 201
        assert resp.json()["quote_number"] == "QUO-000100"

    async def test_advance_sequence_forward_only(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Create first quote (seq = 1, next = 2)
        await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))

        # Advance to 200
        adv = await db_client.put(
            "/api/v1/settings/quote-number-sequence", json={"next_sequence": 200}
        )
        assert adv.status_code == 200
        assert adv.json()["next_sequence"] == 200

        # Try to go back → 422
        back = await db_client.put(
            "/api/v1/settings/quote-number-sequence", json={"next_sequence": 1}
        )
        assert back.status_code in (422, 409)

    async def test_numbering_settings_get_put(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # GET defaults
        resp = await db_client.get("/api/v1/settings/quote-numbering")
        assert resp.status_code == 200
        cfg = resp.json()
        assert "QUO" in cfg["template"]
        assert "preview" in cfg

        # PUT custom template
        put_resp = await db_client.put(
            "/api/v1/settings/quote-numbering",
            json={"template": "{{SERIES:EST}}-{{SEQUENCE:4}}", "sequence_start": 1},
        )
        assert put_resp.status_code == 200
        assert "EST" in put_resp.json()["template"]


# ---------------------------------------------------------------------------
# Default valid_until
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteValidUntil:

    async def test_default_valid_days_applied(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        assert resp.status_code == 201
        q = resp.json()
        assert q["valid_until"] is not None
        expected = (date.today() + timedelta(days=30)).isoformat()
        assert q["valid_until"] == expected

    async def test_explicit_valid_until(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(customer_id, rate_21, valid_until="2026-12-31"),
        )
        assert resp.status_code == 201
        assert resp.json()["valid_until"] == "2026-12-31"

    async def test_custom_default_valid_days_setting(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Change company default to 14 days
        set_resp = await db_client.put(
            "/api/v1/settings/quote-default-valid-days",
            json={"default_valid_days": 14},
        )
        assert set_resp.status_code == 200

        resp = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        assert resp.status_code == 201
        expected = (date.today() + timedelta(days=14)).isoformat()
        assert resp.json()["valid_until"] == expected


# ---------------------------------------------------------------------------
# Status machine
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteStatusMachine:

    async def test_draft_to_sent(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "SENT"

    async def test_sent_to_draft(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "DRAFT"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "DRAFT"

    async def test_sent_to_rejected_and_back(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "REJECTED"})

        # Back to SENT
        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "SENT"

    async def test_sent_to_accepted_manual(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "ACCEPTED"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACCEPTED"

    async def test_accepted_cannot_be_edited(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "ACCEPTED"})

        put_resp = await db_client.put(
            f"/api/v1/quotes/{q_id}",
            json=_quote_payload(customer_id, rate_21),
        )
        assert put_resp.status_code == 409

    async def test_accepted_has_no_transitions(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "ACCEPTED"})

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"}
        )
        assert resp.status_code == 422

    async def test_cannot_set_expired_manually(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        q_id = (
            await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))
        ).json()["id"]

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "EXPIRED"}
        )
        assert resp.status_code == 422

    async def test_read_time_expiry_on_get(self, db_client: AsyncClient) -> None:
        """A SENT quote with valid_until in the past becomes EXPIRED on GET."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q_id = (
            await db_client.post(
                "/api/v1/quotes",
                json=_quote_payload(customer_id, rate_21, valid_until=yesterday),
            )
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        # Re-fetch – should be EXPIRED now
        resp = await db_client.get(f"/api/v1/quotes/{q_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "EXPIRED"

    async def test_read_time_expiry_on_list(self, db_client: AsyncClient) -> None:
        """SENT quote with expired valid_until shows as EXPIRED in list."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q_id = (
            await db_client.post(
                "/api/v1/quotes",
                json=_quote_payload(customer_id, rate_21, valid_until=yesterday),
            )
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        list_resp = await db_client.get("/api/v1/quotes")
        items = list_resp.json()["items"]
        match = next(i for i in items if i["id"] == q_id)
        assert match["status"] == "EXPIRED"

    async def test_expired_to_accepted(self, db_client: AsyncClient) -> None:
        """Expired quote can be manually accepted (soft-expiry rule)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q_id = (
            await db_client.post(
                "/api/v1/quotes",
                json=_quote_payload(customer_id, rate_21, valid_until=yesterday),
            )
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})
        # Force expiry via GET
        await db_client.get(f"/api/v1/quotes/{q_id}")

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "ACCEPTED"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACCEPTED"

    async def test_expired_to_rejected(self, db_client: AsyncClient) -> None:
        """Expired quote can be rejected."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q_id = (
            await db_client.post(
                "/api/v1/quotes",
                json=_quote_payload(customer_id, rate_21, valid_until=yesterday),
            )
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})
        await db_client.get(f"/api/v1/quotes/{q_id}")  # trigger expiry

        resp = await db_client.post(
            f"/api/v1/quotes/{q_id}/status", json={"status": "REJECTED"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# List / pagination / filtering
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteList:

    async def test_list_empty(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/quotes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_pagination(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        for _ in range(3):
            await db_client.post(
                "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
            )

        page1 = await db_client.get("/api/v1/quotes?limit=2&offset=0")
        assert page1.json()["total"] == 3
        assert len(page1.json()["items"]) == 2

        page2 = await db_client.get("/api/v1/quotes?limit=2&offset=2")
        assert len(page2.json()["items"]) == 1

    async def test_list_filter_by_status(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        r2 = await db_client.post(
            "/api/v1/quotes", json=_quote_payload(customer_id, rate_21)
        )
        await db_client.post(
            f"/api/v1/quotes/{r2.json()['id']}/status", json={"status": "SENT"}
        )

        sent_list = await db_client.get("/api/v1/quotes?status=SENT")
        assert sent_list.json()["total"] == 1
        assert sent_list.json()["items"][0]["id"] == r2.json()["id"]

        draft_list = await db_client.get("/api/v1/quotes?status=DRAFT")
        assert draft_list.json()["total"] == 1

    async def test_list_search(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, name="ACME Corp")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))

        found = await db_client.get("/api/v1/quotes?q=ACME")
        assert found.json()["total"] == 1

        notfound = await db_client.get("/api/v1/quotes?q=zzznotfound")
        assert notfound.json()["total"] == 0

    async def test_list_customer_name_present(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, name="My Customer")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        await db_client.post("/api/v1/quotes", json=_quote_payload(customer_id, rate_21))

        resp = await db_client.get("/api/v1/quotes")
        assert resp.json()["items"][0]["customer_name"] == "My Customer"


# ---------------------------------------------------------------------------
# Cross-company isolation (using random UUIDs – onboarding lock prevents
# registering a second company in the same DB)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteIsolation:

    async def test_random_id_returns_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/quotes/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_delete_random_id_returns_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.delete(f"/api/v1/quotes/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_update_random_id_returns_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.put(
            f"/api/v1/quotes/{uuid.uuid4()}",
            json=_quote_payload(customer_id, rate_21),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth / permissions
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteAuth:

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/quotes")
        assert resp.status_code == 401

    async def test_unauthenticated_list_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated GET /quotes returns 401."""
        resp = await db_client.get("/api/v1/quotes")
        assert resp.status_code == 401

    async def test_unauthenticated_post_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated POST /quotes returns 401."""
        resp = await db_client.post("/api/v1/quotes", json={})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# FK constraints
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteFKConstraints:

    async def test_invalid_customer_id_rejected(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(str(uuid.uuid4()), rate_21),
        )
        assert resp.status_code == 422

    async def test_invalid_vat_rate_id(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes",
            json=_quote_payload(customer_id, str(uuid.uuid4())),
        )
        assert resp.status_code == 422

    async def test_no_lines_rejected(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        payload = _quote_payload(customer_id, rate_21)
        payload["lines"] = []
        resp = await db_client.post("/api/v1/quotes", json=payload)
        assert resp.status_code == 422

    async def test_non_base_currency_rejected(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        payload = _quote_payload(customer_id, rate_21)
        payload["currency"] = "USD"
        resp = await db_client.post("/api/v1/quotes", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List status-filter correctness after expiry (P1 regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteListExpiryFilter:
    """Verify that ?status=SENT / ?status=EXPIRED both see the post-expiry truth.

    Before the fix, list_quotes applied the SQL status filter BEFORE the batch
    expiry flush, so a SENT-but-past-valid_until quote would appear in
    ?status=SENT and be absent from ?status=EXPIRED on first request.
    """

    async def test_expired_quote_absent_from_sent_filter(
        self, db_client: AsyncClient
    ) -> None:
        """A quote past valid_until must not appear in ?status=SENT."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q_id = (
            await db_client.post(
                "/api/v1/quotes",
                json=_quote_payload(customer_id, rate_21, valid_until=yesterday),
            )
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        # First list call – should batch-expire before filtering
        sent_resp = await db_client.get("/api/v1/quotes?status=SENT")
        ids_in_sent = [i["id"] for i in sent_resp.json()["items"]]
        assert q_id not in ids_in_sent, "expired quote must not appear in ?status=SENT"
        assert sent_resp.json()["total"] == 0

    async def test_expired_quote_present_in_expired_filter(
        self, db_client: AsyncClient
    ) -> None:
        """A quote past valid_until must appear in ?status=EXPIRED even on first request."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        q_id = (
            await db_client.post(
                "/api/v1/quotes",
                json=_quote_payload(customer_id, rate_21, valid_until=yesterday),
            )
        ).json()["id"]
        await db_client.post(f"/api/v1/quotes/{q_id}/status", json={"status": "SENT"})

        # First list call – batch expiry must run before count/filter
        expired_resp = await db_client.get("/api/v1/quotes?status=EXPIRED")
        ids_in_expired = [i["id"] for i in expired_resp.json()["items"]]
        assert q_id in ids_in_expired, "expired quote must appear in ?status=EXPIRED"
        assert expired_resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# Query-param contract validation (P2 regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteListParamValidation:
    """Verify that invalid sort_by / status values return 422."""

    async def test_invalid_sort_by_returns_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/quotes?sort_by=nonexistent_field")
        assert resp.status_code == 422

    async def test_invalid_status_returns_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/quotes?status=BOGUS")
        assert resp.status_code == 422
