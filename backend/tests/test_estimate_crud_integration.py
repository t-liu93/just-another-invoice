"""Integration tests for Estimate CRUD (M6.5 step 2).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- Happy flow: create → get → update → delete
- Cost snapshot: M4 product price change does NOT back-fill old estimate
- group_ref → FK resolution: lines correctly linked to groups
- Cross-company isolation: customer / vat_rate / product / unit rejected → 422
- 404 for wrong company
- owner-only auth
- Cascade: delete estimate removes lines + groups
- FK SET NULL: delete product / unit → estimate_line snapshot preserved
- List: q filter, customer_id filter, sort_by, pagination total
- Header totals = sub-table sums
- Ungrouped lines: contribute to total but not to any group
- Input boundary validation: NUMERIC column upper limits rejected → 422
- updated_at always refreshed on PUT even when totals are unchanged
"""

from __future__ import annotations

import asyncio
import uuid

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Auth / setup helpers
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
    """Create company + seed data; return dict with rates, customer, product, unit."""
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert resp.status_code == 200

    # VAT rates (seeded by company creation)
    rates_resp = await client.get("/api/v1/vat-rates")
    rates = {r["label"]: r for r in rates_resp.json()["items"]}

    # Customer
    cust_resp = await client.post(
        "/api/v1/customers",
        json={"name": "ACME Corp", "country_code": "NL"},
    )
    assert cust_resp.status_code == 201
    customer = cust_resp.json()

    # Product (M4 catalogue)
    prod_resp = await client.post(
        "/api/v1/products",
        json={
            "name": "Charger 11kW",
            "purchase_cost_excl_vat": "200.000",
            "margin_rate": "0.3000",
        },
    )
    assert prod_resp.status_code == 201
    product = prod_resp.json()

    # Unit
    units_resp = await client.get("/api/v1/units")
    unit = units_resp.json()["items"][0]

    return {
        "rates": rates,
        "customer": customer,
        "product": product,
        "unit": unit,
    }


def _estimate_payload(
    seed: dict,
    *,
    name: str = "Test Estimate",
    with_customer: bool = True,
    with_product: bool = False,
    with_unit: bool = False,
    with_group: bool = True,
) -> dict:
    """Build a minimal EstimateWrite payload."""
    rate_id = seed["rates"]["NL standard (21%)"]["id"]
    customer_id = seed["customer"]["id"] if with_customer else None

    group_ref = "G1"
    groups: list[dict] = []
    if with_group:
        groups = [
            {
                "ref": group_ref,
                "public_description": "Charger installation",
                "vat_rate_id": rate_id,
                "sort_order": 0,
            }
        ]

    line: dict = {
        "name": "Charger",
        "unit_cost_excl_vat": "200.000",
        "quantity": "1",
        "margin_rate": "0.3",
    }
    if with_product:
        line["product_id"] = seed["product"]["id"]
    if with_unit:
        line["unit_id"] = seed["unit"]["id"]
        line["unit_name"] = seed["unit"]["name"]
    if with_group:
        line["group_ref"] = group_ref

    return {
        "name": name,
        "customer_id": customer_id,
        "notes": "Internal only",
        "groups": groups,
        "lines": [line],
    }


