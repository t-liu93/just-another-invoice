"""M11 mileage data foundation, settings validation, and pure calculations."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import SettingLevel, VatTreatmentEffect, VatTreatmentSide
from jai.models.dictionary import ExpenseCategory
from jai.models.mileage import MileageRate, MileageTransportType
from jai.models.vat import VatTreatment
from jai.schemas.mileage import (
    MileageDefaultsRead,
    MileageDefaultsUpdate,
    validate_nonnegative_numeric_18_3,
    validate_positive_numeric_18_3,
)
from jai.schemas.setting import SETTING_KEY_MILEAGE_DEFAULTS
from jai.services.money import quantize_to_minor_unit
from jai.services.settings import get_setting, set_setting

_TRANSPORT_TYPE_SEEDS = ("Car", "Motorcycle", "Bicycle", "Other")
_RATE_SEEDS = ((date(2024, 1, 1), Decimal("0.230")), (date(2026, 1, 1), Decimal("0.250")))
_MILEAGE_CATEGORY = "Mileage"
_MILEAGE_TREATMENT_CODE = "NL_PRIVATE_TRANSPORT_MILEAGE"
@dataclass(frozen=True)
class RateCandidate:
    """Minimal immutable input for the pure effective-rate resolver."""

    id: uuid.UUID
    transport_type_id: uuid.UUID | None
    effective_from: date
    rate_per_km: Decimal


def _positive_finite(value: Decimal, label: str) -> None:
    validate_positive_numeric_18_3(value, label)


def derive_total_distance(one_way_distance_km: Decimal, round_trip: bool) -> Decimal:
    """Validate and derive the stored three-decimal trip distance."""
    _positive_finite(one_way_distance_km, "One-way distance")
    multiplier = Decimal("2") if round_trip else Decimal("1")
    try:
        total_distance_km = one_way_distance_km * multiplier
    except DecimalException as exc:
        raise ValueError("Total distance must be representable as NUMERIC(18,3).") from exc
    _positive_finite(total_distance_km, "Total distance")
    return total_distance_km


def resolve_effective_rate(
    rules: Iterable[RateCandidate],
    trip_date: date,
    transport_type_id: uuid.UUID | None,
) -> RateCandidate:
    """Apply D8: newest matching type-specific rule, then newest general rule."""
    candidates = [rule for rule in rules if rule.effective_from <= trip_date]
    type_specific = [
        rule
        for rule in candidates
        if transport_type_id is not None and rule.transport_type_id == transport_type_id
    ]
    general = [rule for rule in candidates if rule.transport_type_id is None]
    selected = type_specific or general
    if not selected:
        raise LookupError("No applicable mileage rate is configured for this trip date.")
    return max(selected, key=lambda rule: rule.effective_from)


def calculate_mileage_amount(total_distance_km: Decimal, rate_per_km: Decimal) -> Decimal:
    """Calculate and round only the final monetary amount to currency cents."""
    _positive_finite(total_distance_km, "Total distance")
    _positive_finite(rate_per_km, "Mileage rate")
    try:
        raw_amount = total_distance_km * rate_per_km
        amount = quantize_to_minor_unit(raw_amount)
    except DecimalException as exc:
        raise ValueError("Mileage amount must be representable as NUMERIC(18,3).") from exc
    validate_nonnegative_numeric_18_3(amount, "Mileage amount")
    return amount


async def validate_mileage_defaults(
    session: AsyncSession,
    company_id: uuid.UUID,
    defaults: MileageDefaultsRead | MileageDefaultsUpdate,
) -> MileageDefaultsRead:
    """Ensure company defaults refer to live company-local category and type."""
    category_result = await session.execute(
        select(ExpenseCategory.id).where(
            ExpenseCategory.id == defaults.expense_category_id,
            ExpenseCategory.company_id == company_id,
            ExpenseCategory.active.is_(True),
        )
    )
    if category_result.scalar_one_or_none() is None:
        raise ValueError("Mileage expense category must be active and belong to this company.")
    type_result = await session.execute(
        select(MileageTransportType.id).where(
            MileageTransportType.id == defaults.default_transport_type_id,
            MileageTransportType.company_id == company_id,
            MileageTransportType.active.is_(True),
        )
    )
    if type_result.scalar_one_or_none() is None:
        raise ValueError("Default transport type must be active and belong to this company.")
    return MileageDefaultsRead(**defaults.model_dump())


async def get_mileage_defaults(session: AsyncSession, company_id: uuid.UUID) -> MileageDefaultsRead:
    """Read and validate the company-level default references."""
    defaults = await get_setting(
        session,
        SETTING_KEY_MILEAGE_DEFAULTS,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=MileageDefaultsRead,
    )
    if defaults is None:
        raise LookupError("Mileage defaults are not configured for this company.")
    return await validate_mileage_defaults(session, company_id, defaults)


async def update_mileage_defaults(
    session: AsyncSession,
    company_id: uuid.UUID,
    defaults: MileageDefaultsUpdate,
) -> MileageDefaultsRead:
    """Validate then persist defaults at the COMPANY settings level."""
    validated = await validate_mileage_defaults(session, company_id, defaults)
    await set_setting(
        session,
        SETTING_KEY_MILEAGE_DEFAULTS,
        validated,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
    )
    return validated


async def seed_for_company(session: AsyncSession, company_id: uuid.UUID) -> None:
    """Seed editable M11 dictionaries and defaults idempotently for a company."""
    category_result = await session.execute(
        select(ExpenseCategory).where(
            ExpenseCategory.company_id == company_id, ExpenseCategory.name == _MILEAGE_CATEGORY
        )
    )
    category = category_result.scalar_one_or_none()
    if category is None:
        category = ExpenseCategory(
            company_id=company_id,
            name=_MILEAGE_CATEGORY,
            default_deductible=False,
            active=True,
        )
        session.add(category)
    elif not category.active or category.default_deductible is not False:
        raise ValueError(
            "Existing Mileage category conflicts with the required active, "
            "non-deductible mileage seed; resolve it before seeding defaults."
        )

    types_result = await session.execute(
        select(MileageTransportType).where(MileageTransportType.company_id == company_id)
    )
    by_name = {item.name: item for item in types_result.scalars()}
    for name in _TRANSPORT_TYPE_SEEDS:
        if name not in by_name:
            item = MileageTransportType(company_id=company_id, name=name, active=True)
            session.add(item)
            by_name[name] = item
        elif not by_name[name].active:
            raise ValueError(
                f"Existing {name} transport type is inactive; reactivate it before "
                "seeding defaults."
            )

    existing_rates_result = await session.execute(
        select(MileageRate.effective_from).where(
            MileageRate.company_id == company_id, MileageRate.transport_type_id.is_(None)
        )
    )
    existing_dates = set(existing_rates_result.scalars())
    for effective_from, rate_per_km in _RATE_SEEDS:
        if effective_from not in existing_dates:
            session.add(
                MileageRate(
                    company_id=company_id,
                    transport_type_id=None,
                    effective_from=effective_from,
                    rate_per_km=rate_per_km,
                )
            )

    treatment_result = await session.execute(
        select(VatTreatment).where(
            VatTreatment.company_id == company_id, VatTreatment.code == _MILEAGE_TREATMENT_CODE
        )
    )
    treatment = treatment_result.scalar_one_or_none()
    if treatment is None:
        session.add(
            VatTreatment(
                company_id=company_id,
                code=_MILEAGE_TREATMENT_CODE,
                label="NL Private Transport Mileage",
                side=VatTreatmentSide.PURCHASE,
                effect=VatTreatmentEffect.EXEMPT,
                report_box=None,
                requires_icp=False,
                deductible=False,
                active=True,
            )
        )
    elif (
        treatment.side != VatTreatmentSide.PURCHASE
        or treatment.effect != VatTreatmentEffect.EXEMPT
        or treatment.report_box is not None
        or treatment.requires_icp
        or treatment.deductible is not False
        or not treatment.active
    ):
        raise ValueError(
            "Existing NL_PRIVATE_TRANSPORT_MILEAGE VAT treatment conflicts with "
            "the required active purchase EXEMPT, no-box, non-deductible seed; "
            "resolve it before seeding defaults."
        )

    await session.flush()
    # Existing defaults are deliberately preserved: a company can customise them.
    current = await get_setting(
        session,
        SETTING_KEY_MILEAGE_DEFAULTS,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=MileageDefaultsRead,
    )
    if current is None:
        await set_setting(
            session,
            SETTING_KEY_MILEAGE_DEFAULTS,
            MileageDefaultsRead(
                expense_category_id=category.id,
                default_transport_type_id=by_name["Car"].id,
            ),
            level=SettingLevel.COMPANY,
            scope_id=company_id,
        )
