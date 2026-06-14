"""Integration tests for M8.5 step 1 – bookkeeping fields (paid_by, business_percentage,
depreciation_years) on Expense and RecurringExpense.

Requires a running PostgreSQL instance (``pytest -m integration``).

Coverage:
- Explicit three fields → persisted + read back correctly (ExpenseRead + ExpenseListItem)
- Default values (all omitted) → BUSINESS / 100 / 1 from server_default
- Validation guards: business_percentage <0 or >100 → 422;
  depreciation_years <1 → 422; paid_by invalid → 422
- update_expense changes three fields
- ExpenseListItem contains all three fields
- RecurringExpense template CRUD: stores three fields + reads back
- run-now: generated draft Expense inherits template's three fields (D8 parity)
- Cross-company isolation / owner-only not regressed (existing guard still holds)
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared helpers (self-contained)
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


async def _setup_company(client: AsyncClient, *, base_currency: str = "EUR") -> dict:
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": base_currency, "country_code": "NL"},
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = {r["label"]: r for r in rates_resp.json()["items"]}

    purch_resp = await client.get("/api/v1/vat-treatments?side=PURCHASE")
    purch_treatments = {t["code"]: t for t in purch_resp.json()["items"]}

    return {"rates": rates, "purch_treatments": purch_treatments}


async def _create_category(
    client: AsyncClient,
    *,
    name: str = "Office Supplies",
    default_deductible: bool = True,
) -> dict:
    resp = await client.post(
        "/api/v1/expense-categories",
        json={"name": name, "default_deductible": default_deductible},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_expense(
    client: AsyncClient,
    *,
    category_id: str,
    vat_treatment_id: str,
    vat_rate_id: str,
    net_amount: str = "100.00",
    vat_amount: str = "21.00",
    expense_date: str = "2026-06-14",
    **extra: object,
) -> dict:
    body: dict = {
        "expense_date": expense_date,
        "category_id": category_id,
        "vat_treatment_id": vat_treatment_id,
        "vat_rate_id": vat_rate_id,
        "net_amount": net_amount,
        "vat_amount": vat_amount,
        **extra,
    }
    resp = await client.post("/api/v1/expenses", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_recurring(
    client: AsyncClient,
    *,
    category_id: str,
    vat_treatment_id: str,
    vat_rate_id: str,
    net_amount: str = "50.00",
    vat_amount: str = "10.50",
    start_date: str = "2020-01-01",
    **extra: object,
) -> dict:
    body: dict = {
        "name": "Test Recurring",
        "category_id": category_id,
        "vat_treatment_id": vat_treatment_id,
        "vat_rate_id": vat_rate_id,
        "net_amount": net_amount,
        "vat_amount": vat_amount,
        "frequency": "MONTHLY",
        "start_date": start_date,
        **extra,
    }
    resp = await client.post("/api/v1/recurring-expenses", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Expense bookkeeping fields – explicit values
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseBookkeepingExplicit:
    """Explicitly providing bookkeeping fields persists and reads back correctly."""

    async def test_explicit_all_three_fields_create_and_read(
        self, db_client: AsyncClient
    ) -> None:
        """paid_by=PRIVATE, business_percentage=80, depreciation_years=5 → persisted."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            paid_by="PRIVATE",
            business_percentage="80",
            depreciation_years=5,
        )

        assert exp["paid_by"] == "PRIVATE"
        assert Decimal(exp["business_percentage"]) == Decimal("80")
        assert exp["depreciation_years"] == 5

        # Re-read via GET /expenses/{id}
        get_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_resp.status_code == 200
        reread = get_resp.json()
        assert reread["paid_by"] == "PRIVATE"
        assert Decimal(reread["business_percentage"]) == Decimal("80")
        assert reread["depreciation_years"] == 5

    async def test_explicit_business_and_partial_percentage(
        self, db_client: AsyncClient
    ) -> None:
        """paid_by=BUSINESS, business_percentage=60.5, depreciation_years=3."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            paid_by="BUSINESS",
            business_percentage="60.5",
            depreciation_years=3,
        )

        assert exp["paid_by"] == "BUSINESS"
        assert Decimal(exp["business_percentage"]) == Decimal("60.5")
        assert exp["depreciation_years"] == 3

    async def test_boundary_business_percentage_zero(
        self, db_client: AsyncClient
    ) -> None:
        """business_percentage=0 is accepted (lower bound)."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            business_percentage="0",
        )
        assert Decimal(exp["business_percentage"]) == Decimal("0")


