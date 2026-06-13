"""Expense service – create, list, read, update, and delete expenses (M8 step 1).

Public API
----------
- ``create_expense``    – validate guards, compute amounts/snapshots, persist.
- ``list_expenses``     – filtered/paginated/sorted list for the company.
- ``get_expense``       – fetch by id + company_id (404 guard).
- ``update_expense``    – recompute amounts/snapshots and write back.
- ``delete_expense``    – delete the expense (DB cascade removes attachments in step 2).

Red-line compliance
-------------------
1. All money logic (gross = net + vat, quantize_to_minor_unit) is here in
   the service.  Schema layer never computes amounts.
2. company_id is always injected by the service; front-end never provides it.
3. No manual cascade deletes – DB ON DELETE CASCADE handles sub-tables.
   (Attachment disk-file cleanup is wired in step 2.)
5. vat_treatment.side == PURCHASE guard – sales-side treatments are rejected.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import VatTreatmentSide
from jai.models.company import Company
from jai.models.dictionary import ExpenseCategory
from jai.models.expense import Expense
from jai.models.vat import VatRate, VatTreatment
from jai.schemas.expense import (
    ExpenseInput,
    ExpenseListItem,
    ExpenseListResponse,
    ExpenseRead,
)
from jai.services.money import quantize_to_minor_unit

# ---------------------------------------------------------------------------
# Pure computation helpers (independently unit-testable, no DB)
# ---------------------------------------------------------------------------


def compute_expense_amounts(
    net: Decimal,
    vat: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Compute (net, vat, gross) quantised to the currency minor unit.

    Both ``net`` and ``vat`` are authority inputs (D8: no validation that
    vat == net × rate).  Each is quantised independently to 2 dp (EUR cents),
    then gross = net + vat is quantised once more.

    Returns
    -------
    (net_q, vat_q, gross_q)
        All three quantised to 2 decimal places (ROUND_HALF_UP).

    Examples
    --------
    >>> from decimal import Decimal
    >>> compute_expense_amounts(Decimal("100"), Decimal("21"))
    (Decimal('100.00'), Decimal('21.00'), Decimal('121.00'))
    >>> # net×rate ≠ vat is accepted (D8)
    >>> compute_expense_amounts(Decimal("99.99"), Decimal("20.99"))
    (Decimal('99.99'), Decimal('20.99'), Decimal('120.98'))
    """
    net_q = quantize_to_minor_unit(net)
    vat_q = quantize_to_minor_unit(vat)
    gross_q = quantize_to_minor_unit(net_q + vat_q)
    return net_q, vat_q, gross_q


def resolve_deductible(
    explicit: bool | None,
    category_default: bool | None,
) -> bool:
    """Resolve the effective deductible flag (D9).

    Priority:
    1. Explicit user input (if not None).
    2. ``category.default_deductible`` (if not None).
    3. Default → ``True``.

    Parameters
    ----------
    explicit:
        The value supplied in ``ExpenseInput.deductible`` (or None if omitted).
    category_default:
        The value of ``ExpenseCategory.default_deductible`` for the chosen
        category (or None if the category has no default set).

    Returns
    -------
    bool
        The effective deductible flag.
    """
    if explicit is not None:
        return explicit
    if category_default is not None:
        return category_default
    return True


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------


async def _load_company(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> Company:
    """Load company by id.  Raises ``LookupError`` if not found."""
    stmt = select(Company).where(Company.id == company_id)
    result = await session.execute(stmt)
    company = result.scalar_one_or_none()
    if company is None:
        raise LookupError("Company not found.")
    return company


async def _load_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    company_id: uuid.UUID,
) -> ExpenseCategory:
    """Load expense category scoped to company.  Raises ``LookupError`` on miss."""
    stmt = select(ExpenseCategory).where(
        ExpenseCategory.id == category_id,
        ExpenseCategory.company_id == company_id,
    )
    result = await session.execute(stmt)
    cat = result.scalar_one_or_none()
    if cat is None:
        raise LookupError("Expense category not found or does not belong to this company.")
    return cat


async def _load_vat_treatment(
    session: AsyncSession,
    treatment_id: uuid.UUID,
    company_id: uuid.UUID,
) -> VatTreatment:
    """Load VAT treatment scoped to company.  Raises on miss or SALES side."""
    stmt = select(VatTreatment).where(
        VatTreatment.id == treatment_id,
        VatTreatment.company_id == company_id,
    )
    result = await session.execute(stmt)
    treatment = result.scalar_one_or_none()
    if treatment is None:
        raise LookupError("VAT treatment not found or does not belong to this company.")
    if VatTreatmentSide(treatment.side) != VatTreatmentSide.PURCHASE:
        raise ValueError(
            "VAT treatment is for the SALES side and cannot be used for an expense. "
            "Please select a PURCHASE-side treatment."
        )
    return treatment


