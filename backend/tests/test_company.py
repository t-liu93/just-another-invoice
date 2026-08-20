"""Unit tests for ``jai.services.company`` and ``jai.schemas.company``.

All tests use mocks — no running PostgreSQL required.  Verifies:
- Schema validation (ISO 4217 / ISO 3166)
- Singleton upsert logic (first creation side-effects, update path)
- ``company_to_read`` mapping
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jai.models._enums import SettingLevel
from jai.schemas.company import CompanyRead, CompanyWrite
from jai.schemas.setting import SETTING_KEY_ONBOARDING_COMPLETED
from jai.services.company import company_to_read, get_company, upsert_company

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_company_orm(**overrides: object) -> SimpleNamespace:
    """Build a mock Company ORM instance with sensible defaults."""
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "Test Company",
        "legal_name": None,
        "vat_id": None,
        "coc_number": None,
        "email": None,
        "phone": None,
        "website": None,
        "street": None,
        "house_number": None,
        "house_number_addition": None,
        "postal_code": None,
        "city": None,
        "province": None,
        "country_code": None,
        "base_currency": "EUR",
        "logo_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_owner() -> SimpleNamespace:
    """Build a mock User (owner) with ``company_id = None``."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        role="owner",
        company_id=None,
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestCompanyWriteValidation:
    """Tests for ``CompanyWrite`` Pydantic schema."""

    def test_valid_minimal(self) -> None:
        """Minimal valid payload: name + base_currency."""
        cw = CompanyWrite(name="Acme", base_currency="EUR")
        assert cw.name == "Acme"
        assert cw.base_currency == "EUR"

    def test_valid_full(self) -> None:
        """Full payload with all optional fields (structured address)."""
        cw = CompanyWrite(
            name="Acme",
            vat_id="NL123456789B01",
            coc_number="12345678",
            email="info@acme.nl",
            phone="+31612345678",
            website="https://acme.nl",
            street="Keizersgracht",
            house_number="1",
            house_number_addition="A",
            postal_code="1015 AB",
            city="Amsterdam",
            province=None,
            country_code="NL",
            base_currency="EUR",
        )
        assert cw.country_code == "NL"
        assert cw.street == "Keizersgracht"
        assert cw.house_number == "1"

    def test_invalid_currency_rejected(self) -> None:
        """Invalid ISO 4217 code raises ValidationError."""
        with pytest.raises(Exception, match="Invalid ISO 4217"):
            CompanyWrite(name="X", base_currency="XYZ")

    def test_invalid_country_rejected(self) -> None:
        """Invalid ISO 3166 code raises ValidationError."""
        with pytest.raises(Exception, match="Invalid ISO 3166"):
            CompanyWrite(name="X", base_currency="EUR", country_code="ZZ")

    def test_currency_name_rejected(self) -> None:
        """English name like 'Euro' is NOT a valid ISO 4217 alpha-3 code."""
        with pytest.raises(Exception, match="Invalid ISO 4217"):
            CompanyWrite(name="X", base_currency="Euro")

    def test_country_name_rejected(self) -> None:
        """English name like 'Netherlands' is NOT a valid ISO 3166 alpha-2 code."""
        with pytest.raises(Exception, match="Invalid ISO 3166"):
            CompanyWrite(name="X", base_currency="EUR", country_code="Netherlands")

    def test_currency_alpha3_code_deu_rejected(self) -> None:
        """'DEU' is an ISO 3166 alpha-3 code, not a valid ISO 4217 currency code."""
        with pytest.raises(Exception, match="Invalid ISO 4217"):
            CompanyWrite(name="X", base_currency="DEU")

    def test_empty_name_rejected(self) -> None:
        """Empty string name is rejected."""
        with pytest.raises(ValueError):
            CompanyWrite(name="", base_currency="EUR")

    def test_whitespace_only_name_rejected(self) -> None:
        """Whitespace-only name is rejected after strip."""
        with pytest.raises(ValueError):
            CompanyWrite(name="   ", base_currency="EUR")

    def test_currency_normalised_to_upper(self) -> None:
        """Lowercase currency is normalised to uppercase."""
        cw = CompanyWrite(name="X", base_currency="eur")
        assert cw.base_currency == "EUR"

    def test_country_none_allowed(self) -> None:
        """country_code can be None."""
        cw = CompanyWrite(name="X", base_currency="USD", country_code=None)
        assert cw.country_code is None


class TestCompanyReadMapping:
    """Tests for ``company_to_read``."""

    def test_basic_mapping(self) -> None:
        """All fields are mapped correctly."""
        company_id = uuid.uuid4()
        orm = _make_company_orm(id=company_id, name="Test")
        read = company_to_read(orm)
        assert isinstance(read, CompanyRead)
        assert read.id == company_id
        assert read.name == "Test"
        assert read.has_logo is False
        assert read.logo_url is None

    def test_logo_fields_when_present(self) -> None:
        """has_logo=True and logo_url set when logo_id is not None."""
        logo_id = uuid.uuid4()
        orm = _make_company_orm(logo_id=logo_id)
        read = company_to_read(orm)
        assert read.has_logo is True
        assert read.logo_url.startswith("/api/v1/company/logo?v=")

    def test_logo_fields_when_absent(self) -> None:
        """has_logo=False and logo_url=None when logo_id is None."""
        orm = _make_company_orm(logo_id=None)
        read = company_to_read(orm)
        assert read.has_logo is False
        assert read.logo_url is None


