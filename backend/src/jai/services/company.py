"""Company business-profile service (singleton, M2 step 1).

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
- ``logo_id`` / ``binary_asset`` FK are managed in step 2.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import SettingLevel
from jai.models.company import Company
from jai.models.user import User
from jai.schemas.company import CompanyRead, CompanyWrite
from jai.schemas.setting import SETTING_KEY_ONBOARDING_COMPLETED, OnboardingState
from jai.services.settings import set_setting

#: Advisory-lock key serialising the first company creation.
_COMPANY_LOCK_KEY = 4915_0002


async def get_company(session: AsyncSession) -> Company | None:
    """Return the singleton company row, or ``None`` if not yet created."""
    stmt = select(Company).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def company_to_read(company: Company) -> CompanyRead:
    """Convert a ``Company`` ORM instance to a ``CompanyRead`` schema."""
    return CompanyRead(
        id=company.id,
        name=company.name,
        vat_id=company.vat_id,
        coc_number=company.coc_number,
        email=company.email,
        phone=company.phone,
        website=company.website,
        address_line1=company.address_line1,
        address_line2=company.address_line2,
        postal_code=company.postal_code,
        city=company.city,
        country_code=company.country_code,
        base_currency=company.base_currency,
        has_logo=company.logo_id is not None,
        logo_url="/api/v1/company/logo" if company.logo_id is not None else None,
        created_at=company.created_at,
        updated_at=company.updated_at,
    )


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
        existing.name = data.name
        existing.vat_id = data.vat_id
        existing.coc_number = data.coc_number
        existing.email = data.email
        existing.phone = data.phone
        existing.website = data.website
        existing.address_line1 = data.address_line1
        existing.address_line2 = data.address_line2
        existing.postal_code = data.postal_code
        existing.city = data.city
        existing.country_code = data.country_code
        existing.base_currency = data.base_currency
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
        existing.name = data.name
        existing.vat_id = data.vat_id
        existing.coc_number = data.coc_number
        existing.email = data.email
        existing.phone = data.phone
        existing.website = data.website
        existing.address_line1 = data.address_line1
        existing.address_line2 = data.address_line2
        existing.postal_code = data.postal_code
        existing.city = data.city
        existing.country_code = data.country_code
        existing.base_currency = data.base_currency
        await session.flush()
        await session.refresh(existing)
        return company_to_read(existing)

    # First creation – insert + side-effects.
    company = Company(
        name=data.name,
        vat_id=data.vat_id,
        coc_number=data.coc_number,
        email=data.email,
        phone=data.phone,
        website=data.website,
        address_line1=data.address_line1,
        address_line2=data.address_line2,
        postal_code=data.postal_code,
        city=data.city,
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

    return company_to_read(company)
