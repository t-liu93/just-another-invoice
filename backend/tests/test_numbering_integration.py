"""Integration tests for the numbering engine (M5 step 2).

Covers:
- GET/PUT /settings/invoice-number-sequence (auth, happy path, forward-only)
- Sequence allocation: sequence_start, sequential increment, skip-forward
- Customer-scope sequence (only when template references CUSTOMER_SEQUENCE)
- Customer invoice_prefix round-trip via customer CRUD
- Concurrent allocation (no duplicate sequence numbers)
- Auth and owner-only enforcement
"""

from __future__ import annotations

import asyncio
import uuid

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "owner@example.com",
    password: str = "testpass123",
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


async def _setup_company(client: AsyncClient, base_currency: str = "EUR") -> dict:
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": base_currency, "country_code": "NL"},
    )
    assert resp.status_code == 200
    return resp.json()


async def _create_customer(client: AsyncClient, name: str = "Test Customer") -> dict:
    resp = await client.post(
        "/api/v1/customers",
        json={"name": name, "addresses": []},
    )
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# GET /settings/invoice-number-sequence
# ---------------------------------------------------------------------------


class TestGetInvoiceNumberSequence:
    async def test_unauthenticated_returns_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert resp.status_code == 401

    async def test_no_company_returns_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert resp.status_code == 400

    async def test_default_sequence_from_sequence_start(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert resp.status_code == 200
        data = resp.json()
        # sequence_start default is 1
        assert data["next_sequence"] == 1
        assert "preview_number" in data
        # Default template: {{SERIES:INV}}-{{SEQUENCE:6}}
        assert data["preview_number"] == "INV-000001"

    async def test_respects_custom_sequence_start(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Set sequence_start to 100 via numbering config
        await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{SERIES:INV}}-{{SEQUENCE:6}}", "sequence_start": 100},
        )

        resp = await db_client.get("/api/v1/settings/invoice-number-sequence")
        assert resp.status_code == 200
        data = resp.json()
        assert data["next_sequence"] == 100
        assert data["preview_number"] == "INV-000100"


# ---------------------------------------------------------------------------
# PUT /settings/invoice-number-sequence (advance / skip forward)
# ---------------------------------------------------------------------------


