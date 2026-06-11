"""Invoice service – CRUD + status transitions + product options (M5 step 3).

Public API
----------
- ``create_invoice``          – allocate number, calculate, persist (within transaction)
- ``update_invoice``          – recalculate + replace sub-tables (preserves number)
- ``delete_invoice``          – DB cascade removes lines/taxes; number not recycled
- ``get_invoice``             – fetch by id + company_id (returns None if not found)
- ``list_invoices``           – filtered/paginated list
- ``transition_status``       – M5 lifecycle state machine (DRAFT/SENT/CANCELLED)
- ``list_invoice_product_options`` – customer-safe product projection

Red-line compliance
-------------------
1. All money calculation is delegated to ``services.pricing``; this module only
   persists results.
2. company_id is always injected by the service; front-end never provides it.
3. No manual cascade deletes – DB ON DELETE CASCADE handles sub-tables.
4. Numbering via ``services.numbering.allocate_invoice_number`` (row-locked).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jai.models._enums import (
    DiscountType,
    InvoicePaidStatus,
    InvoiceStatus,
    InvoiceTaxMode,
    SettingLevel,
    VatTreatmentEffect,
    VatTreatmentSide,
)
from jai.models.customer import Customer
from jai.models.dictionary import Unit
from jai.models.invoice import Invoice, InvoiceLine, InvoiceLineTax, InvoiceTax
from jai.models.product import Product
from jai.models.quote import Quote as _Quote
from jai.models.quote import QuoteLine as _QuoteLine
from jai.models.vat import VatRate, VatTreatment
from jai.schemas.invoice import (
    InvoiceLineRead,
    InvoiceLineReadTax,
    InvoiceListItem,
    InvoiceListResponse,
    InvoiceRead,
    InvoiceStatusWrite,
    InvoiceTaxRowRead,
    InvoiceWrite,
    ProductInvoiceOptionListResponse,
    ProductInvoiceOptionRead,
    VatTreatmentSnapshot,
)
from jai.schemas.setting import SETTING_KEY_INVOICE_NUMBERING, InvoiceNumberingConfig
from jai.services.money import quantize_money
from jai.services.numbering import allocate_invoice_number
from jai.services.pricing import (
    _derive_treatment_from_customer,
    compute_pricing,
)
from jai.services.settings import get_setting

# ---------------------------------------------------------------------------
# Allowed status transitions for M5
# ---------------------------------------------------------------------------

_ALLOWED_TRANSITIONS: dict[InvoiceStatus, set[InvoiceStatus]] = {
    InvoiceStatus.DRAFT: {InvoiceStatus.SENT, InvoiceStatus.CANCELLED},
    # Once sent, the invoice is a formal document; cancellation must be done via
    # a credit note (M9/M10).  No direct SENT→CANCELLED transition.
    InvoiceStatus.SENT: set(),
    # COMPLETED is driven by payments (M7); cannot be set manually in M5.
    InvoiceStatus.COMPLETED: set(),
    # Only DRAFT invoices can be cancelled, so reactivating back to DRAFT is safe.
    InvoiceStatus.CANCELLED: {InvoiceStatus.DRAFT},
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_numbering_config(
    session: AsyncSession,
    company_id: uuid.UUID,
) -> InvoiceNumberingConfig:
    """Read InvoiceNumberingConfig from COMPANY-level settings, falling back to default."""
    config = await get_setting(
        session,
        SETTING_KEY_INVOICE_NUMBERING,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=InvoiceNumberingConfig,
    )
    return config or InvoiceNumberingConfig()


async def _resolve_treatment(
    session: AsyncSession,
    company_id: uuid.UUID,
    customer: Customer,
    vat_treatment_id: uuid.UUID | None,
) -> VatTreatment:
    """Resolve or derive the VAT treatment; raises ValueError on failure."""
    if vat_treatment_id is not None:
        stmt = select(VatTreatment).where(
            VatTreatment.id == vat_treatment_id,
            VatTreatment.company_id == company_id,
            VatTreatment.side == VatTreatmentSide.SALES,
            VatTreatment.active == True,  # noqa: E712
        )
        result = await session.execute(stmt)
        treatment = result.scalar_one_or_none()
        if treatment is None:
            raise ValueError(
                "VAT treatment not found, inactive, or not a sales treatment."
            )
        return treatment

    treatment = await _derive_treatment_from_customer(session, company_id, customer)
    if treatment is None:
        raise ValueError(
            "Could not derive a default VAT treatment for this customer. "
            "Please select one manually."
        )
    return treatment


async def _load_vat_rates(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: InvoiceWrite,
) -> dict[uuid.UUID, tuple[str, Decimal]]:
    """Batch-load VAT rates needed for the request; validate ownership.

    LINE mode:   every line vat_rate_id is required and drives calculation.
    DOCUMENT mode: document_vat_rate_id drives calculation; line-level vat_rate_ids
                   are optional draft-defaults that are persisted but not used in
                   calculation – they must still belong to this company.
    """
    required_ids: set[uuid.UUID] = set()
    optional_ids: set[uuid.UUID] = set()

    if body.tax_mode == InvoiceTaxMode.LINE:
        for line in body.lines:
            if line.vat_rate_id is None:
                raise ValueError(
                    f"Line '{line.name}': vat_rate_id is required in LINE tax mode."
                )
            required_ids.add(line.vat_rate_id)
    else:
        if body.document_vat_rate_id is None:
            raise ValueError("document_vat_rate_id is required in DOCUMENT tax mode.")
        required_ids.add(body.document_vat_rate_id)
        # Collect optional per-line rates (draft defaults); validate ownership if present.
        for line in body.lines:
            if line.vat_rate_id is not None:
                optional_ids.add(line.vat_rate_id)

    all_ids = required_ids | optional_ids
    stmt = select(VatRate).where(
        VatRate.id.in_(all_ids),
        VatRate.company_id == company_id,
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    vat_rates: dict[uuid.UUID, tuple[str, Decimal]] = {
        r.id: (r.label, Decimal(str(r.percent))) for r in rows
    }

    # Every ID (required and optional) must be found within this company.
    for rate_id in all_ids:
        if rate_id not in vat_rates:
            raise ValueError(f"VAT rate {rate_id} not found or not in this company.")

    return vat_rates


async def _validate_line_fks(
    session: AsyncSession,
    company_id: uuid.UUID,
    body: InvoiceWrite,
) -> None:
    """Validate all line-level optional FKs (product_id, unit_id) belong to this company.

    Raises ValueError when any referenced ID is absent or belongs to another company.
    """
    product_ids = {line.product_id for line in body.lines if line.product_id is not None}
    unit_ids = {line.unit_id for line in body.lines if line.unit_id is not None}

    if product_ids:
        count_stmt = select(func.count()).where(
            Product.id.in_(product_ids),
            Product.company_id == company_id,
        )
        found = (await session.execute(count_stmt)).scalar_one()
        if found != len(product_ids):
            raise ValueError(
                "One or more product IDs not found or do not belong to this company."
            )

    if unit_ids:
        count_stmt = select(func.count()).where(
            Unit.id.in_(unit_ids),
            Unit.company_id == company_id,
        )
        found = (await session.execute(count_stmt)).scalar_one()
        if found != len(unit_ids):
            raise ValueError(
                "One or more unit IDs not found or do not belong to this company."
            )


def _line_to_read(line: InvoiceLine) -> InvoiceLineRead:
    return InvoiceLineRead(
        id=line.id,
        sort_order=line.sort_order,
        product_id=line.product_id,
        name=line.name,
        description=line.description,
        quantity=Decimal(str(line.quantity)),
        unit_id=line.unit_id,
        unit_name=line.unit_name,
        unit_price=Decimal(str(line.unit_price)),
        discount_type=DiscountType(line.discount_type),
        discount_value=Decimal(str(line.discount_value)),
        vat_rate_id=line.vat_rate_id,
        vat_rate_label=line.vat_rate_label,
        vat_rate_percent=(
            Decimal(str(line.vat_rate_percent)) if line.vat_rate_percent is not None else None
        ),
        subtotal_excl_vat=Decimal(str(line.subtotal_excl_vat)),
        subtotal_incl_vat=Decimal(str(line.subtotal_incl_vat)),
        line_discount_amount=Decimal(str(line.line_discount_amount)),
        document_discount_share=Decimal(str(line.document_discount_share)),
        taxable_amount=Decimal(str(line.taxable_amount)),
        vat_total=Decimal(str(line.vat_total)),
        total_incl_vat=Decimal(str(line.total_incl_vat)),
        line_taxes=[
            InvoiceLineReadTax(
                id=lt.id,
                vat_rate_id=lt.vat_rate_id,
                vat_rate_label=lt.vat_rate_label,
                vat_rate_percent=Decimal(str(lt.vat_rate_percent)),
                effective_vat_percent=Decimal(str(lt.effective_vat_percent)),
                taxable_amount=Decimal(str(lt.taxable_amount)),
                tax_amount=Decimal(str(lt.tax_amount)),
            )
            for lt in line.line_taxes
        ],
    )


def _invoice_to_read(inv: Invoice) -> InvoiceRead:
    treatment_snapshot = VatTreatmentSnapshot(
        id=inv.vat_treatment_id,
        code=inv.vat_treatment_code,
        label=inv.vat_treatment_label,
        effect=inv.vat_treatment_effect,
        requires_icp=inv.vat_treatment_requires_icp,
    )
    return InvoiceRead(
        id=inv.id,
        company_id=inv.company_id,
        customer_id=inv.customer_id,
        invoice_number=inv.invoice_number,
        sequence_number=inv.sequence_number,
        customer_sequence_number=inv.customer_sequence_number,
        unique_hash=inv.unique_hash,
        reference_number=inv.reference_number,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        status=InvoiceStatus(inv.status),
        paid_status=InvoicePaidStatus(inv.paid_status),
        currency=inv.currency,
        exchange_rate=Decimal(str(inv.exchange_rate)),
        tax_mode=InvoiceTaxMode(inv.tax_mode),
        amounts_include_vat=inv.amounts_include_vat,
        vat_treatment_id=inv.vat_treatment_id,
        document_vat_rate_id=inv.document_vat_rate_id,
        vat_treatment_snapshot=treatment_snapshot,
        discount_type=DiscountType(inv.discount_type),
        discount_value=Decimal(str(inv.discount_value)),
        document_discount_amount=Decimal(str(inv.document_discount_amount)),
        subtotal_excl_vat=Decimal(str(inv.subtotal_excl_vat)),
        line_discount_total=Decimal(str(inv.line_discount_total)),
        taxable_amount=Decimal(str(inv.taxable_amount)),
        vat_total=Decimal(str(inv.vat_total)),
        total_incl_vat=Decimal(str(inv.total_incl_vat)),
        due_amount=Decimal(str(inv.due_amount)),
        base_subtotal_excl_vat=Decimal(str(inv.base_subtotal_excl_vat)),
        base_line_discount_total=Decimal(str(inv.base_line_discount_total)),
        base_taxable_amount=Decimal(str(inv.base_taxable_amount)),
        base_vat_total=Decimal(str(inv.base_vat_total)),
        base_total_incl_vat=Decimal(str(inv.base_total_incl_vat)),
        base_due_amount=Decimal(str(inv.base_due_amount)),
        notes=inv.notes,
        warranty_text=inv.warranty_text,
        terms_text=inv.terms_text,
        bank_text=inv.bank_text,
        payment_terms_text=inv.payment_terms_text,
        creator_id=inv.creator_id,
        lines=[_line_to_read(line) for line in inv.lines],
        taxes=[
            InvoiceTaxRowRead(
                id=t.id,
                vat_rate_id=t.vat_rate_id,
                vat_rate_label=t.vat_rate_label,
                vat_rate_percent=Decimal(str(t.vat_rate_percent)),
                effective_vat_percent=Decimal(str(t.effective_vat_percent)),
                taxable_amount=Decimal(str(t.taxable_amount)),
                tax_amount=Decimal(str(t.tax_amount)),
            )
            for t in inv.taxes
        ],
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


def _invoice_to_list_item(inv: Invoice, *, customer_name: str) -> InvoiceListItem:
    return InvoiceListItem(
        id=inv.id,
        company_id=inv.company_id,
        customer_id=inv.customer_id,
        customer_name=customer_name,
        invoice_number=inv.invoice_number,
        reference_number=inv.reference_number,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        status=InvoiceStatus(inv.status),
        paid_status=InvoicePaidStatus(inv.paid_status),
        currency=inv.currency,
        total_incl_vat=Decimal(str(inv.total_incl_vat)),
        due_amount=Decimal(str(inv.due_amount)),
        vat_treatment_snapshot=VatTreatmentSnapshot(
            id=inv.vat_treatment_id,
            code=inv.vat_treatment_code,
            label=inv.vat_treatment_label,
            effect=inv.vat_treatment_effect,
            requires_icp=inv.vat_treatment_requires_icp,
        ),
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


# ---------------------------------------------------------------------------
# Core calculation + persistence helpers
# ---------------------------------------------------------------------------


async def _build_and_persist_invoice(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    company_currency: str,
    creator_id: uuid.UUID | None,
    body: InvoiceWrite,
    customer: Customer,
    treatment: VatTreatment,
    vat_rates: dict[uuid.UUID, tuple[str, Decimal]],
    # For update: provide existing number; for create: None → allocate
    invoice_number: str | None = None,
    sequence_number: int | None = None,
    customer_sequence_number: int | None = None,
    existing_invoice: Invoice | None = None,
) -> Invoice:
    """Calculate pricing and persist a new or updated invoice.

    For creates: invoice_number/sequence_number must be None; they are
    allocated inside this function.
    For updates: invoice_number/sequence_number must be provided (kept).
    """
    request_currency = body.currency or company_currency
    if request_currency != company_currency:
        raise ValueError(
            f"M5 only supports invoices in company base currency "
            f"({company_currency}); got {request_currency}."
        )

    treatment_effect = VatTreatmentEffect(treatment.effect)

    document_vat_rate: tuple[str, Decimal] | None = None
    if body.tax_mode == InvoiceTaxMode.DOCUMENT:
        rate_info = vat_rates.get(body.document_vat_rate_id)  # type: ignore[arg-type]
        document_vat_rate = rate_info

    # Pure pricing calculation
    calc = compute_pricing(
        body.lines,
        tax_mode=body.tax_mode,
        amounts_include_vat=body.amounts_include_vat,
        discount=body.discount,
        treatment_effect=treatment_effect,
        vat_rates=vat_rates,
        document_vat_rate=document_vat_rate,
    )

    # Fix document tax vat_rate_id placeholder
    if body.tax_mode == InvoiceTaxMode.DOCUMENT and calc.document_taxes:
        calc.document_taxes[0].vat_rate_id = body.document_vat_rate_id  # type: ignore[assignment]

    # Allocate number (create only)
    if invoice_number is None:
        numbering_config = await _load_numbering_config(session, company_id)
        invoice_number, sequence_number, customer_sequence_number = (
            await allocate_invoice_number(
                session,
                company_id,
                customer.id,
                body.invoice_date,
                numbering_config=numbering_config,
                customer_invoice_prefix=customer.invoice_prefix,
            )
        )

    total_incl_vat = quantize_money(calc.total_incl_vat)

    # Determine treatment effect string for snapshot
    treatment_effect_str = (
        treatment.effect.value
        if isinstance(treatment.effect, VatTreatmentEffect)
        else str(treatment.effect)
    )

    # Common money values
    subtotal_excl_vat = quantize_money(calc.subtotal_excl_vat)
    line_discount_total = quantize_money(calc.line_discount_total)
    taxable_amount = quantize_money(calc.taxable_amount)
    vat_total = quantize_money(calc.vat_total)
    doc_discount_amount = quantize_money(calc.document_discount_amount)

    # Build or update Invoice row
    if existing_invoice is not None:
        inv = existing_invoice
        # Replace sub-tables: delete old lines (DB CASCADE removes line_taxes)
        # and old document taxes via explicit DELETE (avoids ORM selectin load).
        await session.execute(
            delete(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
        )
        await session.execute(
            delete(InvoiceTax).where(InvoiceTax.invoice_id == inv.id)
        )
    else:
        inv = Invoice()
        inv.company_id = company_id
        inv.invoice_number = invoice_number
        inv.sequence_number = sequence_number  # type: ignore[assignment]
        inv.customer_sequence_number = customer_sequence_number
        inv.status = InvoiceStatus.DRAFT
        inv.paid_status = InvoicePaidStatus.UNPAID
        inv.creator_id = creator_id

    # Set all fields before any flush
    inv.customer_id = customer.id
    inv.reference_number = body.reference_number
    inv.invoice_date = body.invoice_date
    inv.due_date = body.due_date
    inv.currency = request_currency
    inv.exchange_rate = Decimal("1")

    inv.tax_mode = body.tax_mode
    inv.amounts_include_vat = body.amounts_include_vat
    inv.vat_treatment_id = treatment.id
    # LINE mode must never persist a document-level rate; clear any stale value.
    inv.document_vat_rate_id = (
        body.document_vat_rate_id if body.tax_mode == InvoiceTaxMode.DOCUMENT else None
    )

    inv.vat_treatment_code = treatment.code
    inv.vat_treatment_label = treatment.label
    inv.vat_treatment_effect = treatment_effect_str
    inv.vat_treatment_requires_icp = treatment.requires_icp

    inv.discount_type = body.discount.type
    inv.discount_value = body.discount.value
    inv.document_discount_amount = doc_discount_amount

    inv.subtotal_excl_vat = subtotal_excl_vat
    inv.line_discount_total = line_discount_total
    inv.taxable_amount = taxable_amount
    inv.vat_total = vat_total
    inv.total_incl_vat = total_incl_vat
    inv.due_amount = total_incl_vat  # M5: full amount owed until M7

    # M5: exchange_rate = 1; base_* = *
    inv.base_subtotal_excl_vat = subtotal_excl_vat
    inv.base_line_discount_total = line_discount_total
    inv.base_taxable_amount = taxable_amount
    inv.base_vat_total = vat_total
    inv.base_total_incl_vat = total_incl_vat
    inv.base_due_amount = total_incl_vat

    inv.notes = body.notes

    # -- Content block snapshot text (M6 step 4) -----------------------------
    inv.warranty_text = body.warranty_text
    inv.terms_text = body.terms_text
    inv.bank_text = body.bank_text
    inv.payment_terms_text = body.payment_terms_text

    # Persist invoice (create: add + flush to get ID; update: just flush)
    if existing_invoice is None:
        session.add(inv)
    await session.flush()  # get inv.id for child FKs

    # Build line rows - avoid ORM relationship append to prevent selectin triggers;
    # set FK directly and add to session.
    new_line_rows: list[InvoiceLine] = []
    for i, (line_input, line_calc) in enumerate(zip(body.lines, calc.lines, strict=True)):
        line_row = InvoiceLine()
        line_row.invoice_id = inv.id
        line_row.sort_order = i
        line_row.product_id = line_input.product_id
        line_row.name = line_input.name
        line_row.description = line_input.description
        line_row.quantity = line_input.quantity
        line_row.unit_id = line_input.unit_id
        line_row.unit_name = line_input.unit_name
        line_row.unit_price = line_input.unit_price
        line_row.discount_type = line_input.discount.type
        line_row.discount_value = line_input.discount.value
        line_row.vat_rate_id = line_input.vat_rate_id

        if body.tax_mode == InvoiceTaxMode.LINE and line_input.vat_rate_id is not None:
            rate_label, rate_percent = vat_rates[line_input.vat_rate_id]
            line_row.vat_rate_label = rate_label
            line_row.vat_rate_percent = rate_percent
        else:
            line_row.vat_rate_label = None
            line_row.vat_rate_percent = None

        line_row.subtotal_excl_vat = quantize_money(line_calc.subtotal_excl_vat)
        line_row.subtotal_incl_vat = quantize_money(line_calc.subtotal_incl_vat)
        line_row.line_discount_amount = quantize_money(line_calc.line_discount_amount)
        line_row.document_discount_share = quantize_money(
            line_calc.document_discount_share
        )
        line_row.taxable_amount = quantize_money(line_calc.taxable_amount)
        line_row.vat_total = quantize_money(line_calc.vat_total)
        line_row.total_incl_vat = quantize_money(line_calc.total_incl_vat)

        session.add(line_row)
        new_line_rows.append(line_row)

    await session.flush()  # get line row IDs

    # Build per-line tax rows (LINE mode)
    if body.tax_mode == InvoiceTaxMode.LINE:
        for line_row, lt in zip(new_line_rows, calc.line_taxes, strict=False):
            lt_row = InvoiceLineTax()
            lt_row.invoice_line_id = line_row.id
            lt_row.vat_rate_id = lt.vat_rate_id
            lt_row.vat_rate_label = lt.vat_rate_label
            lt_row.vat_rate_percent = lt.vat_rate_percent
            lt_row.effective_vat_percent = lt.effective_vat_percent
            lt_row.taxable_amount = lt.taxable_amount
            lt_row.tax_amount = lt.tax_amount
            session.add(lt_row)

    # Build document tax rows (DOCUMENT mode)
    if body.tax_mode == InvoiceTaxMode.DOCUMENT:
        for dt in calc.document_taxes:
            tax_row = InvoiceTax()
            tax_row.invoice_id = inv.id
            tax_row.vat_rate_id = dt.vat_rate_id
            tax_row.vat_rate_label = dt.vat_rate_label
            tax_row.vat_rate_percent = dt.vat_rate_percent
            tax_row.effective_vat_percent = dt.effective_vat_percent
            tax_row.taxable_amount = dt.taxable_amount
            tax_row.tax_amount = dt.tax_amount
            session.add(tax_row)

    await session.flush()
    return inv


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def clone_quote_to_invoice(
    session: AsyncSession,
    quote: _Quote,
    *,
    company_id: uuid.UUID,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    """Build an Invoice by copying the quote's stored snapshots verbatim.

    No pricing re-computation occurs.  All amounts, VAT rate labels/percentages,
    and the treatment snapshot are cloned directly from the quote's persisted
    rows.  Only a fresh invoice number is allocated.

    The caller owns the ``session.commit()`` so that invoice creation and the
    quote's ``converted_invoice_id`` back-link update are atomic.
    """
    # Need customer only for customer_invoice_prefix (invoice numbering).
    cust_stmt = select(Customer).where(Customer.id == quote.customer_id)
    cust_result = await session.execute(cust_stmt)
    customer = cust_result.scalar_one()

    numbering_config = await _load_numbering_config(session, company_id)
    invoice_number, sequence_number, customer_sequence_number = (
        await allocate_invoice_number(
            session,
            company_id,
            customer.id,
            date.today(),
            numbering_config=numbering_config,
            customer_invoice_prefix=customer.invoice_prefix,
        )
    )

    inv = Invoice()
    inv.company_id = company_id
    inv.customer_id = quote.customer_id
    inv.invoice_number = invoice_number
    inv.sequence_number = sequence_number
    inv.customer_sequence_number = customer_sequence_number
    inv.reference_number = quote.reference_number
    inv.invoice_date = date.today()
    inv.due_date = None
    inv.status = InvoiceStatus.DRAFT
    inv.paid_status = InvoicePaidStatus.UNPAID
    inv.currency = quote.currency
    inv.exchange_rate = Decimal(str(quote.exchange_rate))
    inv.tax_mode = InvoiceTaxMode(quote.tax_mode)
    inv.amounts_include_vat = quote.amounts_include_vat
    inv.vat_treatment_id = quote.vat_treatment_id
    inv.document_vat_rate_id = quote.document_vat_rate_id
    # Copy treatment snapshot verbatim — no re-resolution against current dictionary
    inv.vat_treatment_code = quote.vat_treatment_code
    inv.vat_treatment_label = quote.vat_treatment_label
    inv.vat_treatment_effect = quote.vat_treatment_effect
    inv.vat_treatment_requires_icp = quote.vat_treatment_requires_icp
    # Copy discount
    inv.discount_type = DiscountType(quote.discount_type)
    inv.discount_value = Decimal(str(quote.discount_value))
    inv.document_discount_amount = Decimal(str(quote.document_discount_amount))
    # Copy all computed amounts verbatim
    inv.subtotal_excl_vat = Decimal(str(quote.subtotal_excl_vat))
    inv.line_discount_total = Decimal(str(quote.line_discount_total))
    inv.taxable_amount = Decimal(str(quote.taxable_amount))
    inv.vat_total = Decimal(str(quote.vat_total))
    inv.total_incl_vat = Decimal(str(quote.total_incl_vat))
    inv.due_amount = Decimal(str(quote.total_incl_vat))
    inv.base_subtotal_excl_vat = Decimal(str(quote.base_subtotal_excl_vat))
    inv.base_line_discount_total = Decimal(str(quote.base_line_discount_total))
    inv.base_taxable_amount = Decimal(str(quote.base_taxable_amount))
    inv.base_vat_total = Decimal(str(quote.base_vat_total))
    inv.base_total_incl_vat = Decimal(str(quote.base_total_incl_vat))
    inv.base_due_amount = Decimal(str(quote.base_total_incl_vat))
    inv.notes = quote.notes
    inv.warranty_text = quote.warranty_text
    inv.terms_text = quote.terms_text
    inv.bank_text = quote.bank_text
    inv.payment_terms_text = quote.payment_terms_text
    inv.creator_id = creator_id

    session.add(inv)
    await session.flush()  # get inv.id for child FKs

    # Clone line rows
    line_pairs: list[tuple[_QuoteLine, InvoiceLine]] = []
    for q_line in quote.lines:
        inv_line = InvoiceLine()
        inv_line.invoice_id = inv.id
        inv_line.sort_order = q_line.sort_order
        inv_line.product_id = q_line.product_id
        inv_line.name = q_line.name
        inv_line.description = q_line.description
        inv_line.quantity = Decimal(str(q_line.quantity))
        inv_line.unit_id = q_line.unit_id
        inv_line.unit_name = q_line.unit_name
        inv_line.unit_price = Decimal(str(q_line.unit_price))
        inv_line.discount_type = DiscountType(q_line.discount_type)
        inv_line.discount_value = Decimal(str(q_line.discount_value))
        inv_line.vat_rate_id = q_line.vat_rate_id
        inv_line.vat_rate_label = q_line.vat_rate_label
        inv_line.vat_rate_percent = (
            Decimal(str(q_line.vat_rate_percent))
            if q_line.vat_rate_percent is not None
            else None
        )
        inv_line.subtotal_excl_vat = Decimal(str(q_line.subtotal_excl_vat))
        inv_line.subtotal_incl_vat = Decimal(str(q_line.subtotal_incl_vat))
        inv_line.line_discount_amount = Decimal(str(q_line.line_discount_amount))
        inv_line.document_discount_share = Decimal(str(q_line.document_discount_share))
        inv_line.taxable_amount = Decimal(str(q_line.taxable_amount))
        inv_line.vat_total = Decimal(str(q_line.vat_total))
        inv_line.total_incl_vat = Decimal(str(q_line.total_incl_vat))
        session.add(inv_line)
        line_pairs.append((q_line, inv_line))

    await session.flush()  # get inv_line.id for child FKs

    # Clone per-line taxes (LINE mode)
    for q_line, inv_line in line_pairs:
        for q_lt in q_line.line_taxes:
            inv_lt = InvoiceLineTax()
            inv_lt.invoice_line_id = inv_line.id
            inv_lt.vat_rate_id = q_lt.vat_rate_id
            inv_lt.vat_rate_label = q_lt.vat_rate_label
            inv_lt.vat_rate_percent = Decimal(str(q_lt.vat_rate_percent))
            inv_lt.effective_vat_percent = Decimal(str(q_lt.effective_vat_percent))
            inv_lt.taxable_amount = Decimal(str(q_lt.taxable_amount))
            inv_lt.tax_amount = Decimal(str(q_lt.tax_amount))
            session.add(inv_lt)

    # Clone document-level taxes (DOCUMENT mode)
    for q_tax in quote.taxes:
        inv_tax = InvoiceTax()
        inv_tax.invoice_id = inv.id
        inv_tax.vat_rate_id = q_tax.vat_rate_id
        inv_tax.vat_rate_label = q_tax.vat_rate_label
        inv_tax.vat_rate_percent = Decimal(str(q_tax.vat_rate_percent))
        inv_tax.effective_vat_percent = Decimal(str(q_tax.effective_vat_percent))
        inv_tax.taxable_amount = Decimal(str(q_tax.taxable_amount))
        inv_tax.tax_amount = Decimal(str(q_tax.tax_amount))
        session.add(inv_tax)

    await session.flush()
    # Use an explicit SELECT with nested selectinload so that inv.lines and
    # line.line_taxes are loaded in the async context before _invoice_to_read
    # accesses them synchronously.  A plain session.refresh(inv) only loads
    # inv.lines (one level deep) and leaves line.line_taxes unloaded.
    load_stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.lines).selectinload(InvoiceLine.line_taxes),
            selectinload(Invoice.taxes),
        )
        .where(Invoice.id == inv.id)
    )
    result = await session.execute(load_stmt)
    return _invoice_to_read(result.scalar_one())


async def create_invoice(
    session: AsyncSession,
    body: InvoiceWrite,
    company_id: uuid.UUID,
    company_currency: str,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    """Create a new invoice: allocate number, calculate, persist."""
    # Validate customer belongs to this company
    cust_stmt = select(Customer).where(
        Customer.id == body.customer_id,
        Customer.company_id == company_id,
    )
    cust_result = await session.execute(cust_stmt)
    customer = cust_result.scalar_one_or_none()
    if customer is None:
        raise ValueError("Customer not found or does not belong to this company.")

    treatment = await _resolve_treatment(
        session, company_id, customer, body.vat_treatment_id
    )
    vat_rates = await _load_vat_rates(session, company_id, body)
    await _validate_line_fks(session, company_id, body)

    inv = await _build_and_persist_invoice(
        session,
        company_id=company_id,
        company_currency=company_currency,
        creator_id=creator_id,
        body=body,
        customer=customer,
        treatment=treatment,
        vat_rates=vat_rates,
    )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError("Invoice number already exists (concurrent creation).") from exc

    await session.refresh(inv)
    return _invoice_to_read(inv)


async def get_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
) -> InvoiceRead | None:
    """Return a full InvoiceRead, or None if not found / wrong company."""
    stmt = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.company_id == company_id,
    )
    result = await session.execute(stmt)
    inv = result.scalar_one_or_none()
    if inv is None:
        return None
    return _invoice_to_read(inv)


async def update_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    body: InvoiceWrite,
    company_id: uuid.UUID,
    company_currency: str,
) -> InvoiceRead | None:
    """Update an existing invoice: preserve number, recalculate, replace sub-tables."""
    stmt = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.company_id == company_id,
    )
    result = await session.execute(stmt)
    inv = result.scalar_one_or_none()
    if inv is None:
        return None

    # Only DRAFT invoices can be fully edited; SENT is a locked formal document.
    if inv.status != InvoiceStatus.DRAFT:
        raise ValueError(
            f"Cannot edit a {inv.status.value.lower()} invoice. "
            "Only DRAFT invoices may be modified."
        )

    # Validate customer
    cust_stmt = select(Customer).where(
        Customer.id == body.customer_id,
        Customer.company_id == company_id,
    )
    cust_result = await session.execute(cust_stmt)
    customer = cust_result.scalar_one_or_none()
    if customer is None:
        raise ValueError("Customer not found or does not belong to this company.")

    treatment = await _resolve_treatment(
        session, company_id, customer, body.vat_treatment_id
    )
    vat_rates = await _load_vat_rates(session, company_id, body)
    await _validate_line_fks(session, company_id, body)

    updated = await _build_and_persist_invoice(
        session,
        company_id=company_id,
        company_currency=company_currency,
        creator_id=inv.creator_id,
        body=body,
        customer=customer,
        treatment=treatment,
        vat_rates=vat_rates,
        invoice_number=inv.invoice_number,
        sequence_number=inv.sequence_number,
        customer_sequence_number=inv.customer_sequence_number,
        existing_invoice=inv,
    )

    await session.commit()
    await session.refresh(updated)
    return _invoice_to_read(updated)


async def delete_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
) -> bool:
    """Delete an invoice; returns True if deleted, False if not found.

    Only DRAFT invoices may be deleted.  Raises ValueError for any other status.
    """
    stmt = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.company_id == company_id,
    )
    result = await session.execute(stmt)
    inv = result.scalar_one_or_none()
    if inv is None:
        return False

    if inv.status != InvoiceStatus.DRAFT:
        raise ValueError(
            f"Cannot delete a {inv.status.value.lower()} invoice. "
            "Only DRAFT invoices may be deleted."
        )

    await session.delete(inv)
    await session.commit()
    return True


async def list_invoices(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    customer_id: uuid.UUID | None = None,
    status: str | None = None,
    paid_status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "invoice_date",
) -> InvoiceListResponse:
    """Return a paginated list of invoices for the company."""
    # Always join Customer so we can search by customer name and return it.
    base = (
        select(Invoice)
        .join(Customer, Invoice.customer_id == Customer.id)
        .where(Invoice.company_id == company_id)
    )

    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                Invoice.invoice_number.ilike(like),
                Invoice.reference_number.ilike(like),
                Customer.name.ilike(like),
            )
        )
    if customer_id is not None:
        base = base.where(Invoice.customer_id == customer_id)
    if status:
        base = base.where(Invoice.status == status)
    if paid_status:
        base = base.where(Invoice.paid_status == paid_status)
    if date_from:
        base = base.where(Invoice.invoice_date >= date_from)
    if date_to:
        base = base.where(Invoice.invoice_date <= date_to)

    # Count
    count_stmt = select(func.count()).select_from(base.subquery())
    count_result = await session.execute(count_stmt)
    total = count_result.scalar_one()

    # Sort
    sort_col = {
        "invoice_date": Invoice.invoice_date,
        "created_at": Invoice.created_at,
        "invoice_number": Invoice.sequence_number,
    }.get(sort_by, Invoice.invoice_date)

    data_stmt = base.order_by(sort_col.desc()).limit(limit).offset(offset)
    data_result = await session.execute(data_stmt)
    rows = list(data_result.scalars().all())

    # Batch-load customer names in a single secondary query.
    customer_name_map: dict[uuid.UUID, str] = {}
    if rows:
        cust_ids = list({inv.customer_id for inv in rows})
        cust_result = await session.execute(
            select(Customer.id, Customer.name).where(Customer.id.in_(cust_ids))
        )
        customer_name_map = {cid: name for cid, name in cust_result.all()}

    return InvoiceListResponse(
        items=[
            _invoice_to_list_item(r, customer_name=customer_name_map.get(r.customer_id, ""))
            for r in rows
        ],
        total=total,
    )


async def transition_status(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    body: InvoiceStatusWrite,
) -> InvoiceRead | None:
    """Transition invoice lifecycle status.

    M5 allowed transitions:
    - DRAFT → SENT
    - DRAFT → CANCELLED
    - CANCELLED → DRAFT  (reactivate a mistakenly-cancelled draft)
    SENT is a lock state: use a credit note (M9/M10) to reverse a sent invoice.
    COMPLETED cannot be set manually (M7 drives it via payments).
    """
    stmt = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.company_id == company_id,
    )
    result = await session.execute(stmt)
    inv = result.scalar_one_or_none()
    if inv is None:
        return None

    current_status = InvoiceStatus(inv.status)
    new_status = body.status

    if new_status == InvoiceStatus.COMPLETED:
        raise ValueError(
            "COMPLETED status is set automatically by payments (M7). "
            "It cannot be assigned manually in M5."
        )

    allowed = _ALLOWED_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition from {current_status} to {new_status}. "
            f"Allowed transitions: {', '.join(s.value for s in allowed) or 'none'}."
        )

    inv.status = new_status
    await session.commit()
    await session.refresh(inv)
    return _invoice_to_read(inv)


async def list_invoice_product_options(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None = None,
    limit: int = 20,
) -> ProductInvoiceOptionListResponse:
    """Return customer-safe product options for invoice line auto-fill.

    Explicitly excludes: purchase_cost_excl_vat, margin_rate, supplier, extra.
    """
    stmt = (
        select(Product, Unit)
        .outerjoin(Unit, Product.unit_id == Unit.id)
        .where(
            Product.company_id == company_id,
            Product.active == True,  # noqa: E712
        )
    )

    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))

    stmt = stmt.order_by(Product.name).limit(limit)
    result = await session.execute(stmt)
    rows = result.all()

    items = [
        ProductInvoiceOptionRead(
            id=product.id,
            name=product.name,
            unit_id=product.unit_id,
            unit_name=unit.name if unit is not None else None,
            default_vat_rate_id=product.default_vat_rate_id,
        )
        for product, unit in rows
    ]
    return ProductInvoiceOptionListResponse(items=items)
