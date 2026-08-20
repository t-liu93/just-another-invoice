"""M11 mileage data foundation, settings validation, and pure calculations."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import (
    ExpenseKind,
    MileageTripOwnership,
    PaidBy,
    SettingLevel,
    VatTreatmentEffect,
    VatTreatmentSide,
)
from jai.models.company import Company
from jai.models.dictionary import ExpenseCategory
from jai.models.expense import Expense
from jai.models.mileage import MileageRate, MileageRateAdjustment, MileageTransportType, MileageTrip
from jai.models.vat import VatRate, VatTreatment
from jai.schemas.mileage import (
    MileageCalculationRead,
    MileageCalculationRequest,
    MileageDefaultsRead,
    MileageDefaultsUpdate,
    MileageExpenseListItem,
    MileageExpenseListResponse,
    MileageExpenseRead,
    MileageExpenseWrite,
    MileageRateListResponse,
    MileageRateRead,
    MileageRateWrite,
    MileageTransportTypeListResponse,
    MileageTransportTypeRead,
    MileageTransportTypeWrite,
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


class MileageConfigurationError(ValueError):
    """A missing or invalid editable mileage dictionary configuration."""


class MileageResourceNotFoundError(LookupError, ValueError):
    """An explicitly supplied editable mileage resource is not company-local."""


class MileageExpenseUpdateError(ValueError):
    """Raised when callers try to edit a generated Mileage expense generically."""


@dataclass(frozen=True)
class RateCandidate:
    """Minimal immutable input for the pure effective-rate resolver."""

    id: uuid.UUID
    transport_type_id: uuid.UUID | None
    effective_from: date
    rate_per_km: Decimal


@dataclass(frozen=True)
class _MileagePricing:
    """Resolved live inputs and immutable snapshots for one trip calculation."""

    company: Company
    category: ExpenseCategory
    transport_type: MileageTransportType
    rate: MileageRate
    treatment: VatTreatment
    vat_rate: VatRate
    total_distance_km: Decimal
    amount: Decimal


@dataclass(frozen=True)
class _ResolvedRateSnapshot:
    """The immutable rate facts retained when a trip is recalculated."""

    rule_id: uuid.UUID | None
    transport_type_id: uuid.UUID | None
    transport_type_name: str | None
    effective_from: date
    rate_per_km: Decimal

    @property
    def semantics(self) -> tuple[uuid.UUID | None, str | None, date, Decimal]:
        """Facts that change the selected rate, independent of rule UUID churn."""
        return (
            self.transport_type_id,
            self.transport_type_name,
            self.effective_from,
            self.rate_per_km,
        )


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
        select(ExpenseCategory).where(
            ExpenseCategory.id == defaults.expense_category_id,
            ExpenseCategory.company_id == company_id,
        )
    )
    category = category_result.scalar_one_or_none()
    if category is None:
        raise MileageResourceNotFoundError(
            "Mileage expense category must be active and belong to this company."
        )
    if not category.active:
        raise MileageConfigurationError("Mileage expense category must be active.")
    type_result = await session.execute(
        select(MileageTransportType).where(
            MileageTransportType.id == defaults.default_transport_type_id,
            MileageTransportType.company_id == company_id,
        )
    )
    transport_type = type_result.scalar_one_or_none()
    if transport_type is None:
        raise MileageResourceNotFoundError(
            "Default transport type must be active and belong to this company."
        )
    if not transport_type.active:
        raise MileageConfigurationError("Default transport type must be active.")
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
        raise MileageConfigurationError("Mileage defaults are not configured for this company.")
    try:
        return await validate_mileage_defaults(session, company_id, defaults)
    except (LookupError, ValueError) as exc:
        raise MileageConfigurationError(str(exc)) from exc


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


# ---------------------------------------------------------------------------
# Transport-type and rate dictionaries (M11 step 2)
# ---------------------------------------------------------------------------


def _transport_type_to_read(item: MileageTransportType) -> MileageTransportTypeRead:
    return MileageTransportTypeRead(
        id=item.id,
        name=item.name,
        active=item.active,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _rate_to_read(item: MileageRate) -> MileageRateRead:
    return MileageRateRead(
        id=item.id,
        transport_type_id=item.transport_type_id,
        effective_from=item.effective_from,
        rate_per_km=Decimal(str(item.rate_per_km)),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def _load_transport_type(
    session: AsyncSession, transport_type_id: uuid.UUID, company_id: uuid.UUID
) -> MileageTransportType:
    result = await session.execute(
        select(MileageTransportType).where(
            MileageTransportType.id == transport_type_id,
            MileageTransportType.company_id == company_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise LookupError("Mileage transport type not found.")
    return item


async def _load_rate(
    session: AsyncSession, rate_id: uuid.UUID, company_id: uuid.UUID
) -> MileageRate:
    result = await session.execute(
        select(MileageRate).where(MileageRate.id == rate_id, MileageRate.company_id == company_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise LookupError("Mileage rate not found.")
    return item


async def _configured_default_type_id(
    session: AsyncSession, company_id: uuid.UUID
) -> uuid.UUID | None:
    defaults = await get_setting(
        session,
        SETTING_KEY_MILEAGE_DEFAULTS,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=MileageDefaultsRead,
    )
    return None if defaults is None else defaults.default_transport_type_id


async def list_mileage_transport_types(
    session: AsyncSession, company_id: uuid.UUID
) -> MileageTransportTypeListResponse:
    result = await session.execute(
        select(MileageTransportType)
        .where(MileageTransportType.company_id == company_id)
        .order_by(MileageTransportType.active.desc(), MileageTransportType.name)
    )
    return MileageTransportTypeListResponse(
        items=[_transport_type_to_read(item) for item in result.scalars().all()]
    )


async def get_mileage_transport_type(
    session: AsyncSession, transport_type_id: uuid.UUID, company_id: uuid.UUID
) -> MileageTransportTypeRead:
    return _transport_type_to_read(
        await _load_transport_type(session, transport_type_id, company_id)
    )


async def _check_transport_type_name_unique(
    session: AsyncSession,
    company_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(MileageTransportType.id).where(
        MileageTransportType.company_id == company_id, MileageTransportType.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(MileageTransportType.id != exclude_id)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise ValueError(f"Mileage transport type name '{name}' already exists for this company.")


async def create_mileage_transport_type(
    session: AsyncSession, company_id: uuid.UUID, body: MileageTransportTypeWrite
) -> MileageTransportTypeRead:
    name = body.name.strip()
    await _check_transport_type_name_unique(session, company_id, name)
    item = MileageTransportType(company_id=company_id, name=name, active=body.active)
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(
            f"Mileage transport type name '{name}' already exists for this company."
        ) from exc
    await session.refresh(item)
    return _transport_type_to_read(item)


async def update_mileage_transport_type(
    session: AsyncSession,
    transport_type_id: uuid.UUID,
    company_id: uuid.UUID,
    body: MileageTransportTypeWrite,
) -> MileageTransportTypeRead:
    item = await _load_transport_type(session, transport_type_id, company_id)
    if not body.active and await _configured_default_type_id(session, company_id) == item.id:
        raise MileageConfigurationError(
            "The configured default transport type cannot be deactivated; "
            "select another default first."
        )
    name = body.name.strip()
    await _check_transport_type_name_unique(session, company_id, name, exclude_id=item.id)
    item.name = name
    item.active = body.active
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(
            f"Mileage transport type name '{name}' already exists for this company."
        ) from exc
    await session.refresh(item)
    return _transport_type_to_read(item)


async def delete_mileage_transport_type(
    session: AsyncSession, transport_type_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    item = await _load_transport_type(session, transport_type_id, company_id)
    if await _configured_default_type_id(session, company_id) == item.id:
        raise MileageConfigurationError(
            "The configured default transport type cannot be deleted; select another default first."
        )
    # The database owns cascading rate deletion and SET NULL historical trip links.
    await session.delete(item)
    await session.commit()


async def list_mileage_rates(
    session: AsyncSession, company_id: uuid.UUID
) -> MileageRateListResponse:
    result = await session.execute(
        select(MileageRate)
        .where(MileageRate.company_id == company_id)
        .order_by(
            MileageRate.transport_type_id.is_not(None),
            MileageRate.transport_type_id,
            MileageRate.effective_from.desc(),
        )
    )
    return MileageRateListResponse(items=[_rate_to_read(item) for item in result.scalars().all()])


async def _validate_rate_transport_type(
    session: AsyncSession, company_id: uuid.UUID, transport_type_id: uuid.UUID | None
) -> None:
    if transport_type_id is not None:
        await _load_transport_type(session, transport_type_id, company_id)


async def _check_rate_unique(
    session: AsyncSession,
    company_id: uuid.UUID,
    transport_type_id: uuid.UUID | None,
    effective_from: date,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(MileageRate.id).where(
        MileageRate.company_id == company_id,
        MileageRate.effective_from == effective_from,
    )
    if transport_type_id is None:
        stmt = stmt.where(MileageRate.transport_type_id.is_(None))
    else:
        stmt = stmt.where(MileageRate.transport_type_id == transport_type_id)
    if exclude_id is not None:
        stmt = stmt.where(MileageRate.id != exclude_id)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        scope = "general" if transport_type_id is None else "transport-type-specific"
        raise ValueError(f"A {scope} mileage rate already exists for this effective date.")


async def get_mileage_rate(
    session: AsyncSession, rate_id: uuid.UUID, company_id: uuid.UUID
) -> MileageRateRead:
    return _rate_to_read(await _load_rate(session, rate_id, company_id))


async def create_mileage_rate(
    session: AsyncSession, company_id: uuid.UUID, body: MileageRateWrite
) -> MileageRateRead:
    await _validate_rate_transport_type(session, company_id, body.transport_type_id)
    await _check_rate_unique(session, company_id, body.transport_type_id, body.effective_from)
    item = MileageRate(
        company_id=company_id,
        transport_type_id=body.transport_type_id,
        effective_from=body.effective_from,
        rate_per_km=body.rate_per_km,
    )
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(
            "A mileage rate with this scope and effective date already exists."
        ) from exc
    await session.refresh(item)
    return _rate_to_read(item)


async def update_mileage_rate(
    session: AsyncSession,
    rate_id: uuid.UUID,
    company_id: uuid.UUID,
    body: MileageRateWrite,
) -> MileageRateRead:
    item = await _load_rate(session, rate_id, company_id)
    await _validate_rate_transport_type(session, company_id, body.transport_type_id)
    await _check_rate_unique(
        session, company_id, body.transport_type_id, body.effective_from, exclude_id=item.id
    )
    item.transport_type_id = body.transport_type_id
    item.effective_from = body.effective_from
    item.rate_per_km = body.rate_per_km
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(
            "A mileage rate with this scope and effective date already exists."
        ) from exc
    await session.refresh(item)
    return _rate_to_read(item)


async def delete_mileage_rate(
    session: AsyncSession, rate_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    await session.delete(await _load_rate(session, rate_id, company_id))
    await session.commit()


# ---------------------------------------------------------------------------
# Mileage trip → Expense projection (M11 step 2)
# ---------------------------------------------------------------------------


async def _load_live_trip_transport_type(
    session: AsyncSession,
    company_id: uuid.UUID,
    requested_type_id: uuid.UUID | None,
) -> MileageTransportType:
    if requested_type_id is None:
        requested_type_id = (
            await get_mileage_defaults(session, company_id)
        ).default_transport_type_id
    item = await _load_transport_type(session, requested_type_id, company_id)
    if not item.active:
        raise MileageConfigurationError(
            "Mileage transport type is inactive and cannot be selected."
        )
    return item


async def _load_live_mileage_category(
    session: AsyncSession, company_id: uuid.UUID, category_id: uuid.UUID
) -> ExpenseCategory:
    result = await session.execute(
        select(ExpenseCategory).where(
            ExpenseCategory.id == category_id,
            ExpenseCategory.company_id == company_id,
            ExpenseCategory.active.is_(True),
        )
    )
    category = result.scalar_one_or_none()
    if category is None:
        raise MileageConfigurationError("Mileage expense category is inactive or unavailable.")
    return category


async def _load_mileage_vat_treatment(session: AsyncSession, company_id: uuid.UUID) -> VatTreatment:
    result = await session.execute(
        select(VatTreatment).where(
            VatTreatment.company_id == company_id,
            VatTreatment.code == _MILEAGE_TREATMENT_CODE,
        )
    )
    treatment = result.scalar_one_or_none()
    if treatment is None:
        raise MileageConfigurationError("Mileage VAT treatment is not configured for this company.")
    if (
        not treatment.active
        or treatment.side != VatTreatmentSide.PURCHASE
        or treatment.effect != VatTreatmentEffect.EXEMPT
        or treatment.report_box is not None
        or treatment.requires_icp
        or treatment.deductible is not False
    ):
        raise MileageConfigurationError(
            "Mileage VAT treatment must be active PURCHASE EXEMPT with no report box."
        )
    return treatment


async def _load_company_zero_vat_rate(session: AsyncSession, company_id: uuid.UUID) -> VatRate:
    result = await session.execute(
        select(VatRate).where(
            VatRate.company_id == company_id,
            VatRate.active.is_(True),
            VatRate.percent == Decimal("0"),
        )
    )
    rates = list(result.scalars().all())
    if not rates:
        raise MileageConfigurationError("An active company zero VAT rate is required for mileage.")
    if len(rates) > 1:
        raise MileageConfigurationError(
            "Multiple active company zero VAT rates exist; keep one active "
            "before recording mileage."
        )
    return rates[0]


async def _resolve_rate_for_trip(
    session: AsyncSession,
    company_id: uuid.UUID,
    trip_date: date,
    transport_type_id: uuid.UUID,
) -> MileageRate:
    result = await session.execute(
        select(MileageRate).where(
            MileageRate.company_id == company_id,
            MileageRate.effective_from <= trip_date,
            or_(
                MileageRate.transport_type_id.is_(None),
                MileageRate.transport_type_id == transport_type_id,
            ),
        )
    )
    rules = list(result.scalars().all())
    try:
        selected = resolve_effective_rate(
            (
                RateCandidate(
                    id=rule.id,
                    transport_type_id=rule.transport_type_id,
                    effective_from=rule.effective_from,
                    rate_per_km=Decimal(str(rule.rate_per_km)),
                )
                for rule in rules
            ),
            trip_date,
            transport_type_id,
        )
    except LookupError as exc:
        raise MileageConfigurationError(str(exc)) from exc
    return next(rule for rule in rules if rule.id == selected.id)


async def _resolve_mileage_pricing(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: MileageCalculationRequest,
    *,
    require_category_and_vat: bool,
) -> _MileagePricing:
    defaults: MileageDefaultsRead | None = None
    if require_category_and_vat or body.transport_type_id is None:
        defaults = await get_mileage_defaults(session, company_id)
    transport_type = await _load_live_trip_transport_type(
        session, company_id, body.transport_type_id
    )
    rate = await _resolve_rate_for_trip(session, company_id, body.trip_date, transport_type.id)
    total_distance_km = derive_total_distance(body.one_way_distance_km, body.round_trip)
    amount = calculate_mileage_amount(total_distance_km, Decimal(str(rate.rate_per_km)))

    company_result = await session.execute(select(Company).where(Company.id == company_id))
    company = company_result.scalar_one_or_none()
    if company is None:
        raise LookupError("Company not found.")

    if require_category_and_vat:
        assert defaults is not None
        category = await _load_live_mileage_category(
            session, company_id, defaults.expense_category_id
        )
        treatment = await _load_mileage_vat_treatment(session, company_id)
        vat_rate = await _load_company_zero_vat_rate(session, company_id)
    else:
        # Calculation never exposes the unused fields, but the frozen structure
        # keeps one resolver for both preview and persistence paths.
        category = ExpenseCategory(company_id=company_id, name="", active=True)
        treatment = VatTreatment(
            company_id=company_id,
            code="",
            label="",
            side=VatTreatmentSide.PURCHASE,
            effect=VatTreatmentEffect.EXEMPT,
        )
        vat_rate = VatRate(company_id=company_id, label="", percent=Decimal("0"), active=True)
    return _MileagePricing(
        company=company,
        category=category,
        transport_type=transport_type,
        rate=rate,
        treatment=treatment,
        vat_rate=vat_rate,
        total_distance_km=total_distance_km,
        amount=amount,
    )


def _trip_rate_snapshot(trip: MileageTrip) -> _ResolvedRateSnapshot:
    """Capture the old rate scope before overwriting the trip snapshots.

    This deliberately uses only immutable trip facts: ``MileageRate`` is
    editable in place, including its transport-type scope.  Looking it up here
    would therefore rewrite history in an adjustment audit.  The snapshot also
    survives rule/type deletion because it has no mutable foreign key.
    """
    return _ResolvedRateSnapshot(
        rule_id=trip.rate_rule_id,
        transport_type_id=trip.rate_transport_type_id,
        transport_type_name=trip.rate_transport_type_name,
        effective_from=trip.rate_effective_from,
        rate_per_km=Decimal(str(trip.rate_per_km)),
    )


def _pricing_rate_snapshot(pricing: _MileagePricing) -> _ResolvedRateSnapshot:
    """Return the selected rule's full scope snapshot for an adjustment row."""
    return _ResolvedRateSnapshot(
        rule_id=pricing.rate.id,
        transport_type_id=pricing.rate.transport_type_id,
        transport_type_name=(
            pricing.transport_type.name if pricing.rate.transport_type_id is not None else None
        ),
        effective_from=pricing.rate.effective_from,
        rate_per_km=Decimal(str(pricing.rate.rate_per_km)),
    )


