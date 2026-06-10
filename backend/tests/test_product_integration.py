"""Integration tests for product_category + product CRUD (M4 step 3).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- ProductCategory: CRUD roundtrip, 404, UNIQUE 409, empty name 422,
  isolation, owner-only.
- Product: CRUD roundtrip, list (q, category_id, pagination, sort),
  effective_margin_rate (override vs inherit vs none),
  delete category → product.category_id SET NULL (DB-level cascade, red-line 3),
  purchase_cost Decimal precision, isolation, owner-only.
- FK validation: cross-company category/unit/vat_rate → 422, non-existent → 422.
- Decimal bounds: margin_rate/default_margin_rate/cost overflow → 422.
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
    resp = await client.put(
        "/api/v1/company", json={"name": "Test Co", "base_currency": "EUR"}
    )
    assert resp.status_code == 200


async def _create_category(
    client: AsyncClient,
    name: str = "Electronics",
    default_margin_rate: float | None = 0.3,
) -> dict:
    payload: dict = {"name": name, "active": True}
    if default_margin_rate is not None:
        payload["default_margin_rate"] = str(default_margin_rate)
    resp = await client.post("/api/v1/product-categories", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# ProductCategory CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductCategoryCRUD:

    async def test_create(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/product-categories",
            json={"name": "Solar", "default_margin_rate": "0.25", "active": True},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Solar"
        assert float(data["default_margin_rate"]) == pytest.approx(0.25, rel=1e-3)
        assert data["active"] is True
        assert "id" in data
        assert "company_id" not in data

    async def test_list_empty_no_seeds(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/product-categories")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_get(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "Tools")
        cat_id = cat["id"]

        resp = await db_client.get(f"/api/v1/product-categories/{cat_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Tools"

    async def test_update(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "Before", default_margin_rate=0.2)
        cat_id = cat["id"]

        resp = await db_client.put(
            f"/api/v1/product-categories/{cat_id}",
            json={"name": "After", "default_margin_rate": "0.4", "active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "After"
        assert float(resp.json()["default_margin_rate"]) == pytest.approx(0.4, rel=1e-3)
        assert resp.json()["active"] is False

    async def test_delete(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "TempCat")
        cat_id = cat["id"]

        resp = await db_client.delete(f"/api/v1/product-categories/{cat_id}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/product-categories/{cat_id}")
        assert resp.status_code == 404

    async def test_404_unknown(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/product-categories/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_duplicate_name_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await _create_category(db_client, "Dup")
        resp = await db_client.post(
            "/api/v1/product-categories", json={"name": "Dup"}
        )
        assert resp.status_code == 409

    async def test_update_to_duplicate_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await _create_category(db_client, "A")
        cat_b = await _create_category(db_client, "B")

        resp = await db_client.put(
            f"/api/v1/product-categories/{cat_b['id']}",
            json={"name": "A"},
        )
        assert resp.status_code == 409

    async def test_update_same_name_no_conflict(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "Same")
        resp = await db_client.put(
            f"/api/v1/product-categories/{cat['id']}",
            json={"name": "Same", "active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_empty_name_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post("/api/v1/product-categories", json={"name": ""})
        assert resp.status_code == 422

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/product-categories")
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/product-categories")
        assert resp.status_code == 400

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.product import ProductCategory

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        cat_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                ProductCategory(
                    id=cat_b_id,
                    company_id=company_b_id,
                    name="Company B Category",
                )
            )
            await session.commit()

        resp = await db_client.get(f"/api/v1/product-categories/{cat_b_id}")
        assert resp.status_code == 404

        list_resp = await db_client.get("/api/v1/product-categories")
        names = [r["name"] for r in list_resp.json()["items"]]
        assert "Company B Category" not in names


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductCRUD:

    async def test_create_minimal(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post("/api/v1/products", json={"name": "Widget"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Widget"
        assert data["sku"] is None
        assert data["purchase_cost_excl_vat"] is None
        assert data["margin_rate"] is None
        assert data["effective_margin_rate"] is None
        assert "company_id" not in data

    async def test_create_full(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "Solar Panels", default_margin_rate=0.3)

        resp = await db_client.post(
            "/api/v1/products",
            json={
                "name": "330W Panel",
                "sku": "SP330",
                "category_id": cat["id"],
                "purchase_cost_excl_vat": "180.000",
                "margin_rate": "0.35",
                "supplier": "Acme Solar",
                "extra": {"brand": "Suntech"},
                "active": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "330W Panel"
        assert data["sku"] == "SP330"
        assert float(data["purchase_cost_excl_vat"]) == pytest.approx(180.0, rel=1e-3)
        assert float(data["margin_rate"]) == pytest.approx(0.35, rel=1e-3)
        # product's own margin_rate overrides category default
        assert float(data["effective_margin_rate"]) == pytest.approx(0.35, rel=1e-3)
        assert data["supplier"] == "Acme Solar"
        assert data["extra"] == {"brand": "Suntech"}

    async def test_get(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/products", json={"name": "GetMe"})
        product_id = cr.json()["id"]

        resp = await db_client.get(f"/api/v1/products/{product_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == product_id

    async def test_update(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/products", json={"name": "Before"})
        product_id = cr.json()["id"]

        resp = await db_client.put(
            f"/api/v1/products/{product_id}",
            json={"name": "After", "supplier": "NewCo", "active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "After"
        assert resp.json()["supplier"] == "NewCo"
        assert resp.json()["active"] is False

    async def test_delete(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/products", json={"name": "TempProd"})
        product_id = cr.json()["id"]

        resp = await db_client.delete(f"/api/v1/products/{product_id}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/products/{product_id}")
        assert resp.status_code == 404

    async def test_404_unknown(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/products/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_empty_name_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post("/api/v1/products", json={"name": ""})
        assert resp.status_code == 422

    async def test_negative_cost_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "Bad", "purchase_cost_excl_vat": "-1"},
        )
        assert resp.status_code == 422

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/products")
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/products")
        assert resp.status_code == 400

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.product import Product

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        product_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                Product(
                    id=product_b_id,
                    company_id=company_b_id,
                    name="Company B Product",
                )
            )
            await session.commit()

        resp = await db_client.get(f"/api/v1/products/{product_b_id}")
        assert resp.status_code == 404

        list_resp = await db_client.get("/api/v1/products")
        names = [r["name"] for r in list_resp.json()["items"]]
        assert "Company B Product" not in names


# ---------------------------------------------------------------------------
# effective_margin_rate resolution
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEffectiveMarginRate:

    async def test_product_margin_overrides_category(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "CatA", default_margin_rate=0.3)
        cr = await db_client.post(
            "/api/v1/products",
            json={"name": "P1", "category_id": cat["id"], "margin_rate": "0.45"},
        )
        data = cr.json()
        assert float(data["margin_rate"]) == pytest.approx(0.45, rel=1e-3)
        assert float(data["effective_margin_rate"]) == pytest.approx(0.45, rel=1e-3)

    async def test_inherits_category_default(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "CatB", default_margin_rate=0.3)
        cr = await db_client.post(
            "/api/v1/products",
            json={"name": "P2", "category_id": cat["id"]},
        )
        data = cr.json()
        assert data["margin_rate"] is None
        assert float(data["effective_margin_rate"]) == pytest.approx(0.3, rel=1e-3)

    async def test_no_margin_no_category(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/products", json={"name": "P3"})
        data = cr.json()
        assert data["margin_rate"] is None
        assert data["effective_margin_rate"] is None

    async def test_effective_margin_still_null_when_category_has_no_default(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "CatNoMargin", default_margin_rate=None)
        cr = await db_client.post(
            "/api/v1/products",
            json={"name": "P4", "category_id": cat["id"]},
        )
        data = cr.json()
        assert data["effective_margin_rate"] is None


# ---------------------------------------------------------------------------
# List filtering, pagination, sorting
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductList:

    async def test_list_pagination(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        for i in range(5):
            await db_client.post("/api/v1/products", json={"name": f"Prod{i:02d}"})

        resp = await db_client.get("/api/v1/products?limit=3&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 3

        resp2 = await db_client.get("/api/v1/products?limit=3&offset=3")
        data2 = resp2.json()
        assert data2["total"] == 5
        assert len(data2["items"]) == 2

    async def test_list_search_by_name(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/products", json={"name": "Solar Panel 330W"})
        await db_client.post("/api/v1/products", json={"name": "Inverter 5kW"})

        resp = await db_client.get("/api/v1/products?q=solar")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()["items"]]
        assert "Solar Panel 330W" in names
        assert "Inverter 5kW" not in names

    async def test_list_search_by_sku(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/products", json={"name": "Panel X", "sku": "PNL-001"}
        )
        await db_client.post("/api/v1/products", json={"name": "Inverter Y"})

        resp = await db_client.get("/api/v1/products?q=PNL")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()["items"]]
        assert "Panel X" in names
        assert "Inverter Y" not in names

    async def test_list_filter_by_category(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat_a = await _create_category(db_client, "CatFilter")
        cat_b = await _create_category(db_client, "CatOther")

        await db_client.post(
            "/api/v1/products",
            json={"name": "InCatA", "category_id": cat_a["id"]},
        )
        await db_client.post(
            "/api/v1/products",
            json={"name": "InCatB", "category_id": cat_b["id"]},
        )
        await db_client.post("/api/v1/products", json={"name": "NoCat"})

        resp = await db_client.get(f"/api/v1/products?category_id={cat_a['id']}")
        assert resp.status_code == 200
        data = resp.json()
        names = [r["name"] for r in data["items"]]
        assert names == ["InCatA"]
        assert data["total"] == 1

    async def test_list_sort_by_name(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        for n in ["Zebra", "Alpha", "Mango"]:
            await db_client.post("/api/v1/products", json={"name": n})

        resp = await db_client.get("/api/v1/products?sort_by=name")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()["items"]]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# DB-level cascade: delete category → product.category_id SET NULL (red-line 3)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductCategoryCascade:

    async def test_delete_category_sets_product_category_id_null(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "WillDelete")
        cat_id = cat["id"]

        cr = await db_client.post(
            "/api/v1/products",
            json={"name": "OrphanProduct", "category_id": cat_id},
        )
        assert cr.status_code == 201
        product_id = cr.json()["id"]

        # Delete the category.
        resp = await db_client.delete(f"/api/v1/product-categories/{cat_id}")
        assert resp.status_code == 204

        # Product must still exist but with category_id = NULL (DB SET NULL).
        resp2 = await db_client.get(f"/api/v1/products/{product_id}")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["category_id"] is None


# ---------------------------------------------------------------------------
# Decimal precision
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductDecimalPrecision:

    async def test_purchase_cost_three_decimal_places(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "Precise", "purchase_cost_excl_vat": "99.999"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert float(data["purchase_cost_excl_vat"]) == pytest.approx(99.999, rel=1e-4)


# ---------------------------------------------------------------------------
# FK cross-company validation (P1 fix)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductFKValidation:
    """Products may not reference categories/units/vat_rates from another company."""

    async def test_cross_company_category_id_422(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.product import ProductCategory

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        cat_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="FKTest B", base_currency="EUR"))
            await session.flush()
            session.add(
                ProductCategory(
                    id=cat_b_id, company_id=company_b_id, name="Cat in B"
                )
            )
            await session.commit()

        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "BadProduct", "category_id": str(cat_b_id)},
        )
        assert resp.status_code == 422

    async def test_nonexistent_category_id_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "Ghost", "category_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 422

    async def test_cross_company_unit_id_422(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.dictionary import Unit

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        unit_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="FKTest C", base_currency="EUR"))
            await session.flush()
            session.add(
                Unit(id=unit_b_id, company_id=company_b_id, code="pcs", name="Pieces")
            )
            await session.commit()

        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "UnitBad", "unit_id": str(unit_b_id)},
        )
        assert resp.status_code == 422

    async def test_cross_company_vat_rate_id_422(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.vat import VatRate

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        vat_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="FKTest D", base_currency="EUR"))
            await session.flush()
            session.add(
                VatRate(
                    id=vat_b_id,
                    company_id=company_b_id,
                    label="21% NL",
                    percent="21.000",
                )
            )
            await session.commit()

        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "VatBad", "default_vat_rate_id": str(vat_b_id)},
        )
        assert resp.status_code == 422

    async def test_update_cross_company_category_422(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.product import ProductCategory

        await _full_auth(db_client)
        await _setup_company(db_client)

        cr = await db_client.post("/api/v1/products", json={"name": "UpdateTarget"})
        assert cr.status_code == 201
        product_id = cr.json()["id"]

        company_b_id = uuid.uuid4()
        cat_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="FKTest E", base_currency="EUR"))
            await session.flush()
            session.add(
                ProductCategory(
                    id=cat_b_id, company_id=company_b_id, name="Cat in E"
                )
            )
            await session.commit()

        resp = await db_client.put(
            f"/api/v1/products/{product_id}",
            json={"name": "UpdateTarget", "category_id": str(cat_b_id)},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Decimal upper-bound validation (P2 fix)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Product import (M4 step 4)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductImport:
    """POST /api/v1/products/import – bulk create from structured rows."""

    async def test_import_all_valid(self, db_client: AsyncClient) -> None:
        """Happy path: all rows valid → all created, no errors."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={
                "rows": [
                    {"name": "Panel 330W", "purchase_cost_excl_vat": "120.000"},
                    {"name": "Inverter 5kW", "sku": "INV5K"},
                    {"name": "Cable 10m"},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 3
        assert data["errors"] == []

        # All three should appear in the products list.
        list_resp = await db_client.get("/api/v1/products")
        names = [r["name"] for r in list_resp.json()["items"]]
        assert "Panel 330W" in names
        assert "Inverter 5kW" in names
        assert "Cable 10m" in names

    async def test_import_resolves_category_by_name(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "Solar", default_margin_rate=0.3)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Panel", "category_name": "Solar"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["errors"] == []

        list_resp = await db_client.get("/api/v1/products")
        items = list_resp.json()["items"]
        assert len(items) == 1
        assert items[0]["category_id"] == cat["id"]

    async def test_import_resolves_unit_by_code(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Need a unit; use the seeded units (they are seeded on company setup).
        unit_resp = await db_client.get("/api/v1/units")
        assert unit_resp.status_code == 200
        units = unit_resp.json()["items"]
        piece_unit = next((u for u in units if u["code"] == "pcs"), None)
        assert piece_unit is not None

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Bolt", "unit_code": "pcs"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["errors"] == []

        item = await db_client.get("/api/v1/products")
        prod = item.json()["items"][0]
        assert prod["unit_id"] == piece_unit["id"]

    async def test_import_resolves_vat_percent(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # VAT rates are seeded; get the 21% one.
        vat_resp = await db_client.get("/api/v1/vat-rates")
        assert vat_resp.status_code == 200
        rates = vat_resp.json()["items"]
        vat21 = next((r for r in rates if float(r["percent"]) == 21.0), None)
        assert vat21 is not None

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Panel", "vat_percent": "21"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["errors"] == []

        prod = (await db_client.get("/api/v1/products")).json()["items"][0]
        assert prod["default_vat_rate_id"] == vat21["id"]

    async def test_import_unknown_category_goes_to_errors(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Widget", "category_name": "NonExistentCat"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["row"] == 0
        assert "NonExistentCat" in data["errors"][0]["message"]

    async def test_import_unknown_unit_code_goes_to_errors(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Bolt", "unit_code": "XYZ_UNKNOWN"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert len(data["errors"]) == 1
        assert "XYZ_UNKNOWN" in data["errors"][0]["message"]

    async def test_import_unknown_vat_percent_goes_to_errors(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Panel", "vat_percent": "99"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert len(data["errors"]) == 1
        assert "99" in data["errors"][0]["message"]

    async def test_import_empty_name_goes_to_errors(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": ""}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["row"] == 0
        assert "name is required" in data["errors"][0]["message"]

    async def test_import_partial_success(self, db_client: AsyncClient) -> None:
        """Mix of valid and invalid rows: good rows created, bad rows in errors."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={
                "rows": [
                    {"name": "Good Row 1"},
                    {"name": "Bad Row", "category_name": "NOPE"},
                    {"name": "Good Row 2", "sku": "GR2"},
                    {"name": "", "unit_code": "ALSO_BAD"},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 2
        assert len(data["errors"]) == 2

        error_rows = {e["row"] for e in data["errors"]}
        assert error_rows == {1, 3}

        list_resp = await db_client.get("/api/v1/products")
        names = [r["name"] for r in list_resp.json()["items"]]
        assert "Good Row 1" in names
        assert "Good Row 2" in names
        assert "Bad Row" not in names

    async def test_import_empty_rows_returns_zero(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 0
        assert data["errors"] == []

    async def test_import_isolation_goes_to_own_company(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Imported products belong to the authenticated user's company."""
        from jai.models.product import Product as ProductModel

        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "IsolatedProduct"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

        # Confirm company_id is set on the created product via direct DB check.
        # (The API intentionally never returns company_id in responses.)
        async with db_session_maker() as session:
            from sqlalchemy import select as sa_select
            result = await session.execute(
                sa_select(ProductModel).where(ProductModel.name == "IsolatedProduct")
            )
            prod = result.scalar_one()
            assert prod.company_id is not None

    async def test_import_case_insensitive_category_match(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        cat = await _create_category(db_client, "MixedCase", default_margin_rate=None)

        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "P1", "category_name": "mixedcase"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["errors"] == []

        prod = (await db_client.get("/api/v1/products")).json()["items"][0]
        assert prod["category_id"] == cat["id"]

    async def test_import_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "X"}]},
        )
        assert resp.status_code == 401

    async def test_import_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "X"}]},
        )
        assert resp.status_code == 400

    async def test_import_upsert_by_sku(self, db_client: AsyncClient) -> None:
        """Re-importing the same SKU updates the existing product, not a duplicate."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {
            "rows": [{"name": "Panel A", "sku": "PA001", "purchase_cost_excl_vat": "100.000"}]
        }
        r1 = await db_client.post("/api/v1/products/import", json=payload)
        assert r1.status_code == 200
        assert r1.json()["created"] == 1
        assert r1.json()["updated"] == 0

        # Re-import with a changed price — should update, not create.
        payload2 = {
            "rows": [{"name": "Panel A v2", "sku": "PA001", "purchase_cost_excl_vat": "120.000"}]
        }
        r2 = await db_client.post("/api/v1/products/import", json=payload2)
        assert r2.status_code == 200
        assert r2.json()["created"] == 0
        assert r2.json()["updated"] == 1

        # Verify no duplicate: only 1 product with this SKU.
        list_r = await db_client.get("/api/v1/products?q=PA001")
        items = list_r.json()["items"]
        pa_items = [p for p in items if p["sku"] == "PA001"]
        assert len(pa_items) == 1
        assert pa_items[0]["name"] == "Panel A v2"

    async def test_import_upsert_partial(self, db_client: AsyncClient) -> None:
        """Mix of new and existing SKUs → correct created/updated counts."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Create one product with a known SKU.
        r1 = await db_client.post(
            "/api/v1/products/import", json={"rows": [{"name": "Existing", "sku": "EX01"}]}
        )
        assert r1.json()["created"] == 1

        # Import: one existing SKU + one new SKU + one without SKU (always creates).
        payload = {
            "rows": [
                {"name": "Existing Updated", "sku": "EX01"},
                {"name": "Brand New", "sku": "NEW01"},
                {"name": "No SKU Product"},
            ]
        }
        r2 = await db_client.post("/api/v1/products/import", json=payload)
        assert r2.status_code == 200
        d = r2.json()
        assert d["created"] == 2   # NEW01 + no-SKU product
        assert d["updated"] == 1   # EX01 updated
        assert d["errors"] == []

    async def test_import_no_sku_always_creates(self, db_client: AsyncClient) -> None:
        """Rows without SKU are always created (no dedup key available)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {"rows": [{"name": "No SKU A"}, {"name": "No SKU B"}]}
        r1 = await db_client.post("/api/v1/products/import", json=payload)
        assert r1.json()["created"] == 2

        # Re-import same names — still creates because no SKU to match on.
        r2 = await db_client.post("/api/v1/products/import", json=payload)
        assert r2.json()["created"] == 2
        assert r2.json()["updated"] == 0

    async def test_import_same_batch_duplicate_sku(self, db_client: AsyncClient) -> None:
        """Two rows with the same new SKU in one batch → 1 created, 1 updated (last wins)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {
            "rows": [
                {"name": "First", "sku": "DEDUP01", "purchase_cost_excl_vat": "10.000"},
                {"name": "Second", "sku": "DEDUP01", "purchase_cost_excl_vat": "20.000"},
            ]
        }
        r = await db_client.post("/api/v1/products/import", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["errors"] == []
        assert d["created"] + d["updated"] == 2

        # Only one product with this SKU should exist.
        list_r = await db_client.get("/api/v1/products?q=DEDUP01")
        items = [p for p in list_r.json()["items"] if p["sku"] == "DEDUP01"]
        assert len(items) == 1
        assert items[0]["name"] == "Second"

    async def test_import_invalid_cost_per_row_error(self, db_client: AsyncClient) -> None:
        """A non-numeric cost on one row goes to errors; the valid row is still created."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {
            "rows": [
                {"name": "Valid Product", "sku": "VALID01"},
                {"name": "Bad Cost", "sku": "BAD01", "purchase_cost_excl_vat": "abc"},
            ]
        }
        r = await db_client.post("/api/v1/products/import", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["created"] == 1
        assert len(d["errors"]) == 1
        assert d["errors"][0]["row"] == 1
        assert "purchase_cost_excl_vat" in d["errors"][0]["message"]

    async def test_import_negative_cost_per_row_error(self, db_client: AsyncClient) -> None:
        """A negative cost goes to errors without failing the whole request."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {"rows": [{"name": "Negative", "purchase_cost_excl_vat": "-5.000"}]}
        r = await db_client.post("/api/v1/products/import", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["created"] == 0
        assert len(d["errors"]) == 1
        assert "purchase_cost_excl_vat" in d["errors"][0]["message"]

    async def test_import_margin_overflow_per_row_error(self, db_client: AsyncClient) -> None:
        """A margin_rate above 99.9999 goes to errors without failing the whole request."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {
            "rows": [
                {"name": "OK", "sku": "OK99"},
                {"name": "Overflow Margin", "margin_rate": "100.0000"},
            ]
        }
        r = await db_client.post("/api/v1/products/import", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["created"] == 1
        assert len(d["errors"]) == 1
        assert "margin_rate" in d["errors"][0]["message"]

    async def test_import_nan_cost_per_row_error(self, db_client: AsyncClient) -> None:
        """NaN cost goes to per-row errors instead of 500."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {
            "rows": [
                {"name": "Valid", "sku": "V-NAN"},
                {"name": "NaN Cost", "purchase_cost_excl_vat": "NaN"},
            ]
        }
        r = await db_client.post("/api/v1/products/import", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["created"] == 1
        assert len(d["errors"]) == 1
        assert "purchase_cost_excl_vat" in d["errors"][0]["message"]

    async def test_import_nan_margin_per_row_error(self, db_client: AsyncClient) -> None:
        """NaN margin_rate goes to per-row errors instead of 500."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {"rows": [{"name": "NaN Margin", "margin_rate": "NaN"}]}
        r = await db_client.post("/api/v1/products/import", json=payload)
        assert r.status_code == 200
        d = r.json()
        assert d["created"] == 0
        assert len(d["errors"]) == 1
        assert "margin_rate" in d["errors"][0]["message"]


# ---------------------------------------------------------------------------
# SKU uniqueness – CRUD endpoints (P1 rework)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductSkuUniqueness:
    """Duplicate SKU via CRUD endpoints must return 409, not 500."""

    async def test_create_duplicate_sku_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        r1 = await db_client.post("/api/v1/products", json={"name": "First", "sku": "DUP-SKU"})
        assert r1.status_code == 201

        r2 = await db_client.post("/api/v1/products", json={"name": "Second", "sku": "DUP-SKU"})
        assert r2.status_code in (409, 422)

    async def test_update_to_duplicate_sku_returns_error(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        r1 = await db_client.post("/api/v1/products", json={"name": "A", "sku": "SKU-A"})
        assert r1.status_code == 201
        r2 = await db_client.post("/api/v1/products", json={"name": "B", "sku": "SKU-B"})
        assert r2.status_code == 201
        product_b_id = r2.json()["id"]

        # Try to update B's SKU to A's SKU
        r3 = await db_client.put(
            f"/api/v1/products/{product_b_id}",
            json={"name": "B", "sku": "SKU-A"},
        )
        assert r3.status_code in (409, 422)


# ---------------------------------------------------------------------------
# Decimal upper-bound validation (P2 fix)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProductDecimalBounds:
    """Overflow inputs must return 422, not 500."""

    async def test_margin_rate_overflow_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # 100.0000 > NUMERIC(6,4) max 99.9999
        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "Overflow", "margin_rate": "100.0000"},
        )
        assert resp.status_code == 422

    async def test_cost_overflow_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # 100 trillion exceeds NUMERIC(14,3) max 99999999999.999
        resp = await db_client.post(
            "/api/v1/products",
            json={"name": "Overflow", "purchase_cost_excl_vat": "100000000000.000"},
        )
        assert resp.status_code == 422

    async def test_category_default_margin_overflow_422(
        self, db_client: AsyncClient
    ) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # 100.0000 > NUMERIC(6,4) max 99.9999
        resp = await db_client.post(
            "/api/v1/product-categories",
            json={"name": "BigMargin", "default_margin_rate": "100.0000"},
        )
        assert resp.status_code == 422