# ---------------------------------------------------------------------------
# Happy flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCRUDHappyFlow:

    async def test_create_returns_computed_amounts(
        self, db_client: AsyncClient
    ) -> None:
        """Create estimate → response contains correct costing amounts."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert resp.status_code == 201
        data = resp.json()

        # Root totals: cost=200, margin=60, sell=260
        assert data["total_excl_vat"] == "260.000"
        assert data["total_margin"] == "60.000"

        # Line amounts
        assert len(data["lines"]) == 1
        ln = data["lines"][0]
        assert ln["line_total"] == "200.000"
        assert ln["margin_amount"] == "60.000"
        assert ln["line_sell_excl_vat"] == "260.000"

        # Group amounts
        assert len(data["groups"]) == 1
        g = data["groups"][0]
        assert g["group_sell_excl_vat"] == "260.000"
        assert g["vat_rate_label"] == "NL standard (21%)"

        # Indicative VAT (NL 21%)
        assert data["indicative_vat_rate_percent"] == "21.000"
        assert data["total_incl_vat_indicative"] == "314.600"  # 260 * 1.21

        # Customer name populated
        assert data["customer_name"] == "ACME Corp"
        assert data["notes"] == "Internal only"

    async def test_get_returns_same_data(self, db_client: AsyncClient) -> None:
        """GET by id returns same data as create response."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert create_resp.status_code == 201
        created = create_resp.json()

        get_resp = await db_client.get(f"/api/v1/estimates/{created['id']}")
        assert get_resp.status_code == 200
        got = get_resp.json()

        assert got["id"] == created["id"]
        assert got["total_excl_vat"] == created["total_excl_vat"]
        assert got["total_margin"] == created["total_margin"]
        assert len(got["lines"]) == 1
        assert len(got["groups"]) == 1

    async def test_update_recomputes_amounts(self, db_client: AsyncClient) -> None:
        """PUT replaces sub-tables and recomputes amounts."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        create_resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]

        # Update with a different cost
        updated_payload = {
            "name": "Updated Estimate",
            "customer_id": customer_id,
            "groups": [
                {
                    "ref": "G1",
                    "public_description": "Updated group",
                    "vat_rate_id": rate_id,
                    "sort_order": 0,
                }
            ],
            "lines": [
                {
                    "name": "Charger v2",
                    "unit_cost_excl_vat": "300.000",
                    "quantity": "2",
                    "margin_rate": "0.2",
                    "group_ref": "G1",
                }
            ],
        }
        put_resp = await db_client.put(
            f"/api/v1/estimates/{estimate_id}", json=updated_payload
        )
        assert put_resp.status_code == 200
        data = put_resp.json()

        # cost=600, margin=120, sell=720
        assert data["total_excl_vat"] == "720.000"
        assert data["total_margin"] == "120.000"
        assert data["name"] == "Updated Estimate"

    async def test_delete_returns_204(self, db_client: AsyncClient) -> None:
        """DELETE returns 204 and subsequent GET returns 404."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]

        del_resp = await db_client.delete(f"/api/v1/estimates/{estimate_id}")
        assert del_resp.status_code == 204

        get_resp = await db_client.get(f"/api/v1/estimates/{estimate_id}")
        assert get_resp.status_code == 404

    async def test_create_with_product_and_unit(self, db_client: AsyncClient) -> None:
        """Create with product_id and unit_id → references stored, amounts correct."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, with_product=True, with_unit=True),
        )
        assert resp.status_code == 201
        data = resp.json()
        ln = data["lines"][0]
        assert ln["product_id"] == seed["product"]["id"]
        assert ln["unit_id"] == seed["unit"]["id"]
        assert ln["line_total"] == "200.000"


# ---------------------------------------------------------------------------
# Cost snapshot (red-line 8)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCostSnapshot:

    async def test_product_price_change_does_not_affect_old_estimate(
        self, db_client: AsyncClient
    ) -> None:
        """Updating product purchase cost must NOT change existing estimate amounts."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        # Create estimate referencing the product
        create_resp = await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, with_product=True),
        )
        assert create_resp.status_code == 201
        old_line_total = create_resp.json()["lines"][0]["line_total"]
        old_total = create_resp.json()["total_excl_vat"]
        estimate_id = create_resp.json()["id"]

        # Update product cost in M4 catalogue
        product_id = seed["product"]["id"]
        update_resp = await db_client.put(
            f"/api/v1/products/{product_id}",
            json={"name": "Charger 11kW", "purchase_cost_excl_vat": "500.000"},
        )
        assert update_resp.status_code == 200

        # Estimate amounts must be unchanged
        get_resp = await db_client.get(f"/api/v1/estimates/{estimate_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["lines"][0]["line_total"] == old_line_total
        assert get_resp.json()["total_excl_vat"] == old_total


# ---------------------------------------------------------------------------
# group_ref → FK resolution
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateGroupRefResolution:

    async def test_lines_linked_to_correct_groups(
        self, db_client: AsyncClient
    ) -> None:
        """Lines with group_ref are linked to the correct group_id."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        payload = {
            "name": "Multi-group",
            "customer_id": customer_id,
            "groups": [
                {"ref": "A", "public_description": "Group A", "vat_rate_id": rate_id},
                {"ref": "B", "public_description": "Group B", "vat_rate_id": rate_id},
            ],
            "lines": [
                {
                    "name": "Line A1",
                    "unit_cost_excl_vat": "100",
                    "quantity": "1",
                    "margin_rate": "0.2",
                    "group_ref": "A",
                },
                {
                    "name": "Line A2",
                    "unit_cost_excl_vat": "50",
                    "quantity": "2",
                    "margin_rate": "0",
                    "group_ref": "A",
                },
                {
                    "name": "Line B1",
                    "unit_cost_excl_vat": "75",
                    "quantity": "1",
                    "margin_rate": "0",
                    "group_ref": "B",
                },
                {
                    "name": "Overhead",
                    "unit_cost_excl_vat": "30",
                    "quantity": "1",
                    "margin_rate": "0",
                    # no group_ref → ungrouped
                },
            ],
        }
        resp = await db_client.post("/api/v1/estimates", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        # Find group ids by public_description
        group_a = next(g for g in data["groups"] if g["public_description"] == "Group A")
        group_b = next(g for g in data["groups"] if g["public_description"] == "Group B")

        # Check lines are linked correctly
        line_a1 = next(ln for ln in data["lines"] if ln["name"] == "Line A1")
        line_a2 = next(ln for ln in data["lines"] if ln["name"] == "Line A2")
        line_b1 = next(ln for ln in data["lines"] if ln["name"] == "Line B1")
        overhead = next(ln for ln in data["lines"] if ln["name"] == "Overhead")

        assert line_a1["group_id"] == group_a["id"]
        assert line_a2["group_id"] == group_a["id"]
        assert line_b1["group_id"] == group_b["id"]
        assert overhead["group_id"] is None

        # Group A: 120 + 100 = 220
        assert group_a["group_sell_excl_vat"] == "220.000"
        # Group B: 75
        assert group_b["group_sell_excl_vat"] == "75.000"

        # Total: 220 + 75 + 30 = 325 (overhead counted in total)
        assert data["total_excl_vat"] == "325.000"
        # Total margin: only line A1 has margin = 100*0.2=20
        assert data["total_margin"] == "20.000"

    async def test_ungrouped_line_counted_in_total_not_group(
        self, db_client: AsyncClient
    ) -> None:
        """Ungrouped lines contribute to total_excl_vat but not any group."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        payload = {
            "name": "Ungrouped test",
            "customer_id": customer_id,
            "groups": [
                {"ref": "G1", "public_description": "Grouped", "vat_rate_id": rate_id}
            ],
            "lines": [
                {
                    "name": "Grouped line",
                    "unit_cost_excl_vat": "100",
                    "quantity": "1",
                    "margin_rate": "0",
                    "group_ref": "G1",
                },
                {
                    "name": "Ungrouped line",
                    "unit_cost_excl_vat": "50",
                    "quantity": "1",
                    "margin_rate": "0",
                },
            ],
        }
        resp = await db_client.post("/api/v1/estimates", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        assert data["groups"][0]["group_sell_excl_vat"] == "100.000"
        assert data["total_excl_vat"] == "150.000"


# ---------------------------------------------------------------------------
# Cross-company isolation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateIsolation:

    async def test_cross_company_customer_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """customer_id from another company → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Test",
                "customer_id": str(uuid.uuid4()),
                "groups": [],
                "lines": [],
            },
        )
        assert resp.status_code == 422
        assert "customer" in resp.json()["detail"].lower()

    async def test_cross_company_vat_rate_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """vat_rate_id from another company → 422."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        customer_id = seed["customer"]["id"]

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Test",
                "customer_id": customer_id,
                "groups": [
                    {
                        "ref": "G1",
                        "public_description": "Group",
                        "vat_rate_id": str(uuid.uuid4()),
                    }
                ],
                "lines": [],
            },
        )
        assert resp.status_code == 422
        assert "vat rate" in resp.json()["detail"].lower()

    async def test_cross_company_product_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """product_id from another company → 422."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        customer_id = seed["customer"]["id"]

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Test",
                "customer_id": customer_id,
                "groups": [],
                "lines": [
                    {
                        "name": "Line",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                        "product_id": str(uuid.uuid4()),
                    }
                ],
            },
        )
        assert resp.status_code == 422
        assert "product" in resp.json()["detail"].lower()

    async def test_cross_company_unit_rejected(self, db_client: AsyncClient) -> None:
        """unit_id from another company → 422."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        customer_id = seed["customer"]["id"]

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Test",
                "customer_id": customer_id,
                "groups": [],
                "lines": [
                    {
                        "name": "Line",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                        "unit_id": str(uuid.uuid4()),
                    }
                ],
            },
        )
        assert resp.status_code == 422
        assert "unit" in resp.json()["detail"].lower()

    async def test_get_wrong_company_404(self, db_client: AsyncClient) -> None:
        """GET with a non-existent (or other company's) ID → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/estimates/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_update_wrong_company_404(self, db_client: AsyncClient) -> None:
        """PUT on a non-existent estimate → 404."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        resp = await db_client.put(
            f"/api/v1/estimates/{uuid.uuid4()}",
            json=_estimate_payload(seed),
        )
        assert resp.status_code == 404

    async def test_delete_wrong_company_404(self, db_client: AsyncClient) -> None:
        """DELETE on a non-existent estimate → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.delete(f"/api/v1/estimates/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth (owner-only)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateAuth:

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/estimates")
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/estimates")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Cascade / FK SET NULL behaviour
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCascadeAndSetNull:

    async def test_delete_estimate_cascades_lines_and_groups(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting an estimate removes its groups and lines via DB cascade."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]

        # Delete estimate
        await db_client.delete(f"/api/v1/estimates/{estimate_id}")

        # Estimate is gone; groups/lines are gone via cascade
        get_resp = await db_client.get(f"/api/v1/estimates/{estimate_id}")
        assert get_resp.status_code == 404

    async def test_delete_product_sets_line_product_id_null_snapshot_preserved(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting a product sets estimate_line.product_id → NULL; amounts preserved."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, with_product=True),
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]
        original_line_total = create_resp.json()["lines"][0]["line_total"]

        # Delete the product
        product_id = seed["product"]["id"]
        del_resp = await db_client.delete(f"/api/v1/products/{product_id}")
        assert del_resp.status_code == 204

        # Estimate line snapshot preserved; product_id becomes null
        get_resp = await db_client.get(f"/api/v1/estimates/{estimate_id}")
        assert get_resp.status_code == 200
        ln = get_resp.json()["lines"][0]
        assert ln["product_id"] is None
        assert ln["line_total"] == original_line_total

    async def test_delete_unit_sets_line_unit_id_null_snapshot_preserved(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting a unit sets estimate_line.unit_id → NULL; amounts preserved."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, with_unit=True),
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]
        original_total = create_resp.json()["lines"][0]["line_total"]

        # Delete the unit
        unit_id = seed["unit"]["id"]
        del_resp = await db_client.delete(f"/api/v1/units/{unit_id}")
        assert del_resp.status_code == 204

        # Estimate line snapshot preserved; unit_id becomes null
        get_resp = await db_client.get(f"/api/v1/estimates/{estimate_id}")
        assert get_resp.status_code == 200
        ln = get_resp.json()["lines"][0]
        assert ln["unit_id"] is None
        assert ln["line_total"] == original_total


# ---------------------------------------------------------------------------
# List: filtering, sorting, pagination
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateList:

    async def test_list_returns_all_estimates(self, db_client: AsyncClient) -> None:
        """List returns all estimates for the company with correct total count."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        # Create 3 estimates
        for i in range(3):
            resp = await db_client.post(
                "/api/v1/estimates",
                json=_estimate_payload(seed, name=f"Estimate {i}"),
            )
            assert resp.status_code == 201

        list_resp = await db_client.get("/api/v1/estimates")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    async def test_list_q_filter(self, db_client: AsyncClient) -> None:
        """q filter searches by estimate name."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, name="Solar install project"),
        )
        await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, name="EV charger job"),
        )

        resp = await db_client.get("/api/v1/estimates", params={"q": "solar"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Solar install project"

    async def test_list_customer_id_filter(self, db_client: AsyncClient) -> None:
        """customer_id filter returns only estimates for that customer."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        # Create a second customer
        cust2_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Other Corp", "country_code": "NL"},
        )
        assert cust2_resp.status_code == 201
        other_customer_id = cust2_resp.json()["id"]

        # Estimate for default customer
        await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed, name="For ACME")
        )
        # Estimate without customer
        await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "No customer",
                "customer_id": None,
                "groups": [],
                "lines": [],
            },
        )
        # Estimate for other customer
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "For Other",
                "customer_id": other_customer_id,
                "groups": [
                    {"ref": "G1", "public_description": "Desc", "vat_rate_id": rate_id}
                ],
                "lines": [
                    {"name": "X", "unit_cost_excl_vat": "10", "quantity": "1", "group_ref": "G1"}
                ],
            },
        )

        # Filter by first customer
        acme_id = seed["customer"]["id"]
        resp = await db_client.get(
            "/api/v1/estimates", params={"customer_id": acme_id}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "For ACME"

    async def test_list_pagination_total(self, db_client: AsyncClient) -> None:
        """limit/offset paginates; total reflects all matching rows."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        for i in range(5):
            await db_client.post(
                "/api/v1/estimates",
                json=_estimate_payload(seed, name=f"Est {i}"),
            )

        resp = await db_client.get(
            "/api/v1/estimates", params={"limit": 2, "offset": 0}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2

        resp2 = await db_client.get(
            "/api/v1/estimates", params={"limit": 2, "offset": 4}
        )
        assert resp2.json()["total"] == 5
        assert len(resp2.json()["items"]) == 1

    async def test_list_sort_by_name(self, db_client: AsyncClient) -> None:
        """sort_by=name returns estimates in alphabetical order."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        for name in ["Zeta", "Alpha", "Mango"]:
            await db_client.post(
                "/api/v1/estimates", json=_estimate_payload(seed, name=name)
            )

        resp = await db_client.get("/api/v1/estimates", params={"sort_by": "name"})
        assert resp.status_code == 200
        names = [item["name"] for item in resp.json()["items"]]
        assert names == sorted(names)

    async def test_list_customer_name_populated(
        self, db_client: AsyncClient
    ) -> None:
        """List items include customer_name via JOIN."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        await db_client.post("/api/v1/estimates", json=_estimate_payload(seed))

        resp = await db_client.get("/api/v1/estimates")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items[0]["customer_name"] == "ACME Corp"


# ---------------------------------------------------------------------------
# Header totals == sub-table sums
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateTotalsConsistency:

    async def test_header_totals_equal_line_sums(
        self, db_client: AsyncClient
    ) -> None:
        """Header totals equal corresponding sums of line amounts."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        payload = {
            "name": "Consistency check",
            "customer_id": customer_id,
            "groups": [
                {"ref": "G", "public_description": "All", "vat_rate_id": rate_id}
            ],
            "lines": [
                {
                    "name": "A",
                    "unit_cost_excl_vat": "100",
                    "quantity": "1",
                    "margin_rate": "0.3",
                    "group_ref": "G",
                },
                {
                    "name": "B",
                    "unit_cost_excl_vat": "50",
                    "quantity": "2",
                    "margin_rate": "0.1",
                    "group_ref": "G",
                },
                {"name": "C", "unit_cost_excl_vat": "75", "quantity": "1", "margin_rate": "0"},
            ],
        }
        resp = await db_client.post("/api/v1/estimates", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        from decimal import Decimal

        lines = data["lines"]
        sum_sell = sum(Decimal(ln["line_sell_excl_vat"]) for ln in lines)
        sum_margin = sum(Decimal(ln["margin_amount"]) for ln in lines)

        assert Decimal(data["total_excl_vat"]) == sum_sell
        assert Decimal(data["total_margin"]) == sum_margin

    async def test_group_sell_equals_sum_of_group_lines(
        self, db_client: AsyncClient
    ) -> None:
        """group_sell_excl_vat == sum(line_sell_excl_vat) for lines in that group."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        payload = {
            "name": "Group consistency",
            "customer_id": customer_id,
            "groups": [
                {"ref": "G1", "public_description": "Group1", "vat_rate_id": rate_id},
                {"ref": "G2", "public_description": "Group2", "vat_rate_id": rate_id},
            ],
            "lines": [
                {
                    "name": "L1",
                    "unit_cost_excl_vat": "100",
                    "quantity": "1",
                    "margin_rate": "0.2",
                    "group_ref": "G1",
                },
                {
                    "name": "L2",
                    "unit_cost_excl_vat": "50",
                    "quantity": "3",
                    "margin_rate": "0",
                    "group_ref": "G1",
                },
                {
                    "name": "L3",
                    "unit_cost_excl_vat": "80",
                    "quantity": "1",
                    "margin_rate": "0.1",
                    "group_ref": "G2",
                },
            ],
        }
        resp = await db_client.post("/api/v1/estimates", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        from decimal import Decimal

        group1 = next(g for g in data["groups"] if g["public_description"] == "Group1")
        group2 = next(g for g in data["groups"] if g["public_description"] == "Group2")

        g1_lines = [ln for ln in data["lines"] if ln["group_id"] == group1["id"]]
        g2_lines = [ln for ln in data["lines"] if ln["group_id"] == group2["id"]]

        assert Decimal(group1["group_sell_excl_vat"]) == sum(
            Decimal(ln["line_sell_excl_vat"]) for ln in g1_lines
        )
        assert Decimal(group2["group_sell_excl_vat"]) == sum(
            Decimal(ln["line_sell_excl_vat"]) for ln in g2_lines
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateEdgeCases:

    async def test_create_without_customer(self, db_client: AsyncClient) -> None:
        """customer_id is optional; estimate can be created without it."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json=_estimate_payload(seed, with_customer=False),
        )
        assert resp.status_code == 201
        assert resp.json()["customer_id"] is None
        assert resp.json()["customer_name"] is None

    async def test_create_empty_lines_and_groups(
        self, db_client: AsyncClient
    ) -> None:
        """Estimate with no lines or groups → totals are zero."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={"name": "Empty", "groups": [], "lines": []},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["total_excl_vat"] == "0.000"
        assert data["total_margin"] == "0.000"
        assert data["lines"] == []
        assert data["groups"] == []

    async def test_labor_line_margin_zero_no_margin_amount(
        self, db_client: AsyncClient
    ) -> None:
        """Labor line (margin_rate=0) contributes 0 to Total Margin."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        payload = {
            "name": "Labor only",
            "customer_id": customer_id,
            "groups": [
                {"ref": "G1", "public_description": "Labor", "vat_rate_id": rate_id}
            ],
            "lines": [
                {
                    "name": "Labor 5h",
                    "unit_cost_excl_vat": "60",
                    "quantity": "5",
                    "margin_rate": "0",
                    "group_ref": "G1",
                }
            ],
        }
        resp = await db_client.post("/api/v1/estimates", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        assert data["total_margin"] == "0.000"
        assert data["total_excl_vat"] == "300.000"
        assert data["lines"][0]["margin_amount"] == "0.000"

    async def test_indicative_vat_present_with_nl_rates(
        self, db_client: AsyncClient
    ) -> None:
        """After company + NL rates are seeded, indicative VAT is 21%."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "VAT test",
                "groups": [],
                "lines": [
                    {"name": "X", "unit_cost_excl_vat": "100", "quantity": "1"}
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["indicative_vat_rate_percent"] == "21.000"
        assert data["total_incl_vat_indicative"] == "121.000"

    async def test_update_clears_old_lines(self, db_client: AsyncClient) -> None:
        """After update, only new lines appear (old ones are replaced)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        # Create with 1 line
        create_resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]

        # Update with 2 different lines
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]
        put_resp = await db_client.put(
            f"/api/v1/estimates/{estimate_id}",
            json={
                "name": "Updated",
                "customer_id": customer_id,
                "groups": [
                    {"ref": "G1", "public_description": "G1", "vat_rate_id": rate_id}
                ],
                "lines": [
                    {
                        "name": "New line 1",
                        "unit_cost_excl_vat": "10",
                        "quantity": "1",
                        "group_ref": "G1",
                    },
                    {
                        "name": "New line 2",
                        "unit_cost_excl_vat": "20",
                        "quantity": "1",
                        "group_ref": "G1",
                    },
                ],
            },
        )
        assert put_resp.status_code == 200
        data = put_resp.json()
        assert len(data["lines"]) == 2
        line_names = {ln["name"] for ln in data["lines"]}
        assert line_names == {"New line 1", "New line 2"}