def _pricing_to_calculation(
    body: MileageCalculationRequest, pricing: _MileagePricing
) -> MileageCalculationRead:
    return MileageCalculationRead(
        one_way_distance_km=body.one_way_distance_km,
        total_distance_km=pricing.total_distance_km,
        rate_rule_id=pricing.rate.id,
        rate_effective_from=pricing.rate.effective_from,
        rate_per_km=Decimal(str(pricing.rate.rate_per_km)),
        amount=pricing.amount,
        currency=pricing.company.base_currency,
    )


async def calculate_mileage_expense(
    session: AsyncSession, company_id: uuid.UUID, body: MileageCalculationRequest
) -> MileageCalculationRead:
    pricing = await _resolve_mileage_pricing(
        session, company_id, body, require_category_and_vat=False
    )
    return _pricing_to_calculation(body, pricing)


def _apply_expense_projection(
    expense: Expense,
    pricing: _MileagePricing,
    trip_date: date,
    *,
    preserve_category_and_vat_snapshots: bool = False,
) -> None:
    """Set all D14/D15 projection snapshots; no caller-provided money is accepted."""
    amount = pricing.amount
    treatment = pricing.treatment
    vat_rate = pricing.vat_rate
    expense.company_id = pricing.company.id
    expense.kind = ExpenseKind.MILEAGE
    expense.expense_date = trip_date
    expense.supplier_name = None
    if not preserve_category_and_vat_snapshots:
        expense.category_id = pricing.category.id
        expense.category_name = pricing.category.name
        expense.vat_treatment_id = treatment.id
        expense.vat_treatment_code = treatment.code
        expense.vat_treatment_label = treatment.label
        expense.vat_treatment_effect = treatment.effect
        expense.vat_rate_id = vat_rate.id
        expense.vat_rate_percent = vat_rate.percent
        expense.vat_rate_label = vat_rate.label
    expense.net_amount = amount
    expense.vat_amount = Decimal("0")
    expense.gross_amount = amount
    expense.deductible = False
    expense.currency = pricing.company.base_currency
    expense.exchange_rate = Decimal("1")
    expense.base_net_amount = amount
    expense.base_vat_amount = Decimal("0")
    expense.base_gross_amount = amount
    expense.reference = None
    expense.note = None
    expense.paid_by = PaidBy.PRIVATE
    expense.business_percentage = Decimal("100")
    expense.depreciation_years = 1
    expense.is_draft = False


