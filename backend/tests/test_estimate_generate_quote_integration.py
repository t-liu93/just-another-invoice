"""Integration tests for M6.5 Step 3 – estimate → quote generation.

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- 201 QuoteRead on successful generation
- Generated quote uses LINE tax mode
- Quote line name = group.public_description (zero-leak: not estimate line name)
- Quote line unit_price = group_sell_excl_vat
- Quote line quantity = 1
- Quote line vat_rate_id = group vat_rate_id
- Zero-leak: no cost/margin/internal fields in generated quote or its lines
- 422 when estimate has no customer
- 422 when a group with lines has no vat_rate_id
- 422 when no group has any lines (ungrouped-only estimate)
- Repeated generation: new quote each time, generated_quote_id updated, old quote intact
- M5 pricing totals correct: excl * 1.21 for 21% VAT
- EU_B2B customer: zero effective VAT (reverse-charge treatment from M5)
- Deleting estimate does not affect the already-generated quote (SET NULL FK)
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared auth / setup helpers
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
    """Create company + seed data; return dict with rates, nl_customer."""
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = {r["label"]: r for r in rates_resp.json()["items"]}

    # NL domestic customer
    cust_resp = await client.post(
        "/api/v1/customers",
        json={
            "name": "ACME NL",
            "addresses": [{"type": "BILLING", "country_code": "NL", "city": "Amsterdam"}],
        },
    )
    assert cust_resp.status_code == 201
    nl_customer = cust_resp.json()

    return {"rates": rates, "nl_customer": nl_customer}


async def _create_eu_b2b_customer(client: AsyncClient) -> dict:
    """Create a DE EU B2B customer (billing DE + vat_id set)."""
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": "EU Corp GmbH",
            "vat_id": "DE123456789",
            "addresses": [{"type": "BILLING", "country_code": "DE", "city": "Berlin"}],
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _estimate_payload(
    seed: dict,
    *,
    name: str = "Test Estimate",
    customer_id: str | None = None,
    groups: list[dict] | None = None,
    lines: list[dict] | None = None,
) -> dict:
    """Build an EstimateWrite payload; defaults to one grouped line."""
    rate_id = seed["rates"]["NL standard (21%)"]["id"]
    cid = customer_id if customer_id is not None else seed["nl_customer"]["id"]

    if groups is None:
        groups = [
            {
                "ref": "G1",
                "public_description": "Huawei 11kW charger installation",
                "vat_rate_id": rate_id,
                "sort_order": 0,
            }
        ]
    if lines is None:
        lines = [
            {
                "name": "Charger (INTERNAL)",
                "unit_cost_excl_vat": "200.000",
                "quantity": "1",
                "margin_rate": "0.3",
                "group_ref": "G1",
            }
        ]

    return {
        "name": name,
        "customer_id": cid,
        "groups": groups,
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Happy flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGenerateQuoteHappyFlow:

    async def test_returns_201_quote_read(self, db_client: AsyncClient) -> None:
        """POST generate-quote returns 201 with a QuoteRead body."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        q = resp.json()
        assert "id" in q
        assert "quote_number" in q

    async def test_generated_quote_uses_line_mode(self, db_client: AsyncClient) -> None:
        """Generated quote must use LINE tax mode."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        assert resp.json()["tax_mode"] == "LINE"

    async def test_quote_line_name_equals_public_description(
        self, db_client: AsyncClient
    ) -> None:
        """Quote line name must be group.public_description, not the estimate line name."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        lines = resp.json()["lines"]
        assert len(lines) == 1
        assert lines[0]["name"] == "Huawei 11kW charger installation"

    async def test_quote_line_unit_price_equals_group_sell_excl_vat(
        self, db_client: AsyncClient
    ) -> None:
        """Quote line unit_price must equal group_sell_excl_vat."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_data = create.json()
        est_id = est_data["id"]
        group_sell = est_data["groups"][0]["group_sell_excl_vat"]  # 260.000

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        assert resp.json()["lines"][0]["unit_price"] == group_sell

    async def test_quote_line_quantity_is_1(self, db_client: AsyncClient) -> None:
        """Quote line quantity must be 1 (lump-sum, hides internal breakdown)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        assert resp.json()["lines"][0]["quantity"] == "1.000"

    async def test_quote_line_vat_rate_id_matches_group(
        self, db_client: AsyncClient
    ) -> None:
        """Quote line vat_rate_id must match the group's vat_rate_id."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        assert resp.json()["lines"][0]["vat_rate_id"] == rate_id

    async def test_estimate_generated_quote_id_updated(
        self, db_client: AsyncClient
    ) -> None:
        """After generation, estimate.generated_quote_id must point to the new quote."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]
        assert create.json()["generated_quote_id"] is None

        gen = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert gen.status_code == 201
        new_quote_id = gen.json()["id"]

        est = await db_client.get(f"/api/v1/estimates/{est_id}")
        assert est.status_code == 200
        assert est.json()["generated_quote_id"] == new_quote_id