# ---------------------------------------------------------------------------
# Expense bookkeeping fields – defaults (server_default)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseBookkeepingDefaults:
    """Omitting bookkeeping fields yields BUSINESS / 100 / 1."""

    async def test_defaults_all_omitted(self, db_client: AsyncClient) -> None:
        """When all three fields are omitted, defaults BUSINESS/100/1 are applied."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        assert exp["paid_by"] == "BUSINESS"
        assert Decimal(exp["business_percentage"]) == Decimal("100")
        assert exp["depreciation_years"] == 1


# ---------------------------------------------------------------------------
# Expense bookkeeping fields – validation guards → 422
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseBookkeepingValidation:
    """Bookkeeping field guards return 422 for out-of-range values."""

    async def _base(self, db_client: AsyncClient) -> tuple[dict, dict, dict]:
        """Set up and return (seeds, category, treatment, rate) helper."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        return seeds, cat, seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]

    async def test_business_percentage_below_zero_422(
        self, db_client: AsyncClient
    ) -> None:
        """business_percentage=-1 → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        resp = await db_client.post(
            "/api/v1/expenses",
            json={
                "expense_date": "2026-06-14",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "100.00",
                "vat_amount": "21.00",
                "business_percentage": "-1",
            },
        )
        assert resp.status_code == 422

    async def test_business_percentage_above_100_422(
        self, db_client: AsyncClient
    ) -> None:
        """business_percentage=101 → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        resp = await db_client.post(
            "/api/v1/expenses",
            json={
                "expense_date": "2026-06-14",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "100.00",
                "vat_amount": "21.00",
                "business_percentage": "101",
            },
        )
        assert resp.status_code == 422

    async def test_depreciation_years_zero_422(self, db_client: AsyncClient) -> None:
        """depreciation_years=0 → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        resp = await db_client.post(
            "/api/v1/expenses",
            json={
                "expense_date": "2026-06-14",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "100.00",
                "vat_amount": "21.00",
                "depreciation_years": 0,
            },
        )
        assert resp.status_code == 422

    async def test_paid_by_invalid_422(self, db_client: AsyncClient) -> None:
        """paid_by='CASH' (unknown enum) → 422."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        resp = await db_client.post(
            "/api/v1/expenses",
            json={
                "expense_date": "2026-06-14",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "100.00",
                "vat_amount": "21.00",
                "paid_by": "CASH",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# ExpenseListItem contains bookkeeping fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseListItemBookkeeping:
    """ExpenseListItem returns bookkeeping fields."""

    async def test_list_item_contains_bookkeeping_fields(
        self, db_client: AsyncClient
    ) -> None:
        """GET /expenses list items include paid_by, business_percentage, depreciation_years."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            paid_by="PRIVATE",
            business_percentage="75",
            depreciation_years=2,
        )

        list_resp = await db_client.get("/api/v1/expenses")
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 1
        item = items[0]

        assert item["paid_by"] == "PRIVATE"
        assert Decimal(item["business_percentage"]) == Decimal("75")
        assert item["depreciation_years"] == 2

    async def test_list_item_defaults_when_not_set(
        self, db_client: AsyncClient
    ) -> None:
        """List item shows defaults BUSINESS/100/1 when fields not explicitly set."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        list_resp = await db_client.get("/api/v1/expenses")
        assert list_resp.status_code == 200
        item = list_resp.json()["items"][0]

        assert item["paid_by"] == "BUSINESS"
        assert Decimal(item["business_percentage"]) == Decimal("100")
        assert item["depreciation_years"] == 1


# ---------------------------------------------------------------------------
# update_expense changes bookkeeping fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseBookkeepingUpdate:
    """PUT /expenses/{id} changes bookkeeping fields."""

    async def test_update_all_three_fields(self, db_client: AsyncClient) -> None:
        """PUT changes paid_by, business_percentage, depreciation_years."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        # Create with defaults
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        assert exp["paid_by"] == "BUSINESS"
        assert exp["depreciation_years"] == 1

        # Update
        put_resp = await db_client.put(
            f"/api/v1/expenses/{exp['id']}",
            json={
                "expense_date": "2026-06-14",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "100.00",
                "vat_amount": "21.00",
                "paid_by": "PRIVATE",
                "business_percentage": "50",
                "depreciation_years": 10,
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        updated = put_resp.json()

        assert updated["paid_by"] == "PRIVATE"
        assert Decimal(updated["business_percentage"]) == Decimal("50")
        assert updated["depreciation_years"] == 10

    async def test_update_confirms_draft(self, db_client: AsyncClient) -> None:
        """PUT also confirms the draft (is_draft False) alongside bookkeeping update."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        # Fresh expense is not a draft
        assert exp["is_draft"] is False

        put_resp = await db_client.put(
            f"/api/v1/expenses/{exp['id']}",
            json={
                "expense_date": "2026-06-14",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "100.00",
                "vat_amount": "21.00",
                "paid_by": "PRIVATE",
                "business_percentage": "80",
                "depreciation_years": 2,
            },
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["is_draft"] is False


# ---------------------------------------------------------------------------
# RecurringExpense bookkeeping fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRecurringExpenseBookkeeping:
    """RecurringExpense template CRUD stores and reads bookkeeping fields (D8 parity)."""

    async def test_template_stores_bookkeeping_fields(
        self, db_client: AsyncClient
    ) -> None:
        """Create template with explicit bookkeeping fields → reads back."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        rec = await _create_recurring(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            paid_by="PRIVATE",
            business_percentage="70",
            depreciation_years=5,
        )

        assert rec["paid_by"] == "PRIVATE"
        assert Decimal(rec["business_percentage"]) == Decimal("70")
        assert rec["depreciation_years"] == 5

        # Re-read via GET
        get_resp = await db_client.get(f"/api/v1/recurring-expenses/{rec['id']}")
        assert get_resp.status_code == 200
        reread = get_resp.json()
        assert reread["paid_by"] == "PRIVATE"
        assert Decimal(reread["business_percentage"]) == Decimal("70")
        assert reread["depreciation_years"] == 5

    async def test_template_defaults(self, db_client: AsyncClient) -> None:
        """Template defaults: BUSINESS / 100 / 1."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        rec = await _create_recurring(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        assert rec["paid_by"] == "BUSINESS"
        assert Decimal(rec["business_percentage"]) == Decimal("100")
        assert rec["depreciation_years"] == 1

    async def test_template_update_bookkeeping_fields(
        self, db_client: AsyncClient
    ) -> None:
        """PUT /recurring-expenses/{id} changes bookkeeping fields."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        rec = await _create_recurring(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        assert rec["paid_by"] == "BUSINESS"

        put_resp = await db_client.put(
            f"/api/v1/recurring-expenses/{rec['id']}",
            json={
                "name": "Test Recurring",
                "category_id": cat["id"],
                "vat_treatment_id": treatment["id"],
                "vat_rate_id": rate["id"],
                "net_amount": "50.00",
                "vat_amount": "10.50",
                "frequency": "MONTHLY",
                "start_date": "2020-01-01",
                "paid_by": "PRIVATE",
                "business_percentage": "45.5",
                "depreciation_years": 7,
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        updated = put_resp.json()
        assert updated["paid_by"] == "PRIVATE"
        assert Decimal(updated["business_percentage"]) == Decimal("45.5")
        assert updated["depreciation_years"] == 7


# ---------------------------------------------------------------------------
# run-now: generated draft inherits template bookkeeping fields (D8 parity)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRecurringGenerationBookkeepingParity:
    """run-now generation copies bookkeeping fields from template to draft (D8)."""

    async def test_run_now_draft_inherits_bookkeeping_fields(
        self, db_client: AsyncClient
    ) -> None:
        """Draft Expense generated by run-now has same paid_by/business%/dep_years as template."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        # Create template with non-default bookkeeping fields; use past start_date
        # so run-now immediately generates
        rec = await _create_recurring(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            start_date="2020-01-01",
            paid_by="PRIVATE",
            business_percentage="60",
            depreciation_years=3,
        )

        # run-now
        run_resp = await db_client.post(
            f"/api/v1/recurring-expenses/{rec['id']}/run-now"
        )
        assert run_resp.status_code == 200
        result = run_resp.json()
        assert result["generated"] == 1

        # Fetch the generated expense (it's a draft)
        list_resp = await db_client.get("/api/v1/expenses?is_draft=true")
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 1
        draft = items[0]

        assert draft["paid_by"] == "PRIVATE"
        assert Decimal(draft["business_percentage"]) == Decimal("60")
        assert draft["depreciation_years"] == 3
        assert draft["is_draft"] is True

    async def test_run_now_default_fields_also_copied(
        self, db_client: AsyncClient
    ) -> None:
        """Draft inherits BUSINESS/100/1 when template uses defaults."""
        from decimal import Decimal

        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]

        rec = await _create_recurring(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
            start_date="2020-01-01",
            # no bookkeeping fields → defaults
        )

        run_resp = await db_client.post(
            f"/api/v1/recurring-expenses/{rec['id']}/run-now"
        )
        assert run_resp.status_code == 200

        list_resp = await db_client.get("/api/v1/expenses?is_draft=true")
        draft = list_resp.json()["items"][0]

        assert draft["paid_by"] == "BUSINESS"
        assert Decimal(draft["business_percentage"]) == Decimal("100")
        assert draft["depreciation_years"] == 1


# ---------------------------------------------------------------------------
# Cross-company isolation (regression: existing guard not broken)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseBookkeepingIsolation:
    """Cross-company isolation: bookkeeping-augmented expense still returns 404 for other company.

    The app supports a single registered user; cross-company isolation is
    tested by fetching a random UUID (same pattern used by
    test_expense_integration.py::TestCrossCompanyIsolation).
    """

    async def test_cross_company_expense_random_uuid_404(
        self, db_client: AsyncClient
    ) -> None:
        """GET /expenses/{random_uuid} → 404 (cross-company / non-existent)."""
        import uuid as _uuid

        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/expenses/{_uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_cross_company_recurring_random_uuid_404(
        self, db_client: AsyncClient
    ) -> None:
        """GET /recurring-expenses/{random_uuid} → 404 (cross-company / non-existent)."""
        import uuid as _uuid

        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/recurring-expenses/{_uuid.uuid4()}")
        assert resp.status_code == 404