def _apply_trip_write(
    trip: MileageTrip,
    body: MileageExpenseWrite,
    pricing: _MileagePricing,
) -> None:
    trip.transport_type_id = pricing.transport_type.id
    trip.transport_type_name = pricing.transport_type.name
    trip.rate_rule_id = pricing.rate.id
    trip.rate_transport_type_id = pricing.rate.transport_type_id
    trip.rate_transport_type_name = (
        pricing.transport_type.name if pricing.rate.transport_type_id is not None else None
    )
    trip.rate_effective_from = pricing.rate.effective_from
    trip.rate_per_km = pricing.rate.rate_per_km
    trip.trip_date = body.trip_date
    trip.one_way_distance_km = body.one_way_distance_km
    trip.total_distance_km = pricing.total_distance_km
    trip.round_trip = body.round_trip
    trip.calculated_amount = pricing.amount
    trip.origin_address = body.origin_address
    trip.destination_address = body.destination_address
    trip.purpose = body.purpose
    trip.note = body.note


def _trip_to_read(trip: MileageTrip, expense: Expense) -> MileageExpenseRead:
    return MileageExpenseRead(
        id=trip.id,
        expense_id=trip.expense_id,
        expense_category_id=expense.category_id,
        trip_date=trip.trip_date,
        transport_type_id=trip.transport_type_id,
        transport_type_name=trip.transport_type_name,
        one_way_distance_km=Decimal(str(trip.one_way_distance_km)),
        total_distance_km=Decimal(str(trip.total_distance_km)),
        round_trip=trip.round_trip,
        rate_rule_id=trip.rate_rule_id,
        rate_effective_from=trip.rate_effective_from,
        rate_per_km=Decimal(str(trip.rate_per_km)),
        amount=Decimal(str(trip.calculated_amount)),
        currency=expense.currency,
        origin_address=trip.origin_address,
        destination_address=trip.destination_address,
        purpose=trip.purpose,
        note=trip.note,
        created_at=trip.created_at,
        updated_at=trip.updated_at,
    )


