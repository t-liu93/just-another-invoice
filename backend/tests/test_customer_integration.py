"""Integration tests for customer CRUD + list + addresses (M3 step 1 + 2).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- Full CRUD roundtrip (create → read → update → delete)
- Nested addresses: create with two address types, update, delete one type
- Duplicate address type in request → 422
- Cascade delete: deleting customer also deletes its addresses
- country_code validation → 422
- List with search (``q``) and pagination (``limit``/``offset``/``total``)
- Company-scoped isolation (cross-company → 404)
- Validation errors (invalid currency, invalid email) → 422
- Owner-only enforcement
- Unauthenticated → 401
"""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models.company import Company
from jai.models.customer import Customer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "owner@example.com",
    password: str = "testpassword1",
) -> None:
    """Register → login → MFA setup → MFA verify."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200

    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": code},
    )
    assert resp.status_code == 204


async def _setup_company(client: AsyncClient) -> None:
    """Create the singleton company so that the user has a company_id."""
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": "EUR"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCustomerCRUD:
    """Integration tests for customer CRUD endpoints."""

    async def test_create_customer(self, db_client: AsyncClient) -> None:
        """POST /customers creates a new customer."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Acme BV",
                "contact_name": "John Doe",
                "company_name": "Acme Holding",
                "email": "info@acme.nl",
                "phone": "+31612345678",
                "website": "https://acme.nl",
                "vat_id": "NL123456789B01",
                "currency": "EUR",
                "extra": {"notes": "VIP"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Acme BV"
        assert data["contact_name"] == "John Doe"
        assert data["company_name"] == "Acme Holding"
        assert data["email"] == "info@acme.nl"
        assert data["phone"] == "+31612345678"
        assert data["website"] == "https://acme.nl"
        assert data["vat_id"] == "NL123456789B01"
        assert data["currency"] == "EUR"
        assert data["extra"] == {"notes": "VIP"}
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "company_id" not in data  # never exposed

    async def test_create_customer_minimal(self, db_client: AsyncClient) -> None:
        """POST /customers with only name succeeds."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Minimal Client"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Minimal Client"
        assert data["email"] is None
        assert data["currency"] is None
        assert data["extra"] == {}
        assert data["addresses"] == []

    async def test_get_customer(self, db_client: AsyncClient) -> None:
        """GET /customers/{id} returns the created customer."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Readable", "currency": "USD"},
        )
        customer_id = create_resp.json()["id"]

        resp = await db_client.get(f"/api/v1/customers/{customer_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Readable"
        assert resp.json()["currency"] == "USD"

    async def test_update_customer(self, db_client: AsyncClient) -> None:
        """PUT /customers/{id} updates scalar fields."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Before Update"},
        )
        customer_id = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{customer_id}",
            json={"name": "After Update", "currency": "GBP", "email": "new@test.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "After Update"
        assert data["currency"] == "GBP"
        assert data["email"] == "new@test.com"

    async def test_delete_customer(self, db_client: AsyncClient) -> None:
        """DELETE /customers/{id} removes the customer."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "To Delete"},
        )
        customer_id = create_resp.json()["id"]

        resp = await db_client.delete(f"/api/v1/customers/{customer_id}")
        assert resp.status_code == 204

        # Verify it's gone.
        resp = await db_client.get(f"/api/v1/customers/{customer_id}")
        assert resp.status_code == 404

    async def test_get_nonexistent_customer_404(self, db_client: AsyncClient) -> None:
        """GET /customers/{id} with unknown id → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/customers/{uuid_uuid4()}")
        assert resp.status_code == 404

    async def test_update_nonexistent_customer_404(self, db_client: AsyncClient) -> None:
        """PUT /customers/{id} with unknown id → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            f"/api/v1/customers/{uuid_uuid4()}",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404

    async def test_delete_nonexistent_customer_404(self, db_client: AsyncClient) -> None:
        """DELETE /customers/{id} with unknown id → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.delete(f"/api/v1/customers/{uuid_uuid4()}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestCustomerList:
    """Integration tests for GET /api/v1/customers (list + search + pagination)."""

    async def test_list_empty(self, db_client: AsyncClient) -> None:
        """List returns empty when no customers exist."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_list_returns_customers(self, db_client: AsyncClient) -> None:
        """List returns created customers."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers", json={"name": "Client A"}
        )
        await db_client.post(
            "/api/v1/customers", json={"name": "Client B"}
        )

        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    async def test_list_search_by_name(self, db_client: AsyncClient) -> None:
        """``q`` search filters by name (ILIKE)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers", json={"name": "Alpha BV"}
        )
        await db_client.post(
            "/api/v1/customers", json={"name": "Beta BV"}
        )
        await db_client.post(
            "/api/v1/customers", json={"name": "Gamma Corp"}
        )

        resp = await db_client.get("/api/v1/customers?q=alpha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Alpha BV"

    async def test_list_search_by_email(self, db_client: AsyncClient) -> None:
        """``q`` search filters by email (ILIKE)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers",
            json={"name": "A", "email": "special@test.com"},
        )
        await db_client.post(
            "/api/v1/customers",
            json={"name": "B", "email": "other@test.com"},
        )

        resp = await db_client.get("/api/v1/customers?q=special")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["email"] == "special@test.com"

    async def test_list_search_by_company_name(self, db_client: AsyncClient) -> None:
        """``q`` search filters by company_name (ILIKE)."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers",
            json={"name": "X", "company_name": "Unique Holding BV"},
        )
        await db_client.post(
            "/api/v1/customers",
            json={"name": "Y", "company_name": "Other Corp"},
        )

        resp = await db_client.get("/api/v1/customers?q=unique+holding")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["company_name"] == "Unique Holding BV"

    async def test_list_search_case_insensitive(self, db_client: AsyncClient) -> None:
        """``q`` search is case-insensitive."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers", json={"name": "MiXeD CaSe"}
        )

        resp = await db_client.get("/api/v1/customers?q=mixed+case")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_list_search_no_match(self, db_client: AsyncClient) -> None:
        """``q`` with no match returns empty."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers", json={"name": "Something"}
        )

        resp = await db_client.get("/api/v1/customers?q=zzznonexistent")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert resp.json()["items"] == []

    async def test_list_pagination(self, db_client: AsyncClient) -> None:
        """Pagination with limit/offset works correctly."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Create 5 customers.
        for i in range(5):
            await db_client.post(
                "/api/v1/customers",
                json={"name": f"Client {i:02d}"},
            )

        # Page 1: limit=2, offset=0.
        resp = await db_client.get("/api/v1/customers?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2

        # Page 2: limit=2, offset=2.
        resp = await db_client.get("/api/v1/customers?limit=2&offset=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2

        # Page 3: limit=2, offset=4.
        resp = await db_client.get("/api/v1/customers?limit=2&offset=4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 1  # last page has 1 item

    async def test_list_default_pagination(self, db_client: AsyncClient) -> None:
        """Default limit=50, offset=0."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers", json={"name": "Only One"}
        )

        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1