# ---------------------------------------------------------------------------
# Zero-leak guardrail
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGenerateQuoteZeroLeak:

    async def test_quote_and_lines_have_no_internal_cost_fields(
        self, db_client: AsyncClient
    ) -> None:
        """Generated quote response must not contain any cost/margin/internal fields."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        body = resp.json()

        # Document-level: no estimate internal fields
        for forbidden in (
            "unit_cost_excl_vat",
            "margin_rate",
            "line_total",
            "margin_amount",
        ):
            assert forbidden not in body, f"Field '{forbidden}' must not appear in quote body"

        # Line-level: no estimate internal fields
        assert len(body["lines"]) >= 1
        for line in body["lines"]:
            for forbidden in (
                "unit_cost_excl_vat",
                "margin_rate",
                "line_total",
                "margin_amount",
            ):
                assert forbidden not in line, (
                    f"Field '{forbidden}' must not appear in quote line"
                )
            # product_id must be null (we never pass internal product references)
            assert line["product_id"] is None
            # description must be null (no internal line description leaked)
            assert line["description"] is None
            # name must be the public description, not an internal estimate line name
            assert line["name"] == "Huawei 11kW charger installation"

    async def test_quote_line_has_no_unit(self, db_client: AsyncClient) -> None:
        """Quote line unit_id and unit_name must be null (lump-sum, no breakdown)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        line = resp.json()["lines"][0]
        assert line["unit_id"] is None
        assert line["unit_name"] is None