# ---------------------------------------------------------------------------
# Service logic (mocked DB)
# ---------------------------------------------------------------------------


class TestGetCompany:
    """Tests for ``get_company``."""

    async def test_returns_none_when_empty(self) -> None:
        """No company row → returns None."""
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        result = await get_company(session)
        assert result is None

    async def test_returns_company_when_exists(self) -> None:
        """Existing row → returns the ORM instance."""
        orm = _make_company_orm()
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = orm
        session.execute.return_value = result_mock

        result = await get_company(session)
        assert result is orm


class TestUpsertCompany:
    """Tests for ``upsert_company``."""

    async def test_first_creation_sets_onboarding(self) -> None:
        """First upsert sets onboarding.completed=true."""
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock
        # session.add() is synchronous in real AsyncSession.
        session.add = MagicMock()

        # Mock flush to set server-default fields on the Company instance.
        async def _fake_flush() -> None:
            for call_args in session.add.call_args_list:
                obj = call_args[0][0]
                if hasattr(obj, "base_currency"):
                    if obj.id is None:
                        obj.id = uuid.uuid4()
                    if obj.created_at is None:
                        obj.created_at = datetime.now(UTC)
                    if obj.updated_at is None:
                        obj.updated_at = datetime.now(UTC)

        session.flush = AsyncMock(side_effect=_fake_flush)

        owner = _make_owner()
        data = CompanyWrite(name="Acme", base_currency="EUR")

        with (
            patch("jai.services.company.set_setting", new_callable=AsyncMock) as mock_set,
            patch("jai.services.company._seed_mileage", new_callable=AsyncMock),
        ):
            result = await upsert_company(session, data, owner)

        assert result.name == "Acme"
        # set_setting was called for onboarding.completed.
        mock_set.assert_called_once()
        call_args = mock_set.call_args
        assert call_args[0][1] == SETTING_KEY_ONBOARDING_COMPLETED
        assert call_args[1]["level"] == SettingLevel.GLOBAL

    async def test_first_creation_links_owner(self) -> None:
        """First upsert links owner.company_id to the new company."""
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock
        session.add = MagicMock()

        async def _fake_flush() -> None:
            for call_args in session.add.call_args_list:
                obj = call_args[0][0]
                if hasattr(obj, "base_currency"):
                    if obj.id is None:
                        obj.id = uuid.uuid4()
                    if obj.created_at is None:
                        obj.created_at = datetime.now(UTC)
                    if obj.updated_at is None:
                        obj.updated_at = datetime.now(UTC)

        session.flush = AsyncMock(side_effect=_fake_flush)

        owner = _make_owner()
        data = CompanyWrite(name="Acme", base_currency="EUR")

        with (
            patch("jai.services.company.set_setting", new_callable=AsyncMock),
            patch("jai.services.company._seed_mileage", new_callable=AsyncMock),
        ):
            result = await upsert_company(session, data, owner)

        # owner.company_id should have been set.
        assert owner.company_id is not None
        assert owner.company_id == result.id

    async def test_update_does_not_set_onboarding(self) -> None:
        """Subsequent upsert (company exists) does not call set_setting."""
        existing = _make_company_orm(name="Old Name")
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        session.execute.return_value = result_mock

        owner = _make_owner()
        data = CompanyWrite(name="New Name", base_currency="USD")

        with patch("jai.services.company.set_setting", new_callable=AsyncMock) as mock_set:
            result = await upsert_company(session, data, owner)

        mock_set.assert_not_called()
        assert result.name == "New Name"
        # owner.company_id unchanged.
        assert owner.company_id is None

    async def test_update_mutates_existing_row(self) -> None:
        """Update path sets all fields on the existing ORM object."""
        existing = _make_company_orm(
            name="Old", base_currency="EUR", vat_id="OLD_VAT"
        )
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        session.execute.return_value = result_mock

        owner = _make_owner()
        data = CompanyWrite(
            name="New",
            base_currency="USD",
            vat_id="NEW_VAT",
            country_code="DE",
            street="Musterstraße",
            house_number="5",
            postal_code="10115",
            city="Berlin",
        )

        with patch("jai.services.company.set_setting", new_callable=AsyncMock):
            await upsert_company(session, data, owner)

        assert existing.name == "New"
        assert existing.base_currency == "USD"
        assert existing.vat_id == "NEW_VAT"
        assert existing.country_code == "DE"
        assert existing.street == "Musterstraße"
        assert existing.house_number == "5"
        assert existing.city == "Berlin"