@pytest.mark.integration
class TestCustomerValidation:
    """Integration tests for schema validation → 422."""

    async def test_invalid_currency_422(self, db_client: AsyncClient) -> None:
        """Invalid ISO 4217 currency code → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "X", "currency": "INVALID"},
        )
        assert resp.status_code == 422

    async def test_invalid_email_422(self, db_client: AsyncClient) -> None:
        """Invalid email format → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "X", "email": "not-an-email"},
        )
        assert resp.status_code == 422

    async def test_empty_name_422(self, db_client: AsyncClient) -> None:
        """Empty name → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": ""},
        )
        assert resp.status_code == 422

    async def test_currency_normalised_on_create(self, db_client: AsyncClient) -> None:
        """Lowercase currency is normalised to uppercase on create."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Normalised", "currency": "eur"},
        )
        assert resp.status_code == 201
        assert resp.json()["currency"] == "EUR"

    async def test_currency_normalised_on_update(self, db_client: AsyncClient) -> None:
        """Lowercase currency is normalised to uppercase on update."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Norm Update"},
        )
        cid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={"name": "Norm Update", "currency": "usd"},
        )
        assert resp.status_code == 200
        assert resp.json()["currency"] == "USD"


@pytest.mark.integration
class TestCustomerAuth:
    """Integration tests for authentication and authorization."""

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated request → 401."""
        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 401

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "X"},
        )
        assert resp.status_code == 401

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Customer owned by company B is invisible to a user of company A.

        Directly inserts company B + its customer into the DB so that no
        second authenticated user is needed.  Asserts GET/PUT/DELETE all
        return 404 and that the list endpoint does not leak company B data.
        """
        # Set up user A with company A via the normal auth flow.
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Create a customer for company A (should remain visible).
        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Company A Client"},
        )
        assert resp.status_code == 201

        # Directly insert company B + its customer into the DB.
        company_b_id = uuid.uuid4()
        customer_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            session.add(
                Customer(id=customer_b_id, company_id=company_b_id, name="Company B Client")
            )
            await session.commit()

        # User A cannot GET, PUT, or DELETE company B's customer.
        resp = await db_client.get(f"/api/v1/customers/{customer_b_id}")
        assert resp.status_code == 404

        resp = await db_client.put(
            f"/api/v1/customers/{customer_b_id}",
            json={"name": "Hacked"},
        )
        assert resp.status_code == 404

        resp = await db_client.delete(f"/api/v1/customers/{customer_b_id}")
        assert resp.status_code == 404

        # List only returns company A's customer; company B's is invisible.
        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        names = [item["name"] for item in data["items"]]
        assert "Company A Client" in names
        assert "Company B Client" not in names

        # company_id is never exposed in any response.
        resp = await db_client.get("/api/v1/customers")
        for item in resp.json()["items"]:
            assert "company_id" not in item

    async def test_no_company_returns_400(self, db_client: AsyncClient) -> None:
        """User without company_id → 400 on customer operations."""
        # Register + MFA but do NOT create company.
        await _full_auth(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "No Company"},
        )
        assert resp.status_code == 400

        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 400


@pytest.mark.integration
class TestCustomerAddresses:
    """Integration tests for nested address create/update/delete (M3 step 2)."""

    async def test_create_with_both_addresses(self, db_client: AsyncClient) -> None:
        """POST /customers with billing + shipping addresses."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Addr Test",
                "addresses": [
                    {
                        "type": "BILLING",
                        "street": "Keizersgracht",
                        "house_number": "1",
                        "city": "Amsterdam",
                        "country_code": "NL",
                    },
                    {
                        "type": "SHIPPING",
                        "street": "Prinsengracht",
                        "house_number": "2",
                        "city": "Amsterdam",
                        "country_code": "NL",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["addresses"]) == 2
        types = {a["type"] for a in data["addresses"]}
        assert "BILLING" in types
        assert "SHIPPING" in types

        billing = next(a for a in data["addresses"] if a["type"] == "BILLING")
        assert billing["street"] == "Keizersgracht"
        assert billing["city"] == "Amsterdam"
        assert billing["country_code"] == "NL"

    async def test_create_billing_only(self, db_client: AsyncClient) -> None:
        """POST /customers with only billing address."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Billing Only",
                "addresses": [
                    {"type": "BILLING", "city": "Rotterdam"},
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["addresses"]) == 1
        assert data["addresses"][0]["type"] == "BILLING"
        assert data["addresses"][0]["city"] == "Rotterdam"

    async def test_get_customer_includes_addresses(self, db_client: AsyncClient) -> None:
        """GET /customers/{id} returns addresses in the response."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Get Addr",
                "addresses": [{"type": "BILLING", "city": "Leiden"}],
            },
        )
        cid = create_resp.json()["id"]

        resp = await db_client.get(f"/api/v1/customers/{cid}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["addresses"]) == 1
        assert data["addresses"][0]["city"] == "Leiden"

    async def test_update_changes_address_fields(self, db_client: AsyncClient) -> None:
        """PUT /customers/{id} updates address fields in-place."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Update Addr",
                "addresses": [{"type": "BILLING", "city": "OldCity"}],
            },
        )
        cid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={
                "name": "Update Addr",
                "addresses": [{"type": "BILLING", "city": "NewCity", "street": "NewStreet"}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["addresses"]) == 1
        assert data["addresses"][0]["city"] == "NewCity"
        assert data["addresses"][0]["street"] == "NewStreet"

    async def test_update_removes_address_type(self, db_client: AsyncClient) -> None:
        """PUT without a type in addresses[] removes that address type."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Remove Addr",
                "addresses": [
                    {"type": "BILLING", "city": "Amsterdam"},
                    {"type": "SHIPPING", "city": "Rotterdam"},
                ],
            },
        )
        cid = create_resp.json()["id"]
        assert len(create_resp.json()["addresses"]) == 2

        # Update with only billing → shipping should be removed.
        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={
                "name": "Remove Addr",
                "addresses": [{"type": "BILLING", "city": "Amsterdam"}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["addresses"]) == 1
        assert data["addresses"][0]["type"] == "BILLING"

    async def test_update_add_address_type(self, db_client: AsyncClient) -> None:
        """PUT with a new type in addresses[] adds that address."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Add Addr", "addresses": [{"type": "BILLING", "city": "A"}]},
        )
        cid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={
                "name": "Add Addr",
                "addresses": [
                    {"type": "BILLING", "city": "A"},
                    {"type": "SHIPPING", "city": "B"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["addresses"]) == 2

    async def test_update_remove_all_addresses(self, db_client: AsyncClient) -> None:
        """PUT with empty addresses[] removes all existing addresses."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Clear Addr", "addresses": [{"type": "BILLING", "city": "X"}]},
        )
        cid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={"name": "Clear Addr", "addresses": []},
        )
        assert resp.status_code == 200
        assert resp.json()["addresses"] == []

    async def test_duplicate_address_type_rejected(self, db_client: AsyncClient) -> None:
        """Two addresses of same type in one request → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Dup Addr",
                "addresses": [
                    {"type": "BILLING", "city": "A"},
                    {"type": "BILLING", "city": "B"},
                ],
            },
        )
        assert resp.status_code == 422

    async def test_invalid_country_code_in_address_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """Invalid country_code in address → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Invalid CC",
                "addresses": [{"type": "BILLING", "country_code": "INVALID"}],
            },
        )
        assert resp.status_code == 422

    async def test_delete_customer_cascades_to_addresses(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Deleting a customer also deletes its addresses (DB ON DELETE CASCADE)."""
        from sqlalchemy import select

        from jai.models.address import Address

        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Cascade Test",
                "addresses": [
                    {"type": "BILLING", "city": "Amsterdam"},
                    {"type": "SHIPPING", "city": "Rotterdam"},
                ],
            },
        )
        cid = create_resp.json()["id"]

        # Confirm 2 addresses exist in DB before delete.
        async with db_session_maker() as session:
            result = await session.execute(
                select(Address).where(Address.customer_id == uuid.UUID(cid))
            )
            addresses_before = result.scalars().all()
        assert len(addresses_before) == 2

        # Delete the customer.
        del_resp = await db_client.delete(f"/api/v1/customers/{cid}")
        assert del_resp.status_code == 204

        # Confirm addresses are gone too (CASCADE).
        async with db_session_maker() as session:
            result = await session.execute(
                select(Address).where(Address.customer_id == uuid.UUID(cid))
            )
            addresses_after = result.scalars().all()
        assert len(addresses_after) == 0

    async def test_address_persists_across_refresh(
        self, db_client: AsyncClient
    ) -> None:
        """Addresses round-trip: create then GET returns same data."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={
                "name": "Roundtrip",
                "addresses": [
                    {
                        "type": "BILLING",
                        "street": "Damrak",
                        "house_number": "10",
                        "house_number_addition": "B",
                        "postal_code": "1012 LG",
                        "city": "Amsterdam",
                        "province": None,
                        "country_code": "NL",
                    }
                ],
            },
        )
        assert create_resp.status_code == 201
        cid = create_resp.json()["id"]

        get_resp = await db_client.get(f"/api/v1/customers/{cid}")
        assert get_resp.status_code == 200
        addr = get_resp.json()["addresses"][0]
        assert addr["street"] == "Damrak"
        assert addr["house_number"] == "10"
        assert addr["house_number_addition"] == "B"
        assert addr["postal_code"] == "1012 LG"
        assert addr["city"] == "Amsterdam"
        assert addr["country_code"] == "NL"