async def _load_vat_rate(
    session: AsyncSession,
    rate_id: uuid.UUID,
    company_id: uuid.UUID,
) -> VatRate:
    """Load VAT rate scoped to company.  Raises ``LookupError`` on miss."""
    stmt = select(VatRate).where(
        VatRate.id == rate_id,
        VatRate.company_id == company_id,
    )
    result = await session.execute(stmt)
    rate = result.scalar_one_or_none()
    if rate is None:
        raise LookupError("VAT rate not found or does not belong to this company.")
    return rate


async def _load_expense(
    session: AsyncSession,
    expense_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Expense:
    """Load an expense scoped to the company (red-line 2).

    Raises ``LookupError`` (→ HTTP 404) if the expense does not exist or
    belongs to a different company.
    """
    stmt = select(Expense).where(
        Expense.id == expense_id,
        Expense.company_id == company_id,
    )
    result = await session.execute(stmt)
    exp = result.scalar_one_or_none()
    if exp is None:
        raise LookupError("Expense not found.")
    return exp


def _expense_to_read(exp: Expense, attachment_count: int = 0) -> ExpenseRead:
    """Convert an ORM Expense to ExpenseRead schema."""
    return ExpenseRead(
        id=exp.id,
        expense_date=exp.expense_date,
        category_id=exp.category_id,
        category_name=exp.category_name,
        supplier_name=exp.supplier_name,
        vat_treatment_id=exp.vat_treatment_id,
        vat_treatment_code=exp.vat_treatment_code,
        vat_treatment_label=exp.vat_treatment_label,
        vat_treatment_effect=exp.vat_treatment_effect,
        vat_rate_id=exp.vat_rate_id,
        vat_rate_percent=Decimal(str(exp.vat_rate_percent)),
        vat_rate_label=exp.vat_rate_label,
        net_amount=Decimal(str(exp.net_amount)),
        vat_amount=Decimal(str(exp.vat_amount)),
        gross_amount=Decimal(str(exp.gross_amount)),
        deductible=exp.deductible,
        currency=exp.currency,
        exchange_rate=Decimal(str(exp.exchange_rate)),
        base_net_amount=Decimal(str(exp.base_net_amount)),
        base_vat_amount=Decimal(str(exp.base_vat_amount)),
        base_gross_amount=Decimal(str(exp.base_gross_amount)),
        reference=exp.reference,
        note=exp.note,
        is_draft=exp.is_draft,
        attachment_count=attachment_count,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
    )


async def _apply_body(
    session: AsyncSession,
    exp: Expense,
    body: ExpenseInput,
    company_id: uuid.UUID,
) -> None:
    """Apply ExpenseInput fields to an Expense ORM object.

    Resolves all FK lookups, snapshots, amounts, and deductible default.
    Mutates ``exp`` in-place.  Does NOT flush or commit.
    """
    # -- Load company for base_currency (D10) ---------------------------------
    company = await _load_company(session, company_id)

    # -- Validate + snapshot category (D12) -----------------------------------
    category = await _load_category(session, body.category_id, company_id)

    # -- Validate + snapshot VAT treatment (D7, PURCHASE guard) ---------------
    treatment = await _load_vat_treatment(session, body.vat_treatment_id, company_id)

    # -- Validate + snapshot VAT rate (D7) ------------------------------------
    rate = await _load_vat_rate(session, body.vat_rate_id, company_id)

    # -- Compute amounts (D11: quantize_to_minor_unit) -------------------------
    net_q, vat_q, gross_q = compute_expense_amounts(body.net_amount, body.vat_amount)

    # -- Resolve deductible default (D9) ----------------------------------------
    effective_deductible = resolve_deductible(body.deductible, category.default_deductible)

    # -- Apply all fields to ORM object ----------------------------------------
    exp.company_id = company_id
    exp.expense_date = body.expense_date

    exp.category_id = category.id
    exp.category_name = category.name

    exp.supplier_name = body.supplier_name

    exp.vat_treatment_id = treatment.id
    exp.vat_treatment_code = str(treatment.code)
    exp.vat_treatment_label = str(treatment.label)
    exp.vat_treatment_effect = str(treatment.effect)

    exp.vat_rate_id = rate.id
    exp.vat_rate_percent = rate.percent
    exp.vat_rate_label = str(rate.label)

    exp.net_amount = net_q
    exp.vat_amount = vat_q
    exp.gross_amount = gross_q
    exp.deductible = effective_deductible

    # D10: single base currency – currency = company.base_currency, exchange_rate = 1
    exp.currency = company.base_currency
    exp.exchange_rate = Decimal("1")
    exp.base_net_amount = net_q
    exp.base_vat_amount = vat_q
    exp.base_gross_amount = gross_q

    exp.reference = body.reference
    exp.note = body.note


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def create_expense(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: ExpenseInput,
    creator_id: uuid.UUID | None,
) -> ExpenseRead:
    """Create a new expense record.

    Guards:
    - D12: category must belong to this company.
    - D7 + PURCHASE guard: treatment must belong to this company and be
      side == PURCHASE (sales-side treatment → 422).
    - D7: rate must belong to this company.
    - D10: currency = company.base_currency, exchange_rate = 1.
    - D11: net/vat quantised to minor unit; gross = net + vat.
    - D9: deductible defaults via category.default_deductible → True.
    """
    exp = Expense()
    exp.is_draft = False
    exp.creator_id = creator_id
    await _apply_body(session, exp, body, company_id)
    session.add(exp)
    await session.commit()
    await session.refresh(exp)
    return _expense_to_read(exp)


async def get_expense(
    session: AsyncSession,
    expense_id: uuid.UUID,
    company_id: uuid.UUID,
) -> ExpenseRead:
    """Fetch a single expense by id scoped to company.

    Raises ``LookupError`` (→ HTTP 404) if not found or cross-company.
    """
    exp = await _load_expense(session, expense_id, company_id)
    return _expense_to_read(exp)


async def update_expense(
    session: AsyncSession,
    expense_id: uuid.UUID,
    company_id: uuid.UUID,
    body: ExpenseInput,
) -> ExpenseRead:
    """Edit an expense.  Same guards as create_expense.

    Also supports transitioning ``is_draft`` from True to False (confirm a
    recurring-expense draft) by calling PUT with ``is_draft`` not in the body
    – caller must set it on the ORM object if needed.  The service does NOT
    reset is_draft here; it preserves the existing value unless overridden.
    """
    exp = await _load_expense(session, expense_id, company_id)
    # Preserve is_draft: the PUT endpoint may not change this; step 3 route adds
    # explicit confirm logic.
    await _apply_body(session, exp, body, company_id)
    await session.commit()
    await session.refresh(exp)
    return _expense_to_read(exp)


async def delete_expense(
    session: AsyncSession,
    expense_id: uuid.UUID,
    company_id: uuid.UUID,
) -> None:
    """Delete an expense.

    DB ON DELETE CASCADE removes expense_attachment rows (red-line 3).
    Disk-file cleanup for attachments is wired in step 2.
    """
    exp = await _load_expense(session, expense_id, company_id)
    await session.delete(exp)
    await session.commit()


async def list_expenses(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    vat_treatment_id: uuid.UUID | None = None,
    deductible: bool | None = None,
    is_draft: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "expense_date",
) -> ExpenseListResponse:
    """Return a paginated expenses list for the company.

    Mirrors the filter/pagination/sort contract of ``services.payment.list_payments``.

    Filters
    -------
    q
        ilike search against ``supplier_name``, ``reference``, and ``category_name``.
    category_id
        Exact match on ``expense.category_id``.
    vat_treatment_id
        Exact match on ``expense.vat_treatment_id``.
    deductible
        Exact match on ``expense.deductible``.
    is_draft
        Exact match on ``expense.is_draft``.
    date_from / date_to
        Inclusive range on ``expense_date``.

    Sort
    ----
    sort_by in {"expense_date", "created_at"}; default "expense_date".
    Primary sort DESC (newest first).

    All results are scoped to company_id (red-line 2).
    """
    base = select(Expense).where(Expense.company_id == company_id)

    # -- Filters ---------------------------------------------------------------
    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                Expense.supplier_name.ilike(like),
                Expense.reference.ilike(like),
                Expense.category_name.ilike(like),
            )
        )
    if category_id is not None:
        base = base.where(Expense.category_id == category_id)
    if vat_treatment_id is not None:
        base = base.where(Expense.vat_treatment_id == vat_treatment_id)
    if deductible is not None:
        base = base.where(Expense.deductible == deductible)
    if is_draft is not None:
        base = base.where(Expense.is_draft == is_draft)
    if date_from is not None:
        base = base.where(Expense.expense_date >= date_from)
    if date_to is not None:
        base = base.where(Expense.expense_date <= date_to)

    # -- Count (total, unaffected by pagination) --------------------------------
    count_stmt = select(func.count()).select_from(base.subquery())
    count_result = await session.execute(count_stmt)
    total = count_result.scalar_one()

    # -- Sort ------------------------------------------------------------------
    sort_primary: object
    sort_secondary: object
    if sort_by == "created_at":
        sort_primary = Expense.created_at.desc()
        sort_secondary = Expense.expense_date.desc()
    else:
        # Default: expense_date (newest first); stable secondary: created_at
        sort_primary = Expense.expense_date.desc()
        sort_secondary = Expense.created_at.desc()

    data_stmt = base.order_by(sort_primary, sort_secondary).limit(limit).offset(offset)
    data_result = await session.execute(data_stmt)
    expenses = list(data_result.scalars().all())

    items: list[ExpenseListItem] = [
        ExpenseListItem(
            id=exp.id,
            expense_date=exp.expense_date,
            category_name=exp.category_name,
            supplier_name=exp.supplier_name,
            net_amount=Decimal(str(exp.net_amount)),
            vat_amount=Decimal(str(exp.vat_amount)),
            gross_amount=Decimal(str(exp.gross_amount)),
            deductible=exp.deductible,
            is_draft=exp.is_draft,
            attachment_count=0,  # step 2 will populate this
        )
        for exp in expenses
    ]

    return ExpenseListResponse(items=items, total=total)
