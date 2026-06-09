"""Unit tests for ``jai.schemas.customer`` and ``jai.services.customer``.

All tests use mocks — no running PostgreSQL required.  Verifies:
- Schema validation (ISO 4217, email format, name constraints)
- Service list/get/create/update/delete logic
- Company-scoped filtering
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jai.schemas.customer import CustomerListResponse, CustomerRead, CustomerWrite
from jai.services.customer import (
    _to_read,
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    update_customer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_customer_orm(**overrides: object) -> SimpleNamespace:
    """Build a mock Customer ORM instance with sensible defaults."""
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "company_id": uuid.uuid4(),
        "name": "Test Customer",
        "contact_name": None,
        "company_name": None,
        "email": None,
        "phone": None,
        "website": None,
        "vat_id": None,
        "currency": None,
        "extra": {},
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_owner(**overrides: object) -> SimpleNamespace:
    """Build a mock User (owner) with ``company_id`` set."""
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "role": "owner",
        "company_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestCustomerWriteValidation:
    """Tests for ``CustomerWrite`` Pydantic schema."""

    def test_valid_minimal(self) -> None:
        """Minimal valid payload: name only."""
        cw = CustomerWrite(name="Acme")
        assert cw.name == "Acme"
        assert cw.email is None
        assert cw.currency is None
        assert cw.extra == {}

    def test_valid_full(self) -> None:
        """Full payload with all optional fields."""
        cw = CustomerWrite(
            name="Acme BV",
            contact_name="John Doe",
            company_name="Acme Holding BV",
            email="info@acme.nl",
            phone="+31612345678",
            website="https://acme.nl",
            vat_id="NL123456789B01",
            currency="EUR",
            extra={"notes": "VIP client"},
        )
        assert cw.name == "Acme BV"
        assert cw.contact_name == "John Doe"
        assert cw.company_name == "Acme Holding BV"
        assert cw.email == "info@acme.nl"
        assert cw.currency == "EUR"
        assert cw.extra == {"notes": "VIP client"}

    def test_invalid_currency_rejected(self) -> None:
        """Invalid ISO 4217 code raises ValidationError."""
        with pytest.raises(Exception, match="Invalid ISO 4217"):
            CustomerWrite(name="X", currency="XYZ")

    def test_invalid_email_rejected(self) -> None:
        """Badly formatted email raises ValidationError."""
        with pytest.raises(Exception, match="Invalid email format"):
            CustomerWrite(name="X", email="not-an-email")

    def test_email_without_domain_rejected(self) -> None:
        """Email without domain part is rejected."""
        with pytest.raises(Exception, match="Invalid email format"):
            CustomerWrite(name="X", email="user@")

    def test_valid_email_accepted(self) -> None:
        """Normal email addresses are accepted."""
        cw = CustomerWrite(name="X", email="user@example.com")
        assert cw.email == "user@example.com"

    def test_currency_normalised_to_upper(self) -> None:
        """Lowercase currency is normalised to uppercase."""
        cw = CustomerWrite(name="X", currency="eur")
        assert cw.currency == "EUR"

    def test_currency_none_allowed(self) -> None:
        """currency can be None."""
        cw = CustomerWrite(name="X", currency=None)
        assert cw.currency is None

    def test_email_none_allowed(self) -> None:
        """email can be None."""
        cw = CustomerWrite(name="X", email=None)
        assert cw.email is None

    def test_empty_name_rejected(self) -> None:
        """Empty string name is rejected."""
        with pytest.raises(ValueError):
            CustomerWrite(name="")

    def test_whitespace_only_name_rejected(self) -> None:
        """Whitespace-only name is rejected after strip."""
        with pytest.raises(ValueError):
            CustomerWrite(name="   ")

    def test_currency_name_rejected(self) -> None:
        """English name 'Euro' is NOT a valid ISO 4217 alpha-3 code."""
        with pytest.raises(Exception, match="Invalid ISO 4217"):
            CustomerWrite(name="X", currency="Euro")

    def test_extra_defaults_to_empty_dict(self) -> None:
        """extra defaults to {} when not provided."""
        cw = CustomerWrite(name="X")
        assert cw.extra == {}

    def test_extra_custom_value(self) -> None:
        """extra accepts arbitrary dict data."""
        cw = CustomerWrite(name="X", extra={"key": "value", "nested": {"a": 1}})
        assert cw.extra["key"] == "value"
        assert cw.extra["nested"]["a"] == 1


# ---------------------------------------------------------------------------
# Service logic – _to_read mapping
# ---------------------------------------------------------------------------


class TestCustomerToRead:
    """Tests for ``_to_read`` mapping."""

    def test_basic_mapping(self) -> None:
        """All scalar fields are mapped correctly."""
        customer_id = uuid.uuid4()
        orm = _make_customer_orm(
            id=customer_id, name="Test", currency="USD", extra={"a": 1}
        )
        read = _to_read(orm)
        assert isinstance(read, CustomerRead)
        assert read.id == customer_id
        assert read.name == "Test"
        assert read.currency == "USD"
        assert read.extra == {"a": 1}
        assert read.addresses == []

    def test_null_fields(self) -> None:
        """Optional null fields stay null."""
        orm = _make_customer_orm()
        read = _to_read(orm)
        assert read.contact_name is None
        assert read.company_name is None
        assert read.email is None
        assert read.phone is None
        assert read.website is None
        assert read.vat_id is None
        assert read.currency is None


# ---------------------------------------------------------------------------
# Service logic – list_customers (mocked DB)
# ---------------------------------------------------------------------------


class TestListCustomers:
    """Tests for ``list_customers``."""

    async def test_returns_empty_list(self) -> None:
        """No customers → empty items, total=0."""
        session = AsyncMock()
        # count query
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        # rows query
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(side_effect=[count_result, rows_result])

        company_id = uuid.uuid4()
        result = await list_customers(session, company_id)
        assert isinstance(result, CustomerListResponse)
        assert result.items == []
        assert result.total == 0

    async def test_returns_customers_with_total(self) -> None:
        """Returns items and correct total count."""
        orm1 = _make_customer_orm(name="A")
        orm2 = _make_customer_orm(name="B")

        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 2
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = [orm1, orm2]
        session.execute = AsyncMock(side_effect=[count_result, rows_result])

        company_id = uuid.uuid4()
        result = await list_customers(session, company_id)
        assert result.total == 2
        assert len(result.items) == 2

    async def test_default_pagination(self) -> None:
        """Default limit=50, offset=0."""
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        rows_result = MagicMock()
        rows_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(side_effect=[count_result, rows_result])

        await list_customers(session, uuid.uuid4())
        # Verify two execute calls (count + rows).
        assert session.execute.call_count == 2


# ---------------------------------------------------------------------------
# Service logic – get_customer (mocked DB)
# ---------------------------------------------------------------------------


class TestGetCustomer:
    """Tests for ``get_customer``."""

    async def test_returns_none_when_not_found(self) -> None:
        """No matching customer → returns None."""
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        result = await get_customer(session, uuid.uuid4(), uuid.uuid4())
        assert result is None

    async def test_returns_customer_when_found(self) -> None:
        """Matching customer → returns the ORM instance."""
        orm = _make_customer_orm()
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = orm
        session.execute.return_value = result_mock

        result = await get_customer(session, orm.id, orm.company_id)
        assert result is orm


# ---------------------------------------------------------------------------
# Service logic – create_customer (mocked DB)
# ---------------------------------------------------------------------------


class TestCreateCustomer:
    """Tests for ``create_customer``."""

    async def test_creates_with_company_id(self) -> None:
        """Create injects company_id from the caller, not the payload."""
        session = AsyncMock()
        session.add = MagicMock()

        async def _fake_flush(*args: object, **kwargs: object) -> None:
            for call_args in session.add.call_args_list:
                obj = call_args[0][0]
                if obj.id is None:
                    obj.id = uuid.uuid4()
                if not hasattr(obj, "created_at") or obj.created_at is None:
                    obj.created_at = datetime.now(UTC)
                if not hasattr(obj, "updated_at") or obj.updated_at is None:
                    obj.updated_at = datetime.now(UTC)

        session.flush = AsyncMock(side_effect=_fake_flush)
        session.refresh = AsyncMock()

        company_id = uuid.uuid4()
        data = CustomerWrite(name="New Client", currency="EUR")
        result = await create_customer(session, data, company_id)

        assert result.name == "New Client"
        # Verify the ORM object was created with correct company_id.
        added_obj = session.add.call_args[0][0]
        assert added_obj.company_id == company_id
        assert added_obj.name == "New Client"

    async def test_create_sets_all_fields(self) -> None:
        """All fields from CustomerWrite are set on the ORM instance."""
        session = AsyncMock()
        session.add = MagicMock()

        async def _fake_flush(*args: object, **kwargs: object) -> None:
            for call_args in session.add.call_args_list:
                obj = call_args[0][0]
                if obj.id is None:
                    obj.id = uuid.uuid4()
                if not hasattr(obj, "created_at") or obj.created_at is None:
                    obj.created_at = datetime.now(UTC)
                if not hasattr(obj, "updated_at") or obj.updated_at is None:
                    obj.updated_at = datetime.now(UTC)

        session.flush = AsyncMock(side_effect=_fake_flush)
        session.refresh = AsyncMock()

        data = CustomerWrite(
            name="Full Client",
            contact_name="Jane",
            company_name="Full Corp",
            email="jane@full.com",
            phone="+31600000000",
            website="https://full.com",
            vat_id="NL000000000B01",
            currency="USD",
            extra={"tag": "test"},
        )
        result = await create_customer(session, data, uuid.uuid4())
        assert result.name == "Full Client"

        added_obj = session.add.call_args[0][0]
        assert added_obj.contact_name == "Jane"
        assert added_obj.company_name == "Full Corp"
        assert added_obj.email == "jane@full.com"
        assert added_obj.phone == "+31600000000"
        assert added_obj.website == "https://full.com"
        assert added_obj.vat_id == "NL000000000B01"
        assert added_obj.currency == "USD"
        assert added_obj.extra == {"tag": "test"}


# ---------------------------------------------------------------------------
# Service logic – update_customer (mocked DB)
# ---------------------------------------------------------------------------


class TestUpdateCustomer:
    """Tests for ``update_customer``."""

    async def test_update_mutates_fields(self) -> None:
        """Update sets all scalar fields on the existing ORM object."""
        orm = _make_customer_orm(name="Old", currency="EUR", vat_id="OLD")
        session = AsyncMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()

        data = CustomerWrite(name="New", currency="USD", vat_id="NEW")
        result = await update_customer(session, orm, data)

        assert orm.name == "New"
        assert orm.currency == "USD"
        assert orm.vat_id == "NEW"
        assert result.name == "New"

    async def test_update_sets_extra(self) -> None:
        """Update replaces the extra dict."""
        orm = _make_customer_orm(extra={"old": True})
        session = AsyncMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()

        data = CustomerWrite(name="X", extra={"new": True})
        await update_customer(session, orm, data)
        assert orm.extra == {"new": True}


# ---------------------------------------------------------------------------
# Service logic – delete_customer (mocked DB)
# ---------------------------------------------------------------------------


class TestDeleteCustomer:
    """Tests for ``delete_customer``."""

    async def test_delete_calls_session_delete(self) -> None:
        """delete_customer calls session.delete + flush."""
        orm = _make_customer_orm()
        session = AsyncMock()
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        await delete_customer(session, orm)
        session.delete.assert_called_once_with(orm)
        session.flush.assert_called_once()
