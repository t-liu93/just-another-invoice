"""Integration tests for POST /api/v1/estimates/calculate (M6.5 step 1).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- Happy flow: lines + groups → correct per-line / per-group / totals / indicative VAT
- Cross-company vat_rate rejection → 422
- Auth: owner-only, unauthenticated → 401
- No company → 400
- Indicative VAT present with seeded NL 21% rate
- Empty lines / groups → 200 with zeros
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
    """Create company and return seed data (rates)."""
    resp = await client.put(
        "/api/v1/company",
        json={
            "name": "Test Estimate Co",
            "base_currency": "EUR",
            "country_code": "NL",
        },
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = rates_resp.json()["items"]
    by_label = {r["label"]: r for r in rates}

    return {"rates": by_label}


def _calc_payload(
    *,
    groups: list[dict] | None = None,
    lines: list[dict] | None = None,
) -> dict:
    """Build an estimate calculate request payload."""
    if groups is None:
        groups = []
    if lines is None:
        lines = [
            {
                "name": "Device",
                "unit_cost_excl_vat": "100.000",
                "quantity": "1",
                "margin_rate": "0.3",
                "group_ref": "G1",
            },
        ]
        if not groups:
            groups = [{"ref": "G1", "public_description": "Charger installation"}]
            lines[0]["group_ref"] = "G1"
    return {"groups": groups, "lines": lines}


# ---------------------------------------------------------------------------
# Happy flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCalculateHappyFlow:

    async def test_single_line_single_group(self, db_client: AsyncClient) -> None:
        """1 device line in 1 group: cost=100, margin=30%, sell=130."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json=_calc_payload(),
        )
        assert resp.status_code == 200
        data = resp.json()

        # Line
        assert len(data["lines"]) == 1
        line = data["lines"][0]
        assert line["line_total"] == "100.000"
        assert line["margin_amount"] == "30.000"
        assert line["line_sell_excl_vat"] == "130.000"
        assert line["sort_order"] == 0

        # Group
        assert len(data["groups"]) == 1
        group = data["groups"][0]
        assert group["ref"] == "G1"
        assert group["group_sell_excl_vat"] == "130.000"

        # Totals
        assert data["total_margin"] == "30.000"
        assert data["total_excl_vat"] == "130.000"

        # Indicative VAT (NL 21% standard rate)
        assert data["indicative_vat_rate_percent"] == "21.000"
        assert data["total_incl_vat_indicative"] == "157.300"  # 130 * 1.21

    async def test_multiple_lines_multiple_groups(
        self, db_client: AsyncClient
    ) -> None:
        """2 device lines in group A + 1 labor line in group B."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json=_calc_payload(
                groups=[
                    {"ref": "A", "public_description": "Charger + cable"},
                    {"ref": "B", "public_description": "Installation"},
                ],
                lines=[
                    {
                        "name": "Charger",
                        "unit_cost_excl_vat": "500",
                        "quantity": "1",
                        "margin_rate": "0.3",
                        "group_ref": "A",
                    },
                    {
                        "name": "Cable",
                        "unit_cost_excl_vat": "50",
                        "quantity": "2",
                        "margin_rate": "0.2",
                        "group_ref": "A",
                    },
                    {
                        "name": "Labor",
                        "unit_cost_excl_vat": "75",
                        "quantity": "1",
                        "margin_rate": "0",
                        "group_ref": "B",
                    },
                ],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()

        # Lines
        assert len(data["lines"]) == 3
        # Charger: total=500, margin=150, sell=650
        assert data["lines"][0]["line_sell_excl_vat"] == "650.000"
        # Cable: total=100, margin=20, sell=120
        assert data["lines"][1]["line_sell_excl_vat"] == "120.000"
        # Labor: total=75, margin=0, sell=75
        assert data["lines"][2]["line_sell_excl_vat"] == "75.000"

        # Groups
        group_a = next(g for g in data["groups"] if g["ref"] == "A")
        group_b = next(g for g in data["groups"] if g["ref"] == "B")
        assert group_a["group_sell_excl_vat"] == "770.000"  # 650 + 120
        assert group_b["group_sell_excl_vat"] == "75.000"

        # Totals
        assert data["total_margin"] == "170.000"  # 150 + 20 + 0
        assert data["total_excl_vat"] == "845.000"  # 650 + 120 + 75

        # Indicative: 845 * 1.21 = 1022.45
        assert data["total_incl_vat_indicative"] == "1022.450"

    async def test_ungrouped_lines_contribute_to_total_not_group(
        self, db_client: AsyncClient
    ) -> None:
        """Ungrouped lines: count toward total, not toward group."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json=_calc_payload(
                groups=[{"ref": "G1", "public_description": "Main"}],
                lines=[
                    {
                        "name": "Device",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                        "margin_rate": "0.2",
                        "group_ref": "G1",
                    },
                    {
                        "name": "Overhead",
                        "unit_cost_excl_vat": "50",
                        "quantity": "1",
                        "margin_rate": "0",
                    },
                ],
            ),
        )
        assert resp.status_code == 200
        data = resp.json()

        # Group G1 sell = device only = 120
        assert data["groups"][0]["group_sell_excl_vat"] == "120.000"
        # Total includes both: 120 + 50 = 170
        assert data["total_excl_vat"] == "170.000"

    async def test_empty_lines_and_groups(self, db_client: AsyncClient) -> None:
        """Empty estimate → all zeros, indicative still computed."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={"groups": [], "lines": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lines"] == []
        assert data["groups"] == []
        assert data["total_margin"] == "0.000"
        assert data["total_excl_vat"] == "0.000"
        assert data["total_incl_vat_indicative"] == "0.000"

    async def test_no_groups_only_lines(self, db_client: AsyncClient) -> None:
        """Lines without groups → totals correct, groups empty."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [
                    {
                        "name": "A",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                        "margin_rate": "0.1",
                    },
                    {
                        "name": "B",
                        "unit_cost_excl_vat": "50",
                        "quantity": "1",
                        "margin_rate": "0",
                    },
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["groups"]) == 0
        assert data["total_excl_vat"] == "160.000"  # 110 + 50
        assert data["total_margin"] == "10.000"


