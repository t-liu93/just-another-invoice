"""Integration tests for payment_method / expense_category / unit CRUD (M4 step 2).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers per-resource:
- Full CRUD roundtrip + list
- 404 for unknown id / wrong company
- UNIQUE conflict → 409
- Empty name/code → 422
- company-scoped isolation (cross-company → 404)
- owner-only (unauthenticated → 401, no company → 400)
- Seed seeding: new company → 2 payment methods + 5 expense categories + 3 units
- Seed idempotent (second company creation must not duplicate)
"""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models.company import Company

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


async def _setup_company(client: AsyncClient) -> None:
    resp = await client.put("/api/v1/company", json={"name": "Test Co", "base_currency": "EUR"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Payment Methods
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPaymentMethodCRUD:

    async def test_create(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/payment-methods", json={"name": "Wire Transfer", "active": True}
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Wire Transfer"
        assert data["active"] is True
        assert "id" in data
        assert "company_id" not in data

    async def test_list_includes_seeds(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/payment-methods")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()["items"]}
        assert "Bank transfer" in names
        assert "Cash" in names

    async def test_get(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        items = (await db_client.get("/api/v1/payment-methods")).json()["items"]
        item_id = items[0]["id"]

        resp = await db_client.get(f"/api/v1/payment-methods/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == item_id

    async def test_update(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/payment-methods", json={"name": "Before"})
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/payment-methods/{item_id}",
            json={"name": "After", "active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "After"
        assert resp.json()["active"] is False

    async def test_delete(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/payment-methods", json={"name": "Temp"})
        item_id = cr.json()["id"]

        resp = await db_client.delete(f"/api/v1/payment-methods/{item_id}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/payment-methods/{item_id}")
        assert resp.status_code == 404

    async def test_404_unknown(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        for method in ("get", "put", "delete"):
            func = getattr(db_client, method)
            kwargs = (
                {"json": {"name": "X"}} if method == "put" else {}
            )
            resp = await func(f"/api/v1/payment-methods/{uuid.uuid4()}", **kwargs)
            assert resp.status_code == 404

    async def test_duplicate_name_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/payment-methods", json={"name": "Dup"})
        resp = await db_client.post("/api/v1/payment-methods", json={"name": "Dup"})
        assert resp.status_code == 409

    async def test_update_to_duplicate_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/payment-methods", json={"name": "A"})
        cr = await db_client.post("/api/v1/payment-methods", json={"name": "B"})
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/payment-methods/{item_id}", json={"name": "A"}
        )
        assert resp.status_code == 409

    async def test_update_same_name_no_conflict(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/payment-methods", json={"name": "Same"})
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/payment-methods/{item_id}", json={"name": "Same", "active": False}
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_empty_name_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post("/api/v1/payment-methods", json={"name": ""})
        assert resp.status_code == 422

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/payment-methods")
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/payment-methods")
        assert resp.status_code == 400

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.dictionary import PaymentMethod

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        item_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                PaymentMethod(
                    id=item_b_id,
                    company_id=company_b_id,
                    name="Company B Method",
                )
            )
            await session.commit()

        resp = await db_client.get(f"/api/v1/payment-methods/{item_b_id}")
        assert resp.status_code == 404

        list_resp = await db_client.get("/api/v1/payment-methods")
        names = [r["name"] for r in list_resp.json()["items"]]
        assert "Company B Method" not in names


# ---------------------------------------------------------------------------
# Expense Categories
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExpenseCategoryCRUD:

    async def test_create(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/expense-categories",
            json={
                "name": "Custom Cat",
                "description": "My desc",
                "default_deductible": True,
                "active": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Custom Cat"
        assert data["description"] == "My desc"
        assert data["default_deductible"] is True
        assert "company_id" not in data

    async def test_list_includes_seeds(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/expense-categories")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()["items"]}
        assert {"Tool", "Consumable", "Product", "Regular", "Travel"}.issubset(names)

    async def test_get(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        items = (await db_client.get("/api/v1/expense-categories")).json()["items"]
        item_id = items[0]["id"]

        resp = await db_client.get(f"/api/v1/expense-categories/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == item_id

    async def test_update(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post(
            "/api/v1/expense-categories", json={"name": "Before"}
        )
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/expense-categories/{item_id}",
            json={
                "name": "After",
                "description": "desc",
                "default_deductible": False,
                "active": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "After"
        assert resp.json()["description"] == "desc"
        assert resp.json()["default_deductible"] is False

    async def test_delete(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/expense-categories", json={"name": "Temp"})
        item_id = cr.json()["id"]

        resp = await db_client.delete(f"/api/v1/expense-categories/{item_id}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/expense-categories/{item_id}")
        assert resp.status_code == 404

    async def test_404_unknown(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/expense-categories/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_duplicate_name_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/expense-categories", json={"name": "Dup"})
        resp = await db_client.post("/api/v1/expense-categories", json={"name": "Dup"})
        assert resp.status_code == 409

    async def test_empty_name_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post("/api/v1/expense-categories", json={"name": ""})
        assert resp.status_code == 422

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/expense-categories")
        assert resp.status_code == 401

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.dictionary import ExpenseCategory

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        item_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                ExpenseCategory(
                    id=item_b_id, company_id=company_b_id, name="Company B Cat"
                )
            )
            await session.commit()

        resp = await db_client.get(f"/api/v1/expense-categories/{item_b_id}")
        assert resp.status_code == 404

        list_resp = await db_client.get("/api/v1/expense-categories")
        names = [r["name"] for r in list_resp.json()["items"]]
        assert "Company B Cat" not in names


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUnitCRUD:

    async def test_create(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/units", json={"code": "kg", "name": "Kilogram", "active": True}
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == "kg"
        assert data["name"] == "Kilogram"
        assert "company_id" not in data

    async def test_list_includes_seeds(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/units")
        assert resp.status_code == 200
        codes = {r["code"] for r in resp.json()["items"]}
        assert {"pcs", "hr", "m"}.issubset(codes)
        names = {r["name"] for r in resp.json()["items"]}
        assert {"Piece", "Hour", "Meter"}.issubset(names)

    async def test_get(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        items = (await db_client.get("/api/v1/units")).json()["items"]
        item_id = items[0]["id"]

        resp = await db_client.get(f"/api/v1/units/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == item_id

    async def test_update(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/units", json={"code": "old", "name": "Old"})
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/units/{item_id}",
            json={"code": "new", "name": "New", "active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "new"
        assert resp.json()["name"] == "New"
        assert resp.json()["active"] is False

    async def test_delete(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/units", json={"code": "tmp", "name": "Tmp"})
        item_id = cr.json()["id"]

        resp = await db_client.delete(f"/api/v1/units/{item_id}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/units/{item_id}")
        assert resp.status_code == 404

    async def test_404_unknown(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/units/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_duplicate_code_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/units", json={"code": "dup", "name": "Dup"})
        resp = await db_client.post(
            "/api/v1/units", json={"code": "dup", "name": "Other"}
        )
        assert resp.status_code == 409

    async def test_update_to_duplicate_code_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/units", json={"code": "aa", "name": "AA"})
        cr = await db_client.post("/api/v1/units", json={"code": "bb", "name": "BB"})
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/units/{item_id}", json={"code": "aa", "name": "BB"}
        )
        assert resp.status_code == 409

    async def test_update_same_code_no_conflict(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post(
            "/api/v1/units", json={"code": "same", "name": "Same"}
        )
        item_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/units/{item_id}",
            json={"code": "same", "name": "Updated", "active": True},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    async def test_empty_code_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/units", json={"code": "", "name": "Name"}
        )
        assert resp.status_code == 422

    async def test_empty_name_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/units", json={"code": "x", "name": ""}
        )
        assert resp.status_code == 422

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/units")
        assert resp.status_code == 401

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.dictionary import Unit

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        item_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                Unit(
                    id=item_b_id,
                    company_id=company_b_id,
                    code="cb_unit",
                    name="Company B Unit",
                )
            )
            await session.commit()

        resp = await db_client.get(f"/api/v1/units/{item_b_id}")
        assert resp.status_code == 404

        list_resp = await db_client.get("/api/v1/units")
        codes = [r["code"] for r in list_resp.json()["items"]]
        assert "cb_unit" not in codes


# ---------------------------------------------------------------------------
# Seed seeding
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDictionarySeeds:

    async def test_seed_payment_methods_on_company_creation(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/payment-methods")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()["items"]}
        assert "Bank transfer" in names
        assert "Cash" in names

    async def test_seed_expense_categories_on_company_creation(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/expense-categories")
        assert resp.status_code == 200
        names = {r["name"] for r in resp.json()["items"]}
        assert {"Tool", "Consumable", "Product", "Regular", "Travel"}.issubset(names)

    async def test_seed_units_on_company_creation(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/units")
        assert resp.status_code == 200
        items = resp.json()["items"]
        by_code = {r["code"]: r for r in items}
        assert "pcs" in by_code
        assert by_code["pcs"]["name"] == "Piece"
        assert "hr" in by_code
        assert by_code["hr"]["name"] == "Hour"
        assert "m" in by_code
        assert by_code["m"]["name"] == "Meter"

    async def test_seed_idempotent(self, db_client: AsyncClient) -> None:
        """Calling company update a second time must not duplicate seeds."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Second save (update path).
        await db_client.put(
            "/api/v1/company", json={"name": "Updated Co", "base_currency": "EUR"}
        )

        pm_items = (await db_client.get("/api/v1/payment-methods")).json()["items"]
        bank_count = sum(1 for r in pm_items if r["name"] == "Bank transfer")
        assert bank_count == 1

        ec_items = (await db_client.get("/api/v1/expense-categories")).json()["items"]
        tool_count = sum(1 for r in ec_items if r["name"] == "Tool")
        assert tool_count == 1

        unit_items = (await db_client.get("/api/v1/units")).json()["items"]
        pcs_count = sum(1 for r in unit_items if r["code"] == "pcs")
        assert pcs_count == 1