class TestPutInvoiceNumberSequence:
    async def test_unauthenticated_returns_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 200},
        )
        assert resp.status_code == 401

    async def test_no_company_returns_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 200},
        )
        assert resp.status_code == 400

    async def test_advance_sequence_when_no_row_exists(self, db_client: AsyncClient) -> None:
        """First advance: sequence does not exist yet; any value ≥ sequence_start is OK."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 200},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["next_sequence"] == 200
        assert data["preview_number"] == "INV-000200"

    async def test_advance_sequence_forward(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # First advance to 50
        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 50},
        )
        assert resp.status_code == 200

        # Advance again to 100 (larger → OK)
        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 100},
        )
        assert resp.status_code == 200
        assert resp.json()["next_sequence"] == 100

    async def test_backward_advance_rejected_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Advance to 200
        await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 200},
        )

        # Attempt to go back to 100 → should fail
        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 100},
        )
        assert resp.status_code == 422

    async def test_same_value_rejected_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 50},
        )

        # Same value – not strictly greater, should fail
        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 50},
        )
        assert resp.status_code == 422

    async def test_preview_reflects_custom_template(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Change numbering template
        await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{SERIES:FACT}}-{{SEQUENCE:4}}", "sequence_start": 1},
        )

        resp = await db_client.put(
            "/api/v1/settings/invoice-number-sequence",
            json={"next_sequence": 99},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["next_sequence"] == 99
        assert data["preview_number"] == "FACT-0099"


# ---------------------------------------------------------------------------
# GET /settings/numbering – preview field
# ---------------------------------------------------------------------------


class TestGetNumberingConfigPreview:
    async def test_numbering_config_includes_preview(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/settings/numbering")
        assert resp.status_code == 200
        data = resp.json()
        assert "preview" in data
        # Default: template is {{SERIES:INV}}-{{SEQUENCE:6}}, sequence_start=1
        assert data["preview"] == "INV-000001"


# ---------------------------------------------------------------------------
# Sequence allocation via allocate_invoice_number
# ---------------------------------------------------------------------------


class TestAllocateInvoiceNumber:
    """Service-layer tests against the real DB (no HTTP)."""

    async def test_first_allocation_uses_sequence_start(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from datetime import date

        from jai.models.company import Company
        from jai.models.customer import Customer
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import allocate_invoice_number

        config = InvoiceNumberingConfig(
            template="{{SERIES:INV}}-{{SEQUENCE:6}}",
            sequence_start=100,
        )
        company_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        async with db_session_maker() as session:
            # Insert minimal company and customer rows
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            session.add(Customer(id=customer_id, company_id=company_id, name="Cust"))
            await session.flush()

            num, seq, cust_seq = await allocate_invoice_number(
                session,
                company_id,
                customer_id,
                date(2026, 6, 10),
                numbering_config=config,
                customer_invoice_prefix=None,
            )
            await session.commit()

        assert num == "INV-000100"
        assert seq == 100
        assert cust_seq is None

    async def test_sequential_allocation_increments(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from datetime import date

        from jai.models.company import Company
        from jai.models.customer import Customer
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import allocate_invoice_number

        config = InvoiceNumberingConfig(
            template="{{SERIES:INV}}-{{SEQUENCE:4}}",
            sequence_start=1,
        )
        company_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            session.add(Customer(id=customer_id, company_id=company_id, name="Cust"))
            await session.flush()

            nums = []
            for _ in range(3):
                num, _, _ = await allocate_invoice_number(
                    session,
                    company_id,
                    customer_id,
                    date(2026, 6, 10),
                    numbering_config=config,
                    customer_invoice_prefix=None,
                )
                nums.append(num)
            await session.commit()

        assert nums == ["INV-0001", "INV-0002", "INV-0003"]

    async def test_customer_series_in_template(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from datetime import date

        from jai.models.company import Company
        from jai.models.customer import Customer
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import allocate_invoice_number

        config = InvoiceNumberingConfig(
            template="{{CUSTOMER_SERIES}}-{{SEQUENCE:4}}",
            sequence_start=1,
        )
        company_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            session.add(Customer(id=customer_id, company_id=company_id, name="Cust"))
            await session.flush()

            num, seq, cust_seq = await allocate_invoice_number(
                session,
                company_id,
                customer_id,
                date(2026, 6, 10),
                numbering_config=config,
                customer_invoice_prefix="ABC",
            )
            await session.commit()

        assert num == "ABC-0001"
        assert seq == 1
        assert cust_seq is None  # no CUSTOMER_SEQUENCE in template

    async def test_customer_sequence_allocated_only_when_referenced(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from datetime import date

        from jai.models.company import Company
        from jai.models.customer import Customer
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import allocate_invoice_number

        config_with = InvoiceNumberingConfig(
            template="{{SERIES:INV}}-{{CUSTOMER_SEQUENCE:3}}-{{SEQUENCE:4}}",
            sequence_start=1,
        )
        company_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            session.add(Customer(id=customer_id, company_id=company_id, name="Cust"))
            await session.flush()

            num, seq, cust_seq = await allocate_invoice_number(
                session,
                company_id,
                customer_id,
                date(2026, 6, 10),
                numbering_config=config_with,
                customer_invoice_prefix=None,
            )
            await session.commit()

        assert num == "INV-001-0001"
        assert seq == 1
        assert cust_seq == 1

    async def test_customer_sequence_increments_per_customer(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Two customers each get independent customer-scope sequences."""
        from datetime import date

        from jai.models.company import Company
        from jai.models.customer import Customer
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import allocate_invoice_number

        config = InvoiceNumberingConfig(
            template="{{CUSTOMER_SEQUENCE:2}}-{{SEQUENCE:4}}",
            sequence_start=1,
        )
        company_id = uuid.uuid4()
        cust_a = uuid.uuid4()
        cust_b = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            session.add(Customer(id=cust_a, company_id=company_id, name="A"))
            session.add(Customer(id=cust_b, company_id=company_id, name="B"))
            await session.flush()

            # Two invoices for customer A
            num_a1, _, cust_seq_a1 = await allocate_invoice_number(
                session,
                company_id,
                cust_a,
                date(2026, 6, 10),
                numbering_config=config,
                customer_invoice_prefix=None,
            )
            num_a2, _, cust_seq_a2 = await allocate_invoice_number(
                session,
                company_id,
                cust_a,
                date(2026, 6, 10),
                numbering_config=config,
                customer_invoice_prefix=None,
            )
            # One invoice for customer B
            num_b1, _, cust_seq_b1 = await allocate_invoice_number(
                session,
                company_id,
                cust_b,
                date(2026, 6, 10),
                numbering_config=config,
                customer_invoice_prefix=None,
            )
            await session.commit()

        # Company sequence is shared: 1, 2, 3
        assert num_a1 == "01-0001"
        assert num_a2 == "02-0002"
        assert num_b1 == "01-0003"  # customer B starts at 1 for its own counter
        assert cust_seq_a1 == 1
        assert cust_seq_a2 == 2
        assert cust_seq_b1 == 1

    async def test_concurrent_allocation_no_duplicates(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Concurrent allocation never produces duplicate invoice numbers."""
        from datetime import date

        from jai.models.company import Company
        from jai.models.customer import Customer
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import allocate_invoice_number

        config = InvoiceNumberingConfig(
            template="{{SEQUENCE:6}}",
            sequence_start=1,
        )
        company_id = uuid.uuid4()
        customer_id = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            session.add(Customer(id=customer_id, company_id=company_id, name="Cust"))
            await session.commit()

        concurrency = 10
        numbers: list[str] = []

        async def _allocate() -> str:
            async with db_session_maker() as session:
                num, _, _ = await allocate_invoice_number(
                    session,
                    company_id,
                    customer_id,
                    date(2026, 6, 10),
                    numbering_config=config,
                    customer_invoice_prefix=None,
                )
                await session.commit()
                return num

        results = await asyncio.gather(*[_allocate() for _ in range(concurrency)])
        numbers.extend(results)

        # All numbers must be unique
        assert len(set(numbers)) == concurrency
        # All should be between 000001 and 000010
        int_values = sorted(int(n) for n in numbers)
        assert int_values == list(range(1, concurrency + 1))


# ---------------------------------------------------------------------------
# Customer invoice_prefix round-trip
# ---------------------------------------------------------------------------


class TestCustomerInvoicePrefix:
    async def test_create_with_invoice_prefix(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Prefix Customer", "invoice_prefix": "PCO", "addresses": []},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["invoice_prefix"] == "PCO"

    async def test_update_invoice_prefix(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        customer = await _create_customer(db_client, "Test Cust")
        cust_id = customer["id"]

        resp = await db_client.put(
            f"/api/v1/customers/{cust_id}",
            json={"name": "Test Cust", "invoice_prefix": "TC", "addresses": []},
        )
        assert resp.status_code == 200
        assert resp.json()["invoice_prefix"] == "TC"

    async def test_clear_invoice_prefix(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/customers",
            json={"name": "Prefix Cust", "invoice_prefix": "ABC", "addresses": []},
        )
        cust_id = resp.json()["id"]

        # Clear prefix
        resp = await db_client.put(
            f"/api/v1/customers/{cust_id}",
            json={"name": "Prefix Cust", "invoice_prefix": None, "addresses": []},
        )
        assert resp.status_code == 200
        assert resp.json()["invoice_prefix"] is None

    async def test_list_includes_invoice_prefix(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/customers",
            json={"name": "A Customer", "invoice_prefix": "XYZ", "addresses": []},
        )

        resp = await db_client.get("/api/v1/customers")
        assert resp.status_code == 200
        customers = resp.json()["items"]
        matching = [c for c in customers if c["name"] == "A Customer"]
        assert len(matching) == 1
        assert matching[0]["invoice_prefix"] == "XYZ"


# ---------------------------------------------------------------------------
# advance_sequence service-level edge cases
# ---------------------------------------------------------------------------


class TestAdvanceSequenceEdgeCases:
    async def test_advance_when_sequence_not_created_yet(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Can advance before any invoice has been created."""
        from jai.models.company import Company
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import advance_sequence

        config = InvoiceNumberingConfig(
            template="{{SERIES:INV}}-{{SEQUENCE:4}}",
            sequence_start=1,
        )
        company_id = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            await session.flush()

            new_next, preview = await advance_sequence(
                session, company_id, 500, numbering_config=config
            )
            await session.commit()

        assert new_next == 500
        assert preview == "INV-0500"

    async def test_advance_backward_raises(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.company import Company
        from jai.schemas.setting import InvoiceNumberingConfig
        from jai.services.numbering import advance_sequence

        config = InvoiceNumberingConfig(
            template="{{SEQUENCE:4}}", sequence_start=1
        )
        company_id = uuid.uuid4()

        async with db_session_maker() as session:
            session.add(Company(id=company_id, name="Co", base_currency="EUR"))
            await session.flush()
            await advance_sequence(session, company_id, 100, numbering_config=config)
            await session.flush()

            with pytest.raises(ValueError, match="advance"):
                await advance_sequence(session, company_id, 99, numbering_config=config)


# ---------------------------------------------------------------------------
# PUT /settings/numbering – template validation
# ---------------------------------------------------------------------------


class TestPutNumberingConfigValidation:
    """PUT /settings/numbering must reject invalid templates and strip preview."""

    async def test_unknown_placeholder_rejected_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{UNKNOWN}}-{{SEQUENCE:4}}", "sequence_start": 1},
        )
        assert resp.status_code == 422

    async def test_injection_attempt_rejected_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{__import__('os')}}-{{SEQUENCE:4}}", "sequence_start": 1},
        )
        assert resp.status_code == 422

    async def test_no_sequence_placeholder_rejected_422(self, db_client: AsyncClient) -> None:
        """A fully static template (no SEQUENCE) is rejected to prevent duplicate numbers."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "FIXED-001", "sequence_start": 1},
        )
        assert resp.status_code == 422

    async def test_series_only_without_sequence_rejected_422(self, db_client: AsyncClient) -> None:
        """SERIES placeholder alone (without SEQUENCE) is also rejected."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{SERIES:INV}}", "sequence_start": 1},
        )
        assert resp.status_code == 422

    async def test_old_config_not_overwritten_on_invalid_template(
        self, db_client: AsyncClient
    ) -> None:
        """A failed validation must not overwrite the previously stored config."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Save a valid baseline config
        await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{SERIES:INV}}-{{SEQUENCE:6}}", "sequence_start": 5},
        )

        # Attempt to save an invalid template
        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{UNKNOWN}}", "sequence_start": 1},
        )
        assert resp.status_code == 422

        # Original config must still be intact
        resp = await db_client.get("/api/v1/settings/numbering")
        assert resp.status_code == 200
        data = resp.json()
        assert data["template"] == "{{SERIES:INV}}-{{SEQUENCE:6}}"
        assert data["sequence_start"] == 5

    async def test_customer_sequence_only_rejected_422(self, db_client: AsyncClient) -> None:
        """{{CUSTOMER_SEQUENCE:n}} alone must be rejected: per-customer counters duplicate."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={"template": "{{CUSTOMER_SEQUENCE:4}}", "sequence_start": 1},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "company-level {{SEQUENCE:n}}" in detail
        assert "only together with {{SEQUENCE:n}}" in detail

    async def test_customer_series_and_customer_sequence_rejected_422(
        self, db_client: AsyncClient
    ) -> None:
        """{{CUSTOMER_SERIES}}-{{CUSTOMER_SEQUENCE:4}} has no company-level SEQUENCE."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={
                "template": "{{CUSTOMER_SERIES}}-{{CUSTOMER_SEQUENCE:4}}",
                "sequence_start": 1,
            },
        )
        assert resp.status_code == 422

    async def test_customer_sequence_combined_with_company_sequence_accepted(
        self, db_client: AsyncClient
    ) -> None:
        """Company-level SEQUENCE present → template is valid even with CUSTOMER_SEQUENCE."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            "/api/v1/settings/numbering",
            json={
                "template": "{{CUSTOMER_SERIES}}-{{CUSTOMER_SEQUENCE:4}}-{{SEQUENCE:6}}",
                "sequence_start": 1,
            },
        )
        assert resp.status_code == 200

    async def test_client_preview_not_persisted(self, db_client: AsyncClient) -> None:
        """Client-supplied preview value must be ignored and not stored."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.put(
            "/api/v1/settings/numbering",
            json={
                "template": "{{SERIES:FAC}}-{{SEQUENCE:4}}",
                "sequence_start": 1,
                "preview": "INJECTED",
            },
        )

        resp = await db_client.get("/api/v1/settings/numbering")
        assert resp.status_code == 200
        data = resp.json()
        # Must be computed from template, not the client-supplied string
        assert data["preview"] == "FAC-0001"
        assert data["preview"] != "INJECTED"