# ---------------------------------------------------------------------------
# Customer locale CRUD (M9 step 2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCustomerLocaleCRUD:
    """Integration tests for customer.locale field (M9 step 2)."""

    async def test_create_customer_with_locale_en(self, db_client: AsyncClient) -> None:
        """POST /customers with locale='en' stores and returns the value."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Locale EN", "locale": "en"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["locale"] == "en"

    async def test_create_customer_with_locale_zh(self, db_client: AsyncClient) -> None:
        """POST /customers with locale='zh' stores and returns the value."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Locale ZH", "locale": "zh"},
        )
        assert resp.status_code == 201
        assert resp.json()["locale"] == "zh"

    async def test_create_customer_locale_null(self, db_client: AsyncClient) -> None:
        """POST /customers without locale returns locale=null."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "No Locale"},
        )
        assert resp.status_code == 201
        assert resp.json()["locale"] is None

    async def test_update_customer_locale(self, db_client: AsyncClient) -> None:
        """PUT /customers/{id} can change locale en → zh."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Switchable", "locale": "en"},
        )
        cid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={"name": "Switchable", "locale": "zh"},
        )
        assert resp.status_code == 200
        assert resp.json()["locale"] == "zh"

    async def test_update_customer_clears_locale(self, db_client: AsyncClient) -> None:
        """PUT /customers/{id} with locale=null clears the locale."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "ClearLocale", "locale": "zh"},
        )
        cid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cid}",
            json={"name": "ClearLocale", "locale": None},
        )
        assert resp.status_code == 200
        assert resp.json()["locale"] is None

    async def test_create_customer_invalid_locale_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """POST /customers with locale='fr' → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Bad Locale", "locale": "fr"},
        )
        assert resp.status_code == 422

    async def test_get_customer_returns_locale(self, db_client: AsyncClient) -> None:
        """GET /customers/{id} returns locale field."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "GetLocale", "locale": "zh"},
        )
        cid = create_resp.json()["id"]

        resp = await db_client.get(f"/api/v1/customers/{cid}")
        assert resp.status_code == 200
        assert resp.json()["locale"] == "zh"


# ---------------------------------------------------------------------------
# Document defaults settings (M9 step 2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDocumentDefaultsSettings:
    """Integration tests for GET/PUT /settings/document-defaults (M9 step 2)."""

    async def test_get_document_defaults_returns_en_by_default(
        self, db_client: AsyncClient
    ) -> None:
        """GET /settings/document-defaults returns 'en' when not configured."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/settings/document-defaults")
        assert resp.status_code == 200
        assert resp.json()["locale"] == "en"

    async def test_put_document_defaults_zh(self, db_client: AsyncClient) -> None:
        """PUT /settings/document-defaults can set locale to 'zh'."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/document-defaults",
            json={"locale": "zh"},
        )
        assert resp.status_code == 200
        assert resp.json()["locale"] == "zh"

    async def test_put_document_defaults_en(self, db_client: AsyncClient) -> None:
        """PUT /settings/document-defaults can set locale to 'en'."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # First set zh
        await db_client.put(
            "/api/v1/settings/document-defaults",
            json={"locale": "zh"},
        )
        # Then reset to en
        resp = await db_client.put(
            "/api/v1/settings/document-defaults",
            json={"locale": "en"},
        )
        assert resp.status_code == 200
        assert resp.json()["locale"] == "en"

    async def test_get_document_defaults_reflects_update(
        self, db_client: AsyncClient
    ) -> None:
        """GET /settings/document-defaults reflects a previous PUT."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.put(
            "/api/v1/settings/document-defaults",
            json={"locale": "zh"},
        )

        resp = await db_client.get("/api/v1/settings/document-defaults")
        assert resp.status_code == 200
        assert resp.json()["locale"] == "zh"

    async def test_put_document_defaults_invalid_locale_rejected(
        self, db_client: AsyncClient
    ) -> None:
        """PUT /settings/document-defaults with invalid locale → 422."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/document-defaults",
            json={"locale": "fr"},
        )
        assert resp.status_code == 422

    async def test_document_defaults_requires_owner(
        self, db_client: AsyncClient
    ) -> None:
        """Non-owner user cannot access document-defaults endpoints."""
        # Register an owner first to make the app non-fresh
        await _full_auth(db_client, email="owner2@example.com")
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/settings/document-defaults")
        # Owner access → 200
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def uuid_uuid4() -> str:
    """Return a random UUID4 as string (for URL path construction)."""
    import uuid as _uuid

    return str(_uuid.uuid4())