async def _load_trip(
    session: AsyncSession, trip_id: uuid.UUID, company_id: uuid.UUID
) -> MileageTrip:
    result = await session.execute(
        select(MileageTrip).where(MileageTrip.id == trip_id, MileageTrip.company_id == company_id)
    )
    trip = result.scalar_one_or_none()
    if trip is None:
        raise LookupError("Mileage expense not found.")
    return trip


async def _load_projection_expense(
    session: AsyncSession, trip: MileageTrip, company_id: uuid.UUID
) -> Expense:
    if trip.expense_id is None:
        raise MileageConfigurationError("Mileage trip has no Expense projection.")
    result = await session.execute(
        select(Expense).where(
            Expense.id == trip.expense_id,
            Expense.company_id == company_id,
            Expense.kind == ExpenseKind.MILEAGE,
        )
    )
    expense = result.scalar_one_or_none()
    if expense is None:
        raise MileageConfigurationError("Mileage trip Expense projection is unavailable.")
    return expense


async def create_mileage_expense(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: MileageExpenseWrite,
    creator_id: uuid.UUID | None,
) -> MileageExpenseRead:
    try:
        pricing = await _resolve_mileage_pricing(
            session, company_id, body, require_category_and_vat=True
        )
        expense = Expense(creator_id=creator_id)
        _apply_expense_projection(expense, pricing, body.trip_date)
        session.add(expense)
        await session.flush()
        trip = MileageTrip(
            company_id=company_id,
            expense_id=expense.id,
            ownership=MileageTripOwnership.PRIVATE,
            creator_id=creator_id,
            transport_type_name=pricing.transport_type.name,
            rate_effective_from=pricing.rate.effective_from,
            rate_per_km=pricing.rate.rate_per_km,
            trip_date=body.trip_date,
            one_way_distance_km=body.one_way_distance_km,
            total_distance_km=pricing.total_distance_km,
            calculated_amount=pricing.amount,
        )
        _apply_trip_write(trip, body, pricing)
        session.add(trip)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(trip)
    await session.refresh(expense)
    return _trip_to_read(trip, expense)


