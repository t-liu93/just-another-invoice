"""Integration coverage for M11 Step 1 migration seeds and typed defaults."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models.company import Company
from jai.models.dictionary import ExpenseCategory
from jai.models.mileage import MileageRate, MileageTransportType
from jai.schemas.mileage import MileageDefaultsUpdate
from jai.services.mileage import get_mileage_defaults, seed_for_company, update_mileage_defaults


async def _authenticate_owner(client: AsyncClient) -> None:
    assert (
        await client.post(
            "/api/v1/auth/register",
            json={"email": "mileage@example.com", "password": "testpassword1"},
        )
    ).status_code == 201
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "mileage@example.com", "password": "testpassword1"},
        )
    ).status_code == 200
    setup = await client.post("/api/v1/auth/mfa/setup")
    assert setup.status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(setup.json()["secret"]).now()},
        )
    ).status_code == 204


@pytest.mark.integration
class TestMileageFoundation:
    async def test_company_seed_defaults_are_idempotent_and_company_scoped(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        await _authenticate_owner(db_client)
        company_response = await db_client.put(
            "/api/v1/company",
            json={"name": "Mileage Co", "base_currency": "EUR", "country_code": "NL"},
        )
        assert company_response.status_code == 200
        first_company_id = company_response.json()["id"]

        defaults_response = await db_client.get("/api/v1/settings/mileage-defaults")
        assert defaults_response.status_code == 200
        defaults = defaults_response.json()

        async with db_session_maker() as session:
            category = await session.get(ExpenseCategory, defaults["expense_category_id"])
            transport_type = await session.get(
                MileageTransportType, defaults["default_transport_type_id"]
            )
            assert category is not None and category.name == "Mileage"
            assert category.default_deductible is False
            assert transport_type is not None and transport_type.name == "Car"

            # A direct second company exercises future-company seed idempotency.
            other_company = Company(name="Other Co", base_currency="EUR")
            session.add(other_company)
            await session.flush()
            await seed_for_company(session, other_company.id)
            await seed_for_company(session, other_company.id)

            seeded_type_names = list(
                (
                    await session.scalars(
                        select(MileageTransportType.name)
                        .where(MileageTransportType.company_id == other_company.id)
                        .order_by(MileageTransportType.name)
                    )
                ).all()
            )
            assert seeded_type_names == ["Bicycle", "Car", "Motorcycle", "Other"]
            seeded_general_rates = list(
                (
                    await session.execute(
                        select(MileageRate.effective_from, MileageRate.rate_per_km)
                        .where(
                            MileageRate.company_id == other_company.id,
                            MileageRate.transport_type_id.is_(None),
                        )
                        .order_by(MileageRate.effective_from)
                    )
                ).tuples()
            )
            assert seeded_general_rates == [
                (date(2024, 1, 1), Decimal("0.230")),
                (date(2026, 1, 1), Decimal("0.250")),
            ]

            other_defaults = await get_mileage_defaults(session, other_company.id)
            first_company_uuid = uuid.UUID(first_company_id)

            # Each category/type validation branch is independent: a category
            # failure must not hide a cross-company type failure, and vice versa.
            with pytest.raises(ValueError, match="belong to this company"):
                await update_mileage_defaults(
                    session,
                    first_company_uuid,
                    MileageDefaultsUpdate(
                        expense_category_id=other_defaults.expense_category_id,
                        default_transport_type_id=defaults["default_transport_type_id"],
                    ),
                )
            with pytest.raises(ValueError, match="belong to this company"):
                await update_mileage_defaults(
                    session,
                    first_company_uuid,
                    MileageDefaultsUpdate(
                        expense_category_id=defaults["expense_category_id"],
                        default_transport_type_id=other_defaults.default_transport_type_id,
                    ),
                )

            category.active = False
            await session.flush()
            with pytest.raises(ValueError, match="category must be active"):
                await update_mileage_defaults(
                    session,
                    first_company_uuid,
                    MileageDefaultsUpdate(
                        expense_category_id=defaults["expense_category_id"],
                        default_transport_type_id=defaults["default_transport_type_id"],
                    ),
                )
            category.active = True
            transport_type.active = False
            await session.flush()
            with pytest.raises(ValueError, match="transport type must be active"):
                await update_mileage_defaults(
                    session,
                    first_company_uuid,
                    MileageDefaultsUpdate(
                        expense_category_id=defaults["expense_category_id"],
                        default_transport_type_id=defaults["default_transport_type_id"],
                    ),
                )