# ---------------------------------------------------------------------------
# 422 precondition cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGenerateQuote422:

    async def test_no_customer_returns_422(self, db_client: AsyncClient) -> None:
        """Estimate with no customer → 422."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]

        payload = {
            "name": "No Customer Estimate",
            "customer_id": None,
            "groups": [
                {
                    "ref": "G1",
                    "public_description": "Service",
                    "vat_rate_id": rate_id,
                    "sort_order": 0,
                }
            ],
            "lines": [
                {
                    "name": "Labor",
                    "unit_cost_excl_vat": "100.000",
                    "quantity": "1",
                    "margin_rate": "0",
                    "group_ref": "G1",
                }
            ],
        }
        create = await db_client.post("/api/v1/estimates", json=payload)
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 422

    async def test_group_missing_vat_rate_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """Group with lines but null vat_rate_id → 422."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        payload = _estimate_payload(
            seed,
            groups=[
                {
                    "ref": "G1",
                    "public_description": "Service",
                    "vat_rate_id": None,  # no VAT rate
                    "sort_order": 0,
                }
            ],
            lines=[
                {
                    "name": "Labor",
                    "unit_cost_excl_vat": "100.000",
                    "quantity": "1",
                    "margin_rate": "0",
                    "group_ref": "G1",
                }
            ],
        )
        create = await db_client.post("/api/v1/estimates", json=payload)
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 422

    async def test_no_grouped_lines_returns_422(self, db_client: AsyncClient) -> None:
        """Estimate with only ungrouped lines → 422 (no grouped lines to project)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        payload = _estimate_payload(
            seed,
            groups=[],  # no groups
            lines=[
                {
                    "name": "Ungrouped line",
                    "unit_cost_excl_vat": "100.000",
                    "quantity": "1",
                    "margin_rate": "0",
                    # no group_ref
                }
            ],
        )
        create = await db_client.post("/api/v1/estimates", json=payload)
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 422

    async def test_wrong_company_estimate_returns_404(
        self, db_client: AsyncClient
    ) -> None:
        """Cross-company isolation: non-existent estimate → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        import uuid as _uuid

        fake_id = str(_uuid.uuid4())
        resp = await db_client.post(f"/api/v1/estimates/{fake_id}/generate-quote")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Repeated generation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGenerateQuoteRepeated:

    async def test_repeated_generation_creates_new_quote(
        self, db_client: AsyncClient
    ) -> None:
        """Each generate call creates a new quote; generated_quote_id updated; old intact."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        # First generation
        gen1 = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert gen1.status_code == 201
        quote1_id = gen1.json()["id"]

        # Second generation
        gen2 = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert gen2.status_code == 201
        quote2_id = gen2.json()["id"]

        # Two distinct quotes
        assert quote1_id != quote2_id

        # Estimate points to the latest quote
        est = await db_client.get(f"/api/v1/estimates/{est_id}")
        assert est.status_code == 200
        assert est.json()["generated_quote_id"] == quote2_id

        # Old quote still exists
        old_quote = await db_client.get(f"/api/v1/quotes/{quote1_id}")
        assert old_quote.status_code == 200


# ---------------------------------------------------------------------------
# M5 pricing correctness
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGenerateQuotePricing:

    async def test_21pct_vat_totals(self, db_client: AsyncClient) -> None:
        """Generated quote totals: excl=260, vat=54.6, incl=314.6 for 21% VAT."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        # cost=200, margin=0.3 → group_sell_excl_vat = 200*(1+0.3) = 260
        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        q = resp.json()

        assert q["subtotal_excl_vat"] == "260.000"
        assert q["vat_total"] == "54.600"
        assert q["total_incl_vat"] == "314.600"

    async def test_eu_b2b_customer_gets_zero_vat(self, db_client: AsyncClient) -> None:
        """EU B2B customer (reverse-charge): effective VAT = 0 (M5 treatment derivation)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        eu_customer = await _create_eu_b2b_customer(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]

        payload = _estimate_payload(
            seed,
            customer_id=eu_customer["id"],
            groups=[
                {
                    "ref": "G1",
                    "public_description": "Equipment",
                    "vat_rate_id": rate_id,
                    "sort_order": 0,
                }
            ],
            lines=[
                {
                    "name": "Charger (INTERNAL)",
                    "unit_cost_excl_vat": "200.000",
                    "quantity": "1",
                    "margin_rate": "0.3",
                    "group_ref": "G1",
                }
            ],
        )
        create = await db_client.post("/api/v1/estimates", json=payload)
        assert create.status_code == 201
        est_id = create.json()["id"]

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        q = resp.json()

        # EU_B2B_REVERSE treatment → effective VAT = 0 (reverse charge)
        assert q["vat_total"] == "0.000"
        assert q["total_incl_vat"] == q["subtotal_excl_vat"]  # no VAT added
        assert q["vat_treatment_snapshot"]["code"] == "EU_B2B_REVERSE"

    async def test_multiple_groups_produce_multiple_lines(
        self, db_client: AsyncClient
    ) -> None:
        """Two groups → two quote lines, each with correct unit_price."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]

        payload = _estimate_payload(
            seed,
            groups=[
                {
                    "ref": "G1",
                    "public_description": "Equipment",
                    "vat_rate_id": rate_id,
                    "sort_order": 0,
                },
                {
                    "ref": "G2",
                    "public_description": "Labor & Shipping",
                    "vat_rate_id": rate_id,
                    "sort_order": 1,
                },
            ],
            lines=[
                {
                    "name": "Charger (INTERNAL)",
                    "unit_cost_excl_vat": "200.000",
                    "quantity": "1",
                    "margin_rate": "0.3",
                    "group_ref": "G1",
                },
                {
                    "name": "Installation labor (INTERNAL)",
                    "unit_cost_excl_vat": "50.000",
                    "quantity": "4",
                    "margin_rate": "0",
                    "group_ref": "G2",
                },
                {
                    "name": "Shipping (INTERNAL)",
                    "unit_cost_excl_vat": "30.000",
                    "quantity": "1",
                    "margin_rate": "0",
                    "group_ref": "G2",
                },
            ],
        )
        create = await db_client.post("/api/v1/estimates", json=payload)
        assert create.status_code == 201
        est_data = create.json()
        est_id = est_data["id"]

        # Verify group sell prices from the estimate
        groups_by_desc = {g["public_description"]: g for g in est_data["groups"]}
        equipment_sell = groups_by_desc["Equipment"]["group_sell_excl_vat"]  # 260
        labor_sell = groups_by_desc["Labor & Shipping"]["group_sell_excl_vat"]  # 230

        resp = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert resp.status_code == 201
        q = resp.json()

        lines = q["lines"]
        assert len(lines) == 2

        # Verify sort order: sort_order=0 (Equipment) comes first
        assert lines[0]["name"] == "Equipment"
        assert lines[0]["unit_price"] == equipment_sell

        assert lines[1]["name"] == "Labor & Shipping"
        assert lines[1]["unit_price"] == labor_sell


# ---------------------------------------------------------------------------
# Cascade / FK isolation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGenerateQuoteCascade:

    async def test_delete_estimate_does_not_delete_generated_quote(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting an estimate must not cascade to the generated quote (SET NULL FK)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create = await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))
        assert create.status_code == 201
        est_id = create.json()["id"]

        gen = await db_client.post(f"/api/v1/estimates/{est_id}/generate-quote")
        assert gen.status_code == 201
        quote_id = gen.json()["id"]

        # Delete the estimate
        del_resp = await db_client.delete(f"/api/v1/estimates/{est_id}")
        assert del_resp.status_code == 204

        # Estimate is gone
        est_resp = await db_client.get(f"/api/v1/estimates/{est_id}")
        assert est_resp.status_code == 404

        # Generated quote still exists and is unaffected
        quote_resp = await db_client.get(f"/api/v1/quotes/{quote_id}")
        assert quote_resp.status_code == 200
        assert quote_resp.json()["id"] == quote_id
        assert quote_resp.json()["status"] == "DRAFT"
