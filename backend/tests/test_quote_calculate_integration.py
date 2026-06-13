"""Integration tests for POST /api/v1/quotes/calculate (M6 step 1).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- Happy flow: LINE mode / DOCUMENT mode, excl VAT / incl VAT
- VAT treatment derivation (NL domestic, EU B2B reverse, EU B2C, export)
- Explicit treatment override
- Treatment not found / inactive → 422
- Cross-company customer/rate/treatment rejection → 422
- Non-base-currency rejection → 422
- Auth: owner-only, unauthenticated → 401
- Schema validation: missing fields → 422
- valid_until field accepted and ignored by pricing engine
"""

from __future__ import annotations

import uuid

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
    """Create company and return seed data (rates, treatments)."""
    resp = await client.put(
        "/api/v1/company",
        json={
            "name": "Test Quote Co",
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


async def _create_customer(
    client: AsyncClient,
    *,
    name: str = "Test Customer",
    vat_id: str | None = None,
    country_code: str = "NL",
) -> str:
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": name,
            "vat_id": vat_id,
            "addresses": [
                {
                    "type": "BILLING",
                    "country_code": country_code,
                    "city": "Amsterdam",
                },
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _calc_payload(
    customer_id: str,
    rate_id: str,
    *,
    tax_mode: str = "LINE",
    amounts_include_vat: bool = False,
    treatment_id: str | None = None,
    currency: str | None = None,
    document_vat_rate_id: str | None = None,
    discount: dict | None = None,
    lines: list[dict] | None = None,
    valid_until: str | None = None,
) -> dict:
    """Build a quote calculate request payload."""
    if lines is None:
        lines = [
            {
                "name": "Service",
                "quantity": "1",
                "unit_price": "100.000",
                "vat_rate_id": rate_id if tax_mode == "LINE" else None,
            },
        ]
    payload: dict = {
        "customer_id": customer_id,
        "quote_date": "2026-06-11",
        "tax_mode": tax_mode,
        "amounts_include_vat": amounts_include_vat,
        "lines": lines,
    }
    if valid_until:
        payload["valid_until"] = valid_until
    if treatment_id:
        payload["vat_treatment_id"] = treatment_id
    if currency:
        payload["currency"] = currency
    if document_vat_rate_id:
        payload["document_vat_rate_id"] = document_vat_rate_id
    if discount:
        payload["discount"] = discount
    return payload


# ---------------------------------------------------------------------------
# Happy flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteCalculateHappyFlow:

    async def test_line_mode_excl_vat(self, db_client: AsyncClient) -> None:
        """LINE mode, 1 line × €100, 21% VAT, excl input."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21_id = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21_id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tax_mode"] == "LINE"
        assert data["amounts_include_vat"] is False
        assert data["subtotal_excl_vat"] == "100.00"
        assert data["vat_total"] == "21.00"
        assert data["total_incl_vat"] == "121.00"
        assert data["line_discount_total"] == "0"
        assert data["document_discount_amount"] == "0"
        assert len(data["lines"]) == 1
        assert len(data["line_taxes"]) == 1
        assert len(data["document_taxes"]) == 0
        assert data["vat_treatment_snapshot"] is not None
        assert data["vat_treatment_snapshot"]["code"] == "NL_DOMESTIC"

    async def test_document_mode_excl_vat(self, db_client: AsyncClient) -> None:
        """DOCUMENT mode, 2 lines, 21% rate, excl input."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21_id = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21_id,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21_id,
                lines=[
                    {"name": "A", "quantity": "1", "unit_price": "100"},
                    {"name": "B", "quantity": "2", "unit_price": "50"},
                ],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tax_mode"] == "DOCUMENT"
        assert data["subtotal_excl_vat"] == "200.00"
        assert data["vat_total"] == "42.00"
        assert data["total_incl_vat"] == "242.00"
        assert len(data["document_taxes"]) == 1
        assert len(data["line_taxes"]) == 0

    async def test_incl_vat_reverse(self, db_client: AsyncClient) -> None:
        """LINE mode, incl VAT input: €121 → reverse to €100 + €21."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21_id = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21_id,
                amounts_include_vat=True,
                lines=[
                    {
                        "name": "Service",
                        "quantity": "1",
                        "unit_price": "121.000",
                        "vat_rate_id": rate_21_id,
                    }
                ],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["taxable_amount"] == "100.00"
        assert data["vat_total"] == "21.00"
        assert data["total_incl_vat"] == "121.00"

    async def test_multi_line_multi_rate(self, db_client: AsyncClient) -> None:
        """2 lines: 21% + 9%, verify separate tax entries."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        rate_9 = seeds["rates"]["NL reduced (9%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21,
                lines=[
                    {"name": "A", "quantity": "1", "unit_price": "100", "vat_rate_id": rate_21},
                    {"name": "B", "quantity": "1", "unit_price": "100", "vat_rate_id": rate_9},
                ],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vat_total"] == "30.00"  # 21 + 9
        assert len(data["line_taxes"]) == 2

    async def test_line_and_document_discounts(self, db_client: AsyncClient) -> None:
        """LINE mode with line + document discounts."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21,
                discount={"type": "PERCENTAGE", "value": "10"},
                lines=[
                    {
                        "name": "A",
                        "quantity": "1",
                        "unit_price": "100",
                        "vat_rate_id": rate_21,
                        "discount": {"type": "PERCENTAGE", "value": "10"},
                    },
                ],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        # Line disc: 10, after line: 90. Doc disc (10% of 90): 9. Taxable: 81.
        assert data["line_discount_total"] == "10.00"
        assert data["document_discount_amount"] == "9.00"
        assert data["taxable_amount"] == "81.00"
        assert data["vat_total"] == "17.01"  # 81 × 21% = 17.01

    async def test_valid_until_accepted_does_not_affect_pricing(
        self, db_client: AsyncClient
    ) -> None:
        """valid_until field is accepted and does not change pricing result."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21, valid_until="2026-07-11"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_incl_vat"] == "121.00"

    async def test_zero_rate_line(self, db_client: AsyncClient) -> None:
        """LINE mode with 0% VAT rate → total equals subtotal."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_0 = seeds["rates"]["Zero (0%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_0,
                lines=[{"name": "X", "quantity": "1", "unit_price": "100", "vat_rate_id": rate_0}],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vat_total"] == "0.00"
        assert data["total_incl_vat"] == "100.00"


# ---------------------------------------------------------------------------
# VAT treatment derivation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteVatTreatmentDerivation:

    async def test_nl_domestic_default(self, db_client: AsyncClient) -> None:
        """NL company + NL customer → NL_DOMESTIC."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, country_code="NL")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21),
        )
        assert resp.status_code == 200
        assert resp.json()["vat_treatment_snapshot"]["code"] == "NL_DOMESTIC"
        assert resp.json()["vat_total"] == "21.00"

    async def test_eu_b2b_reverse(self, db_client: AsyncClient) -> None:
        """NL company + DE customer with VAT number → EU_B2B_REVERSE, VAT = 0."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, country_code="DE", vat_id="DE123456789")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vat_treatment_snapshot"]["code"] == "EU_B2B_REVERSE"
        assert data["vat_treatment_snapshot"]["requires_icp"] is True
        assert data["vat_total"] == "0.00"

    async def test_eu_b2c(self, db_client: AsyncClient) -> None:
        """NL company + FR customer without VAT number → EU_B2C."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, country_code="FR")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21),
        )
        assert resp.status_code == 200
        assert resp.json()["vat_treatment_snapshot"]["code"] == "EU_B2C"
        assert resp.json()["vat_total"] == "21.00"

    async def test_export_non_eu(self, db_client: AsyncClient) -> None:
        """NL company + US customer → EXPORT_NON_EU, VAT = 0."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, country_code="US")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vat_treatment_snapshot"]["code"] == "EXPORT_NON_EU"
        assert data["vat_total"] == "0.00"

    async def test_explicit_treatment_override(self, db_client: AsyncClient) -> None:
        """Explicitly provide treatment_id overrides derivation."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client, country_code="NL")
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        export_treatment = seeds["treatments"]["EXPORT_NON_EU"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21, treatment_id=export_treatment),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vat_treatment_snapshot"]["code"] == "EXPORT_NON_EU"
        assert data["vat_total"] == "0.00"

    async def test_customer_no_billing_country_derives_nl_domestic(
        self, db_client: AsyncClient
    ) -> None:
        """Customer without billing address country → NL_DOMESTIC."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "No Country"},
        )
        customer_id = resp.json()["id"]
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21),
        )
        assert resp.status_code == 200
        assert resp.json()["vat_treatment_snapshot"]["code"] == "NL_DOMESTIC"


# ---------------------------------------------------------------------------
# Validation / error cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteCalculateValidation:

    async def test_non_base_currency_422(self, db_client: AsyncClient) -> None:
        """M6: submitting USD when base is EUR → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21, currency="USD"),
        )
        assert resp.status_code == 422
        assert "base currency" in resp.json()["detail"].lower()

    async def test_cross_company_customer_422(self, db_client: AsyncClient) -> None:
        """Customer from another company → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        fake_customer_id = uuid.uuid4()

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(str(fake_customer_id), rate_21),
        )
        assert resp.status_code == 422

    async def test_unknown_vat_rate_422(self, db_client: AsyncClient) -> None:
        """Unknown VAT rate ID → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, str(uuid.uuid4())),
        )
        assert resp.status_code == 422

    async def test_inactive_treatment_422(self, db_client: AsyncClient) -> None:
        """Explicitly selecting an inactive treatment → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        create_resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "INACTIVE_QUOTE_TEST",
                "label": "Inactive Quote Test",
                "side": "SALES",
                "effect": "APPLY_RATE",
                "active": True,
            },
        )
        treat_id = create_resp.json()["id"]
        await db_client.put(
            f"/api/v1/vat-treatments/{treat_id}",
            json={
                "code": "INACTIVE_QUOTE_TEST",
                "label": "Inactive Quote Test",
                "side": "SALES",
                "effect": "APPLY_RATE",
                "active": False,
            },
        )

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21, treatment_id=treat_id),
        )
        assert resp.status_code == 422

    async def test_line_mode_missing_vat_rate_id_422(self, db_client: AsyncClient) -> None:
        """LINE mode without vat_rate_id on line → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": customer_id,
                "quote_date": "2026-06-11",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "No Rate", "quantity": "1", "unit_price": "100"}],
            },
        )
        assert resp.status_code == 422

    async def test_document_mode_missing_rate_422(self, db_client: AsyncClient) -> None:
        """DOCUMENT mode without document_vat_rate_id → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": customer_id,
                "quote_date": "2026-06-11",
                "tax_mode": "DOCUMENT",
                "amounts_include_vat": False,
                "lines": [{"name": "A", "quantity": "1", "unit_price": "100"}],
            },
        )
        assert resp.status_code == 422

    async def test_empty_lines_422(self, db_client: AsyncClient) -> None:
        """0 lines → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": customer_id,
                "quote_date": "2026-06-11",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [],
            },
        )
        assert resp.status_code == 422

    async def test_zero_quantity_422(self, db_client: AsyncClient) -> None:
        """quantity = 0 → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21,
                lines=[
                    {
                        "name": "Zero Qty",
                        "quantity": "0",
                        "unit_price": "100",
                        "vat_rate_id": rate_21,
                    }
                ],
            ),
        )
        assert resp.status_code == 422

    async def test_negative_unit_price_422(self, db_client: AsyncClient) -> None:
        """unit_price < 0 → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21,
                lines=[
                    {
                        "name": "Neg Price",
                        "quantity": "1",
                        "unit_price": "-5",
                        "vat_rate_id": rate_21,
                    }
                ],
            ),
        )
        assert resp.status_code == 422

    async def test_explicit_treatment_with_cross_company_customer_422(
        self, db_client: AsyncClient
    ) -> None:
        """Explicit treatment + random customer → 422 (customer not validated)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]
        export_treatment = seeds["treatments"]["EXPORT_NON_EU"]["id"]
        fake_customer_id = str(uuid.uuid4())

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                fake_customer_id,
                rate_21,
                treatment_id=export_treatment,
            ),
        )
        assert resp.status_code == 422
        assert "customer" in resp.json()["detail"].lower()

    async def test_purchase_treatment_rejected_422(self, db_client: AsyncClient) -> None:
        """Explicitly passing a PURCHASE treatment → 422 (only SALES allowed)."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        purchase_resp = await db_client.get("/api/v1/vat-treatments?side=PURCHASE")
        purchase_treatments = purchase_resp.json()["items"]
        assert len(purchase_treatments) > 0
        purchase_treatment_id = purchase_treatments[0]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(customer_id, rate_21, treatment_id=purchase_treatment_id),
        )
        assert resp.status_code == 422
        assert "sales treatment" in resp.json()["detail"].lower()

    async def test_document_mode_100pct_discount_returns_tax_entry(
        self, db_client: AsyncClient
    ) -> None:
        """DOCUMENT mode: 100% discount still returns document_taxes entry."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json=_calc_payload(
                customer_id,
                rate_21,
                tax_mode="DOCUMENT",
                document_vat_rate_id=rate_21,
                discount={"type": "PERCENTAGE", "value": "100"},
                lines=[{"name": "A", "quantity": "1", "unit_price": "100"}],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["document_taxes"]) == 1
        assert data["document_taxes"][0]["taxable_amount"] == "0.00"
        assert data["document_taxes"][0]["tax_amount"] == "0.00"
        assert data["document_taxes"][0]["vat_rate_percent"] == "21.000"

    async def test_missing_quote_date_422(self, db_client: AsyncClient) -> None:
        """Missing quote_date → 422 schema validation error."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": customer_id,
                # quote_date omitted
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "X", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert resp.status_code == 422

    async def test_invoice_date_field_rejected_422(self, db_client: AsyncClient) -> None:
        """Sending invoice_date instead of quote_date → 422 (wrong field)."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        customer_id = await _create_customer(db_client)

        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": customer_id,
                "invoice_date": "2026-06-11",  # wrong field name
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "X", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteCalculateAuth:

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": str(uuid.uuid4()),
                "quote_date": "2026-06-11",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "X", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        """Authenticated but no company → 400."""
        await _full_auth(db_client)
        resp = await db_client.post(
            "/api/v1/quotes/calculate",
            json={
                "customer_id": str(uuid.uuid4()),
                "quote_date": "2026-06-11",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": "X", "quantity": "1", "unit_price": "10"}],
            },
        )
        assert resp.status_code == 400