# ---------------------------------------------------------------------------
# VAT rate validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCalculateVatRateValidation:

    async def test_cross_company_vat_rate_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """VAT rate from another company → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        fake_rate_id = str(uuid.uuid4())

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [
                    {"ref": "G1", "public_description": "Test", "vat_rate_id": fake_rate_id},
                ],
                "lines": [
                    {"name": "X", "unit_cost_excl_vat": "100", "quantity": "1"},
                ],
            },
        )
        assert resp.status_code == 422
        assert "not found" in resp.json()["detail"].lower()

    async def test_group_vat_rate_null_accepted(
        self, db_client: AsyncClient
    ) -> None:
        """Group without vat_rate_id → 200 (draft state)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [{"ref": "G1", "public_description": "Draft group"}],
                "lines": [{"name": "X", "unit_cost_excl_vat": "100", "quantity": "1"}],
            },
        )
        assert resp.status_code == 200

    async def test_valid_vat_rate_accepted(
        self, db_client: AsyncClient
    ) -> None:
        """Group with a valid vat_rate_id → 200."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        rate_21_id = seeds["rates"]["NL standard (21%)"]["id"]

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [
                    {"ref": "G1", "public_description": "Test", "vat_rate_id": rate_21_id},
                ],
                "lines": [
                    {"name": "X", "unit_cost_excl_vat": "100", "quantity": "1", "group_ref": "G1"},
                ],
            },
        )
        assert resp.status_code == 200

    async def test_indicative_vat_present_with_seeded_rates(
        self, db_client: AsyncClient
    ) -> None:
        """Indicative VAT is computed (seeded NL 21% is the highest active rate)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [{"name": "X", "unit_cost_excl_vat": "100", "quantity": "1"}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["indicative_vat_rate_percent"] == "21.000"
        assert data["total_incl_vat_indicative"] == "121.000"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCalculateAuth:

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={"groups": [], "lines": []},
        )
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        """Authenticated but no company → 400."""
        await _full_auth(db_client)
        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={"groups": [], "lines": []},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateCalculateSchemaValidation:

    async def test_negative_unit_cost_422(self, db_client: AsyncClient) -> None:
        """unit_cost_excl_vat < 0 → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [{"name": "X", "unit_cost_excl_vat": "-5", "quantity": "1"}],
            },
        )
        assert resp.status_code == 422

    async def test_zero_quantity_422(self, db_client: AsyncClient) -> None:
        """quantity = 0 → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [{"name": "X", "unit_cost_excl_vat": "100", "quantity": "0"}],
            },
        )
        assert resp.status_code == 422

    async def test_negative_margin_rate_422(self, db_client: AsyncClient) -> None:
        """margin_rate < 0 → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [
                    {
                        "name": "X",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                        "margin_rate": "-0.1",
                    }
                ],
            },
        )
        assert resp.status_code == 422

    async def test_missing_name_422(self, db_client: AsyncClient) -> None:
        """Missing required 'name' field → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [{"unit_cost_excl_vat": "100", "quantity": "1"}],
            },
        )
        assert resp.status_code == 422

    async def test_whitespace_stripped_from_name(self, db_client: AsyncClient) -> None:
        """Leading/trailing whitespace in name is stripped."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [{"name": "  Device  ", "unit_cost_excl_vat": "100", "quantity": "1"}],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["lines"][0]["group_ref"] is None


# ---------------------------------------------------------------------------
# Review finding 1: group ref uniqueness & integrity
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateGroupRefValidation:

    async def test_duplicate_group_ref_422(
        self, db_client: AsyncClient
    ) -> None:
        """Two groups with the same ref → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [
                    {"ref": "G1", "public_description": "First"},
                    {"ref": "G1", "public_description": "Second"},
                ],
                "lines": [],
            },
        )
        assert resp.status_code == 422
        assert "duplicate" in resp.json()["detail"][0]["msg"].lower()

    async def test_invalid_line_group_ref_422(
        self, db_client: AsyncClient
    ) -> None:
        """Line group_ref not matching any group → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [{"ref": "G1", "public_description": "Main"}],
                "lines": [
                    {
                        "name": "Orphan",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                        "group_ref": "UNKNOWN",
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert "does not match" in resp.json()["detail"][0]["msg"].lower()


# ---------------------------------------------------------------------------
# Review finding 3: blank strings rejected
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEstimateBlankStringValidation:

    async def test_blank_name_422(self, db_client: AsyncClient) -> None:
        """Whitespace-only name → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [],
                "lines": [
                    {
                        "name": "   ",
                        "unit_cost_excl_vat": "100",
                        "quantity": "1",
                    },
                ],
            },
        )
        assert resp.status_code == 422

    async def test_blank_public_description_422(
        self, db_client: AsyncClient
    ) -> None:
        """Whitespace-only public_description → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [
                    {"ref": "G1", "public_description": "   "},
                ],
                "lines": [],
            },
        )
        assert resp.status_code == 422

    async def test_blank_ref_422(self, db_client: AsyncClient) -> None:
        """Whitespace-only ref → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/estimates/calculate",
            json={
                "groups": [{"ref": "  ", "public_description": "Desc"}],
                "lines": [],
            },
        )
        assert resp.status_code == 422
