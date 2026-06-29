"""Company business-profile service (singleton, M2 step 1; address M3 step 3).

Public API
----------
- ``get_company`` – retrieve the singleton company row (or ``None``).
- ``upsert_company`` – create or update the company.  On **first creation**:
  - Set ``onboarding.completed = true`` (global setting).
  - Link the owner's ``company_id`` to the new company row.
  Both side-effects run in the same DB transaction.

Design notes
------------
- v1 enforces a single row via an advisory lock on the creation path
  that serialises concurrent first-PUT requests, preventing duplicate
  ``company`` rows.
- Address fields use structured European/Dutch schema (M3 step 3):
  street / house_number / house_number_addition / postal_code / city /
  province / country_code.  ``address_line1``/``address_line2`` removed.
- ``logo_id`` / ``binary_asset`` FK are managed in M2 step 2.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import SettingLevel
from jai.models.company import Company
from jai.models.user import User
from jai.schemas.company import CompanyRead, CompanyWrite
from jai.schemas.setting import SETTING_KEY_ONBOARDING_COMPLETED, OnboardingState
from jai.services.dictionary import seed_for_company as _seed_dictionaries
from jai.services.settings import set_setting
from jai.services.vat import seed_for_company as _seed_vat

#: Advisory-lock key serialising the first company creation.
_COMPANY_LOCK_KEY = 4915_0002


async def get_company(session: AsyncSession) -> Company | None:
    """Return the singleton company row, or ``None`` if not yet created."""
    stmt = select(Company).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def company_to_read(company: Company) -> CompanyRead:
    """Convert a ``Company`` ORM instance to a ``CompanyRead`` schema."""
    logo_url: str | None = None
    if company.logo_id is not None:
        # Cache-bust with logo_id hex prefix (changes on every replacement
        # because each upload creates a new BinaryAsset with a fresh UUID).
        version = company.logo_id.hex[:16]
        logo_url = f"/api/v1/company/logo?v={version}"
    return CompanyRead(
        id=company.id,
        name=company.name,
        legal_name=company.legal_name,
        vat_id=company.vat_id,
        coc_number=company.coc_number,
        email=company.email,
        phone=company.phone,
        website=company.website,
        street=company.street,
        house_number=company.house_number,
        house_number_addition=company.house_number_addition,
        postal_code=company.postal_code,
        city=company.city,
        province=company.province,
        country_code=company.country_code,
        base_currency=company.base_currency,
        has_logo=company.logo_id is not None,
        logo_url=logo_url,
        created_at=company.created_at,
        updated_at=company.updated_at,
    )


def _apply_write(company: Company, data: CompanyWrite) -> None:
    """Apply all ``CompanyWrite`` fields to a ``Company`` ORM instance."""
    company.name = data.name
    company.legal_name = data.legal_name
    company.vat_id = data.vat_id
    company.coc_number = data.coc_number
    company.email = data.email
    company.phone = data.phone
    company.website = data.website
    company.street = data.street
    company.house_number = data.house_number
    company.house_number_addition = data.house_number_addition
    company.postal_code = data.postal_code
    company.city = data.city
    company.province = data.province
    company.country_code = data.country_code
    company.base_currency = data.base_currency


async def upsert_company(
    session: AsyncSession,
    data: CompanyWrite,
    owner: User,
) -> CompanyRead:
    """Create or update the singleton company.

    On **first creation** (no existing row):
      1. Insert a new ``Company`` row.
      2. Set ``onboarding.completed = true`` (global setting).
      3. Set ``owner.company_id`` to the new company's ``id``.

    The creation path is serialised with ``pg_advisory_xact_lock`` so that
    two concurrent first-PUT requests cannot both see an empty table and
    each insert a row.  The lock is transaction-scoped (released on commit
    or rollback).

    All three mutations happen in the same transaction; the caller is
    responsible for calling ``session.commit()``.
    """
    existing = await get_company(session)

    if existing is not None:
        # Fast path: update existing row.
        _apply_write(existing, data)
        await session.flush()
        # Refresh to pick up server-default updated_at (onupdate=func.now()).
        await session.refresh(existing)
        return company_to_read(existing)

    # Serialise the first creation to prevent duplicate rows.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)").bindparams(key=_COMPANY_LOCK_KEY)
    )

    # Re-check after acquiring the lock — another request may have inserted.
    existing = await get_company(session)
    if existing is not None:
        # Another request won the race; treat as an update.
        _apply_write(existing, data)
        await session.flush()
        await session.refresh(existing)
        return company_to_read(existing)

    # First creation – insert + side-effects.
    company = Company(
        name=data.name,
        legal_name=data.legal_name,
        vat_id=data.vat_id,
        coc_number=data.coc_number,
        email=data.email,
        phone=data.phone,
        website=data.website,
        street=data.street,
        house_number=data.house_number,
        house_number_addition=data.house_number_addition,
        postal_code=data.postal_code,
        city=data.city,
        province=data.province,
        country_code=data.country_code,
        base_currency=data.base_currency,
    )
    session.add(company)
    await session.flush()  # assign company.id

    # Mark onboarding as completed.
    await set_setting(
        session,
        SETTING_KEY_ONBOARDING_COMPLETED,
        OnboardingState(completed=True),
        level=SettingLevel.GLOBAL,
    )

    # Link owner to the company.
    owner.company_id = company.id
    await session.flush()

    # Seed default VAT dictionaries for the new company.
    await _seed_vat(session, company.id)
    # Seed payment methods, expense categories, and units.
    await _seed_dictionaries(session, company.id)

    return company_to_read(company)