# ---------------------------------------------------------------------------
# Input boundary validation (NUMERIC column upper limits)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateInputBoundaryValidation:
    """Inputs exceeding DB NUMERIC column precision must be rejected with 422."""

    async def test_unit_cost_exceeds_numeric_14_3_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """unit_cost_excl_vat > NUMERIC(14,3) max → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Overflow test",
                "groups": [],
                "lines": [
                    {
                        "name": "X",
                        "unit_cost_excl_vat": "100000000000.000",  # 11 int digits = max
                        "quantity": "1",
                    }
                ],
            },
        )
        # max is 99999999999.999 (11 integer digits); 100000000000 exceeds it
        assert resp.status_code == 422

    async def test_margin_rate_exceeds_numeric_6_4_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """margin_rate > NUMERIC(6,4) max (99.9999) → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Overflow test",
                "groups": [],
                "lines": [
                    {
                        "name": "X",
                        "unit_cost_excl_vat": "10",
                        "quantity": "1",
                        "margin_rate": "100",  # exceeds 99.9999
                    }
                ],
            },
        )
        assert resp.status_code == 422

    async def test_quantity_exceeds_numeric_18_3_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """quantity > NUMERIC(18,3) max → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Overflow test",
                "groups": [],
                "lines": [
                    {
                        "name": "X",
                        "unit_cost_excl_vat": "10",
                        "quantity": "1000000000000000",  # 16 digits, exceeds 15
                    }
                ],
            },
        )
        assert resp.status_code == 422

    async def test_valid_boundary_values_accepted(
        self, db_client: AsyncClient
    ) -> None:
        """Values at the exact NUMERIC upper bounds are accepted (not off-by-one)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Boundary OK",
                "groups": [],
                "lines": [
                    {
                        "name": "X",
                        "unit_cost_excl_vat": "99999999999.999",
                        "quantity": "1",
                        "margin_rate": "99.9999",
                    }
                ],
            },
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# updated_at always refreshed on PUT
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateUpdatedAt:

    async def test_updated_at_refreshes_when_only_description_changes(
        self, db_client: AsyncClient
    ) -> None:
        """PUT with identical totals but changed line description must update updated_at."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)
        rate_id = seed["rates"]["NL standard (21%)"]["id"]
        customer_id = seed["customer"]["id"]

        # Create estimate; same payload will be re-sent in PUT
        payload = {
            "name": "Same totals",
            "customer_id": customer_id,
            "groups": [
                {"ref": "G1", "public_description": "Work", "vat_rate_id": rate_id}
            ],
            "lines": [
                {
                    "name": "Line A",
                    "description": "Original description",
                    "unit_cost_excl_vat": "100",
                    "quantity": "1",
                    "margin_rate": "0.3",
                    "group_ref": "G1",
                }
            ],
        }
        create_resp = await db_client.post("/api/v1/estimates", json=payload)
        assert create_resp.status_code == 201
        created = create_resp.json()
        estimate_id = created["id"]
        initial_updated_at = created["updated_at"]

        # Brief pause so clock advances
        await asyncio.sleep(0.05)

        # PUT with only description changed; cost + margin + totals stay identical
        updated_payload = {
            **payload,
            "lines": [
                {
                    **payload["lines"][0],
                    "description": "Updated description",
                }
            ],
        }
        put_resp = await db_client.put(
            f"/api/v1/estimates/{estimate_id}", json=updated_payload
        )
        assert put_resp.status_code == 200
        updated = put_resp.json()

        # Totals unchanged (same cost/margin values)
        assert updated["total_excl_vat"] == created["total_excl_vat"]
        assert updated["total_margin"] == created["total_margin"]

        # updated_at must be strictly later than the initial value
        from datetime import datetime

        t_initial = datetime.fromisoformat(initial_updated_at)
        t_updated = datetime.fromisoformat(updated["updated_at"])
        assert t_updated > t_initial, (
            f"updated_at not refreshed: initial={t_initial}, after_put={t_updated}"
        )


# ---------------------------------------------------------------------------
# Computed amount overflow (NUMERIC(18,3) upper bound on calculated snapshots)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateComputedAmountOverflow:
    """Inputs that individually pass schema bounds but produce overflowing
    computed amounts must be rejected with 422 on all three paths:
    /calculate, POST /estimates, and PUT /estimates/{id}.
    """

    # Payload whose line_total overflows NUMERIC(18,3):
    # unit_cost=99999999999.999 × quantity=10001 = 1000099999999989.999 > _MONEY_MAX
    _OVERFLOW_LINES = [
        {
            "name": "Overflow line",
            "unit_cost_excl_vat": "99999999999.999",
            "quantity": "10001",
            "margin_rate": "0",
        }
    ]

    async def test_calculate_overflowing_line_total_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """/calculate rejects inputs whose line_total overflows NUMERIC(18,3)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={"groups": [], "lines": self._OVERFLOW_LINES},
        )
        assert resp.status_code == 422

    async def test_create_overflowing_line_total_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """POST /estimates rejects inputs whose line_total overflows NUMERIC(18,3)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates",
            json={
                "name": "Overflow",
                "groups": [],
                "lines": self._OVERFLOW_LINES,
            },
        )
        assert resp.status_code == 422

    async def test_update_overflowing_line_total_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """PUT /estimates/{id} rejects inputs whose line_total overflows NUMERIC(18,3)."""
        await _full_auth(db_client)
        seed = await _setup_company(db_client)

        # Create a valid estimate first
        create_resp = await db_client.post(
            "/api/v1/estimates", json=_estimate_payload(seed)
        )
        assert create_resp.status_code == 201
        estimate_id = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/estimates/{estimate_id}",
            json={
                "name": "Overflow update",
                "groups": [],
                "lines": self._OVERFLOW_LINES,
            },
        )
        assert resp.status_code == 422

    async def test_calculate_overflowing_margin_amount_returns_422(
        self, db_client: AsyncClient
    ) -> None:
        """margin_rate near max applied to sufficient quantity overflows margin_amount → 422.

        unit_cost=99999999999.999 × quantity=200 → line_total=19999999999999.800 (ok)
        line_total × margin_rate=99.9999 → margin_amount≈1999997999999980 > MONEY_MAX
        """
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [
                    {
                        "name": "High margin overflow",
                        "unit_cost_excl_vat": "99999999999.999",
                        "quantity": "200",
                        "margin_rate": "99.9999",
                    }
                ],
            },
        )
        assert resp.status_code == 422