async def get_mileage_expense(
    session: AsyncSession, trip_id: uuid.UUID, company_id: uuid.UUID
) -> MileageExpenseRead:
    trip = await _load_trip(session, trip_id, company_id)
    return _trip_to_read(trip, await _load_projection_expense(session, trip, company_id))


async def update_mileage_expense(
    session: AsyncSession,
    trip_id: uuid.UUID,
    company_id: uuid.UUID,
    body: MileageExpenseWrite,
    actor_id: uuid.UUID | None,
) -> MileageExpenseRead:
    trip = await _load_trip(session, trip_id, company_id)
    expense = await _load_projection_expense(session, trip, company_id)
    old_amount = Decimal(str(trip.calculated_amount))
    try:
        old_rate = _trip_rate_snapshot(trip)
        pricing = await _resolve_mileage_pricing(
            session, company_id, body, require_category_and_vat=False
        )
        new_rate = _pricing_rate_snapshot(pricing)
        _apply_trip_write(trip, body, pricing)
        _apply_expense_projection(
            expense, pricing, body.trip_date, preserve_category_and_vat_snapshots=True
        )
        # A distance-only edit changes amount but not the resolved rate, so it
        # deliberately does not create a rate-adjustment audit row.  Compare
        # the full rate semantics rather than UUID alone: a rule can be edited
        # in place, while a replacement UUID with identical rate facts needs no
        # noisy audit entry.
        if old_rate.semantics != new_rate.semantics:
            session.add(
                MileageRateAdjustment(
                    company_id=company_id,
                    trip_id=trip.id,
                    old_rate_rule_id=old_rate.rule_id,
                    new_rate_rule_id=new_rate.rule_id,
                    old_rate_transport_type_id=old_rate.transport_type_id,
                    new_rate_transport_type_id=new_rate.transport_type_id,
                    old_rate_transport_type_name=old_rate.transport_type_name,
                    new_rate_transport_type_name=new_rate.transport_type_name,
                    old_rate_effective_from=old_rate.effective_from,
                    new_rate_effective_from=new_rate.effective_from,
                    old_rate_per_km=old_rate.rate_per_km,
                    new_rate_per_km=new_rate.rate_per_km,
                    old_amount=old_amount,
                    new_amount=pricing.amount,
                    actor_id=actor_id,
                )
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(trip)
    await session.refresh(expense)
    return _trip_to_read(trip, expense)


async def list_mileage_expenses(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    transport_type_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "trip_date",
) -> MileageExpenseListResponse:
    base = select(MileageTrip).where(MileageTrip.company_id == company_id)
    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                MileageTrip.purpose.ilike(like),
                MileageTrip.origin_address.ilike(like),
                MileageTrip.destination_address.ilike(like),
                MileageTrip.transport_type_name.ilike(like),
            )
        )
    if transport_type_id is not None:
        base = base.where(MileageTrip.transport_type_id == transport_type_id)
    if date_from is not None:
        base = base.where(MileageTrip.trip_date >= date_from)
    if date_to is not None:
        base = base.where(MileageTrip.trip_date <= date_to)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    order_by: tuple[object, object]
    if sort_by == "created_at":
        order_by = (MileageTrip.created_at.desc(), MileageTrip.trip_date.desc())
    else:
        order_by = (MileageTrip.trip_date.desc(), MileageTrip.created_at.desc())
    result = await session.execute(base.order_by(*order_by).limit(limit).offset(offset))
    items = [
        MileageExpenseListItem(
            id=trip.id,
            trip_date=trip.trip_date,
            transport_type_id=trip.transport_type_id,
            transport_type_name=trip.transport_type_name,
            one_way_distance_km=Decimal(str(trip.one_way_distance_km)),
            total_distance_km=Decimal(str(trip.total_distance_km)),
            round_trip=trip.round_trip,
            rate_per_km=Decimal(str(trip.rate_per_km)),
            amount=Decimal(str(trip.calculated_amount)),
            purpose=trip.purpose,
            origin_address=trip.origin_address,
            destination_address=trip.destination_address,
            created_at=trip.created_at,
        )
        for trip in result.scalars().all()
    ]
    return MileageExpenseListResponse(items=items, total=total)


async def delete_mileage_expense(
    session: AsyncSession, trip_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    trip = await _load_trip(session, trip_id, company_id)
    expense = await _load_projection_expense(session, trip, company_id)
    # Delete the Expense root.  The FK's ON DELETE CASCADE removes the trip
    # and its rate-adjustment audit rows; never delete child rows manually.
    await session.delete(expense)
    await session.commit()
