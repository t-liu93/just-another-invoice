"""Formal Advance calculation and draft persistence (M12 step 3).

The allocator is deliberately independent from SQLAlchemy.  It operates in
integer currency-minor units and is consequently usable by both calculate and
the locked create/issue paths without frontend money arithmetic.
"""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jai.db import set_rls_company
from jai.models._enums import (
    AdvanceInputMode,
    DiscountType,
    DocumentChainEventType,
    InvoiceDocumentKind,
    InvoicePaidStatus,
    InvoiceRelationType,
    InvoiceSettlementStatus,
    InvoiceStatus,
    InvoiceTaxMode,
    QuoteSettlementMode,
    QuoteStatus,
)
from jai.models.document import (
    InvoiceCorrection,
    InvoiceCorrectionLine,
    InvoiceCreditBasisLine,
    InvoiceRelation,
)
from jai.models.invoice import Invoice, InvoiceLine, InvoiceLineTax, InvoiceTax
from jai.models.quote import Quote, QuoteLine, QuoteLineTax, QuoteTax
from jai.schemas.invoice import (
    AdvanceCalculationRead,
    AdvanceCalculationRequest,
    AdvanceDraftCreate,
    AdvanceDraftUpdate,
    AdvanceTaxBucketRead,
    InvoiceRead,
)
from jai.services.document_chain import (
    ModeConflictError,
    append_document_chain_event,
    lock_quote_mode,
)
from jai.services.invoice import _load_invoice_read
from jai.services.money import quantize_to_minor_unit


class AdvanceConflictError(ValueError):
    """A retryable Formal Advance conflict suitable for an HTTP 409."""

    code = "ADVANCE_CONFLICT"


class AdvanceStaleError(AdvanceConflictError):
    """A persisted Advance intent/allocation no longer matches locked capacity."""

    code = "ADVANCE_STALE"


class AdvanceValidationError(ValueError):
    """A structurally invalid Formal Advance command with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _assert_replacement_dates(
    *, invoice_date: date, supply_or_advance_date: date | None, credit: Invoice
) -> None:
    """Keep replacement invoice and supply dates after its issued Credit."""
    supply_date = supply_or_advance_date or invoice_date
    if invoice_date < credit.invoice_date or supply_date < credit.invoice_date:
        raise AdvanceValidationError(
            "REPLACEMENT_DATE_BEFORE_CREDIT",
            "Replacement invoice and supply dates cannot precede its Credit Note date.",
        )


def is_retryable_transaction_conflict(exc: DBAPIError) -> bool:
    """Return whether PostgreSQL aborted a transaction for a retryable race.

    This is deliberately limited to PostgreSQL's serialization and deadlock
    SQLSTATEs; ordinary database errors must not be relabelled as conflicts.
    """
    sqlstate, _ = _postgres_error_details(exc)
    return sqlstate in {"40001", "40P01"}


def _postgres_error_details(exc: BaseException) -> tuple[str | None, str | None]:
    """Read PostgreSQL error metadata through SQLAlchemy's async wrapper.

    asyncpg exposes ``sqlstate`` and ``constraint_name`` on its DBAPI error,
    while SQLAlchemy may retain that object under ``orig`` or ``__cause__``.
    Do not infer a domain error from a SQLSTATE family: callers deliberately
    inspect the returned values against one exact PostgreSQL constraint.
    """
    pending: list[object] = [exc]
    visited: set[int] = set()
    sqlstate_result: str | None = None
    constraint_result: str | None = None
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        sqlstate = getattr(current, "sqlstate", None)
        constraint_name = getattr(current, "constraint_name", None)
        diagnostic = getattr(current, "diag", None)
        if not isinstance(constraint_name, str):
            constraint_name = getattr(diagnostic, "constraint_name", None)
        if isinstance(sqlstate, str) and sqlstate_result is None:
            sqlstate_result = sqlstate
        if isinstance(constraint_name, str) and constraint_result is None:
            constraint_result = constraint_name
        if sqlstate_result is not None and constraint_result is not None:
            return sqlstate_result, constraint_result
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if nested is not None:
                pending.append(nested)
    return sqlstate_result, constraint_result


def is_advance_draft_conflict(exc: IntegrityError) -> bool:
    """Whether ``exc`` is precisely the Formal Advance open-draft index."""
    sqlstate, constraint_name = _postgres_error_details(exc)
    return sqlstate == "23505" and constraint_name == "uq_invoice_advance_quote_draft"


def is_invoice_number_conflict(exc: IntegrityError) -> bool:
    """Whether ``exc`` is the existing legal invoice-number uniqueness race."""
    sqlstate, constraint_name = _postgres_error_details(exc)
    return sqlstate == "23505" and constraint_name == "uq_invoice_company_number"


@dataclass(frozen=True)
class AdvanceBucket:
    vat_rate_id: uuid.UUID
    vat_rate_label: str
    vat_rate_percent: Decimal
    taxable_amount: Decimal
    vat_amount: Decimal

    @property
    def gross_amount(self) -> Decimal:
        return self.taxable_amount + self.vat_amount


_HUNDRED = Decimal("100")
_MINOR = Decimal("0.01")
_MAX_PERCENTAGE_DECIMAL_PLACES = 3
ExactAdvanceCreditProvider = Callable[[AsyncSession, Quote], Awaitable[list[AdvanceBucket]]]


def _units(value: Decimal) -> int:
    return int(quantize_to_minor_unit(value) * _HUNDRED)


def _money(units: int) -> Decimal:
    return (Decimal(units) / _HUNDRED).quantize(_MINOR)


def _allocate_units(total: int, weights: list[int]) -> list[int]:
    """Integer largest-remainder split with stable input-order tie breaks."""
    if total < 0 or any(weight < 0 for weight in weights):
        raise ValueError("Advance allocation cannot contain negative amounts.")
    denominator = sum(weights)
    if total == 0:
        return [0] * len(weights)
    if denominator <= 0 or total > denominator:
        raise ValueError("Advance amount exceeds remaining Quote capacity.")
    allocated: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, weight in enumerate(weights):
        quotient, remainder = divmod(total * weight, denominator)
        allocated.append(quotient)
        remainders.append((remainder, index))
    for _, index in sorted(remainders, key=lambda item: (-item[0], item[1]))[
        : total - sum(allocated)
    ]:
        allocated[index] += 1
    return allocated


def allocate_advance_gross(
    buckets: list[AdvanceBucket], requested_gross: Decimal
) -> list[AdvanceBucket]:
    """Allocate gross over the flat taxable/VAT component sequence once.

    The ordering is the persisted stable bucket order, with each bucket's
    ``taxable, VAT`` components adjacent.  This is intentionally the M11.5
    allocator semantics: a single largest-remainder pass over all components,
    followed only by regrouping its exact component shares into VAT buckets.
    """
    requested_units = _units(requested_gross)
    components: list[int] = []
    for bucket in buckets:
        components.extend((_units(bucket.taxable_amount), _units(bucket.vat_amount)))
    allocated_components = _allocate_units(requested_units, components)
    result: list[AdvanceBucket] = []
    for index, bucket in enumerate(buckets):
        taxable_units = allocated_components[index * 2]
        vat_units = allocated_components[index * 2 + 1]
        result.append(
            AdvanceBucket(
                vat_rate_id=bucket.vat_rate_id,
                vat_rate_label=bucket.vat_rate_label,
                vat_rate_percent=bucket.vat_rate_percent,
                taxable_amount=_money(taxable_units),
                vat_amount=_money(vat_units),
            )
        )
    return result


def requested_advance_gross(
    request: AdvanceCalculationRequest, original_quote_gross: Decimal
) -> Decimal:
    """Resolve raw gross or percentage intent; percentages round only here."""
    if request.input_mode == AdvanceInputMode.GROSS_AMOUNT:
        assert request.gross_amount is not None
        return quantize_to_minor_unit(request.gross_amount)
    assert request.percentage is not None
    return (original_quote_gross * request.percentage / _HUNDRED).quantize(
        _MINOR, rounding=ROUND_HALF_UP
    )


def _validate_persistable_advance_intent(request: AdvanceCalculationRequest) -> None:
    """Reject percentage precision that cannot be replayed from NUMERIC(6,3).

    Gross commands deliberately persist the already minor-unit-normalized
    requested amount.  Percentage commands must instead retain the exact user
    input because it is applied to the original Quote total at issue time.
    ``NUMERIC(6,3)`` is the frozen Step-3 storage shape, so accepting more
    than three decimal places would silently change the command on database
    round-trip.
    """
    if request.input_mode != AdvanceInputMode.PERCENTAGE:
        return
    assert request.percentage is not None
    exponent = request.percentage.as_tuple().exponent
    # Pydantic Decimal fields are finite; retain the explicit narrowing for
    # strict typing and a defensive stable API error should that ever change.
    if not isinstance(exponent, int):
        raise AdvanceValidationError(
            "ADVANCE_PERCENTAGE_PRECISION", "Advance percentage must be finite."
        )
    places = max(0, -exponent)
    if places > _MAX_PERCENTAGE_DECIMAL_PLACES:
        raise AdvanceValidationError(
            "ADVANCE_PERCENTAGE_PRECISION",
            "Advance percentage supports at most three decimal places.",
        )


async def _quote_buckets(session: AsyncSession, quote: Quote) -> list[AdvanceBucket]:
    """Read only accepted Quote's persisted line/document tax snapshots."""
    if InvoiceTaxMode(quote.tax_mode) == InvoiceTaxMode.DOCUMENT:
        document_rows = list(
            (
                await session.execute(
                    select(QuoteTax)
                    .where(QuoteTax.quote_id == quote.id)
                    .order_by(
                        QuoteTax.vat_rate_percent, QuoteTax.vat_rate_label, QuoteTax.vat_rate_id
                    )
                )
            ).scalars()
        )
        return [
            AdvanceBucket(
                row.vat_rate_id,
                row.vat_rate_label,
                Decimal(str(row.vat_rate_percent)),
                Decimal(str(row.taxable_amount)),
                Decimal(str(row.tax_amount)),
            )
            for row in document_rows
        ]
    line_rows = list(
        (
            await session.execute(
                select(QuoteLineTax, QuoteLine.sort_order)
                .join(QuoteLine, QuoteLineTax.quote_line_id == QuoteLine.id)
                .where(QuoteLine.quote_id == quote.id)
                .order_by(QuoteLine.sort_order, QuoteLineTax.id)
            )
        ).all()
    )
    grouped: dict[tuple[uuid.UUID, str, Decimal], list[Decimal]] = {}
    for row, _ in line_rows:
        key = (row.vat_rate_id, row.vat_rate_label, Decimal(str(row.vat_rate_percent)))
        amounts = grouped.setdefault(key, [Decimal("0"), Decimal("0")])
        amounts[0] += Decimal(str(row.taxable_amount))
        amounts[1] += Decimal(str(row.tax_amount))
    return [
        AdvanceBucket(key[0], key[1], key[2], amounts[0], amounts[1])
        for key, amounts in sorted(
            grouped.items(), key=lambda item: (item[0][2], item[0][1], item[0][0])
        )
    ]


def subtract_exact_advance_credits(
    issued: list[AdvanceBucket], exact_credits: list[AdvanceBucket]
) -> list[AdvanceBucket]:
    """Reopen only source buckets named by immutable Credit basis rows.

    This is deliberately a typed, pure boundary: a future Step-5 query can
    supply exact source-bucket credits without turning an invoice-level gross
    total into an invented VAT split.
    """
    credits = {
        (row.vat_rate_id, row.vat_rate_label, row.vat_rate_percent): row for row in exact_credits
    }
    issued_keys = {(row.vat_rate_id, row.vat_rate_label, row.vat_rate_percent) for row in issued}
    if not set(credits).issubset(issued_keys):
        raise AdvanceStaleError("Exact Advance Credit refers to an unknown source VAT bucket.")
    reopened: list[AdvanceBucket] = []
    for row in issued:
        credit = credits.get((row.vat_rate_id, row.vat_rate_label, row.vat_rate_percent))
        taxable = row.taxable_amount - (credit.taxable_amount if credit else Decimal("0"))
        vat = row.vat_amount - (credit.vat_amount if credit else Decimal("0"))
        if taxable < 0 or vat < 0:
            raise AdvanceStaleError("Exact Advance Credit coverage exceeds its source bucket.")
        reopened.append(
            AdvanceBucket(row.vat_rate_id, row.vat_rate_label, row.vat_rate_percent, taxable, vat)
        )
    return reopened


async def _issued_advance_buckets(
    session: AsyncSession,
    quote: Quote,
    *,
    exact_credit_provider: ExactAdvanceCreditProvider | None = None,
) -> list[AdvanceBucket]:
    """Return issued Advance coverage minus exact, normalized Credit coverage.

    Step 3 has no Credit Note basis table/API yet.  Its provider intentionally
    returns no reopening data instead of guessing VAT buckets from an
    invoice-level ``credited_total``.  Step 5 plugs real immutable source-basis
    credit rows into that provider.
    """
    rows = list(
        (
            await session.execute(
                select(InvoiceLine)
                .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
                .where(
                    Invoice.quote_id == quote.id,
                    Invoice.document_kind == InvoiceDocumentKind.ADVANCE,
                    Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.COMPLETED]),
                )
                .order_by(Invoice.invoice_date, Invoice.id, InvoiceLine.sort_order, InvoiceLine.id)
            )
        ).scalars()
    )
    grouped: dict[tuple[uuid.UUID, str, Decimal], list[Decimal]] = {}
    for line in rows:
        if line.vat_rate_id is None or line.vat_rate_label is None or line.vat_rate_percent is None:
            continue
        key = (line.vat_rate_id, line.vat_rate_label, Decimal(str(line.vat_rate_percent)))
        amounts = grouped.setdefault(key, [Decimal("0"), Decimal("0")])
        amounts[0] += Decimal(str(line.taxable_amount))
        amounts[1] += Decimal(str(line.vat_total))
    issued = [
        AdvanceBucket(key[0], key[1], key[2], value[0], value[1])
        for key, value in sorted(
            grouped.items(), key=lambda item: (item[0][2], item[0][1], item[0][0])
        )
    ]
    provider = exact_credit_provider or _exact_advance_credit_buckets
    return subtract_exact_advance_credits(issued, await provider(session, quote))


async def _exact_advance_credit_buckets(session: AsyncSession, quote: Quote) -> list[AdvanceBucket]:
    """Return immutable issued Credit buckets for the Quote's Advances.

    Never derive a VAT split from ``credited_total``: correction lines retain
    the selected source-basis bucket, so reopening capacity stays exact for
    mixed 21/9/0% projects.
    """
    credit = Invoice.__table__.alias("advance_credit_invoice")
    source = Invoice.__table__.alias("advance_credit_source")
    rows = (
        await session.execute(
            select(InvoiceCreditBasisLine, InvoiceCorrectionLine)
            .join(
                InvoiceCorrectionLine,
                InvoiceCorrectionLine.source_basis_line_id == InvoiceCreditBasisLine.id,
            )
            .join(InvoiceCorrection, InvoiceCorrection.id == InvoiceCorrectionLine.correction_id)
            .join(credit, credit.c.id == InvoiceCorrection.credit_note_id)
            .join(source, source.c.id == InvoiceCorrection.source_invoice_id)
            .where(
                source.c.quote_id == quote.id,
                source.c.document_kind == InvoiceDocumentKind.ADVANCE,
                credit.c.status.in_([InvoiceStatus.SENT, InvoiceStatus.COMPLETED]),
            )
        )
    ).all()
    grouped: dict[tuple[uuid.UUID, str, Decimal], list[Decimal]] = {}
    for basis, line in rows:
        if basis.vat_rate_id is None or basis.vat_rate_label is None or basis.vat_rate_percent is None:
            raise AdvanceStaleError("Issued Advance Credit lacks a frozen VAT bucket.")
        key = (basis.vat_rate_id, basis.vat_rate_label, Decimal(str(basis.vat_rate_percent)))
        amounts = grouped.setdefault(key, [Decimal("0"), Decimal("0")])
        amounts[0] += Decimal(str(line.net_amount))
        amounts[1] += Decimal(str(line.vat_amount))
    return [
        AdvanceBucket(key[0], key[1], key[2], amounts[0], amounts[1])
        for key, amounts in sorted(grouped.items(), key=lambda item: (item[0][2], item[0][1], item[0][0]))
    ]


async def _remaining_buckets(session: AsyncSession, quote: Quote) -> list[AdvanceBucket]:
    source = await _quote_buckets(session, quote)
    used = {
        (item.vat_rate_id, item.vat_rate_label, item.vat_rate_percent): item
        for item in await _issued_advance_buckets(session, quote)
    }
    result: list[AdvanceBucket] = []
    for item in source:
        used_item = used.get((item.vat_rate_id, item.vat_rate_label, item.vat_rate_percent))
        taxable = item.taxable_amount - (used_item.taxable_amount if used_item else Decimal("0"))
        vat = item.vat_amount - (used_item.vat_amount if used_item else Decimal("0"))
        if taxable < 0 or vat < 0:
            raise AdvanceConflictError("Persisted Advance coverage exceeds its Quote snapshots.")
        result.append(
            AdvanceBucket(
                item.vat_rate_id, item.vat_rate_label, item.vat_rate_percent, taxable, vat
            )
        )
    return result


def _read_calculation(
    request: AdvanceCalculationRequest,
    original_total: Decimal,
    remaining: list[AdvanceBucket],
) -> AdvanceCalculationRead:
    _validate_persistable_advance_intent(request)
    requested = requested_advance_gross(request, original_total)
    if requested <= 0:
        raise AdvanceValidationError(
            "ADVANCE_AMOUNT_TOO_SMALL",
            "Advance gross amount must remain greater than zero after minor-unit rounding.",
        )
    allocated = allocate_advance_gross(remaining, requested)
    capacity = sum((bucket.gross_amount for bucket in remaining), Decimal("0"))
    return AdvanceCalculationRead(
        input_mode=request.input_mode,
        requested_gross_amount=requested,
        original_quote_gross_amount=original_total,
        remaining_capacity=quantize_to_minor_unit(capacity),
        taxable_amount=sum((bucket.taxable_amount for bucket in allocated), Decimal("0")),
        vat_total=sum((bucket.vat_amount for bucket in allocated), Decimal("0")),
        gross_amount=sum((bucket.gross_amount for bucket in allocated), Decimal("0")),
        buckets=[
            AdvanceTaxBucketRead(
                vat_rate_id=bucket.vat_rate_id,
                vat_rate_label=bucket.vat_rate_label,
                vat_rate_percent=bucket.vat_rate_percent,
                taxable_amount=bucket.taxable_amount,
                vat_amount=bucket.vat_amount,
                gross_amount=bucket.gross_amount,
            )
            for bucket in allocated
        ],
    )


def _validate_quote(quote: Quote) -> None:
    if QuoteSettlementMode(quote.settlement_mode) not in {
        QuoteSettlementMode.UNSET,
        QuoteSettlementMode.FORMAL_ADVANCE,
    }:
        raise ModeConflictError("Quote settlement mode does not permit Formal Advances.")
    if QuoteStatus(quote.status) != QuoteStatus.ACCEPTED:
        raise AdvanceValidationError(
            "ADVANCE_INVALID_QUOTE_STATUS", "Quote must be ACCEPTED for a Formal Advance."
        )
    if quote.converted_invoice_id is not None:
        raise ModeConflictError("Quote has already been directly converted.")
    if quote.vat_treatment_code != "NL_DOMESTIC":
        raise AdvanceValidationError(
            "ADVANCE_UNSUPPORTED_VAT_TREATMENT", "Formal Advances require NL_DOMESTIC."
        )


def _validate_advance_dates(invoice_date: date, due_date: date | None) -> None:
    if due_date is not None and due_date < invoice_date:
        raise AdvanceValidationError(
            "ADVANCE_INVALID_DUE_DATE", "Advance due_date must be on or after invoice_date."
        )


async def calculate_advance(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    quote_id: uuid.UUID,
    request: AdvanceCalculationRequest,
) -> AdvanceCalculationRead:
    """Calculate only: no locks, no writes and no event/number side effect."""
    await set_rls_company(session, company_id)
    quote = (
        await session.execute(
            select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id)
        )
    ).scalar_one_or_none()
    if quote is None:
        raise LookupError("Quote not found.")
    _validate_quote(quote)
    return _read_calculation(
        request,
        Decimal(str(quote.total_incl_vat)),
        await _remaining_buckets(session, quote),
    )


async def _locked_quote(session: AsyncSession, company_id: uuid.UUID, quote_id: uuid.UUID) -> Quote:
    quote = (
        await session.execute(
            select(Quote)
            .where(Quote.id == quote_id, Quote.company_id == company_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if quote is None:
        raise LookupError("Quote not found.")
    _validate_quote(quote)
    return quote


async def _open_draft(session: AsyncSession, quote_id: uuid.UUID, *, lock: bool) -> Invoice | None:
    stmt = select(Invoice).where(
        Invoice.quote_id == quote_id,
        Invoice.document_kind == InvoiceDocumentKind.ADVANCE,
        Invoice.status == InvoiceStatus.DRAFT,
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def _has_active_final(session: AsyncSession, quote_id: uuid.UUID) -> bool:
    return (
        await session.scalar(
            select(Invoice.id)
            .where(
                Invoice.quote_id == quote_id,
                Invoice.document_kind == InvoiceDocumentKind.FINAL,
            )
            .limit(1)
        )
    ) is not None


@dataclass(frozen=True)
class AdvanceCreationAvailability:
    """Shared structural predicate for a new Formal Advance Draft.

    Command-specific amount and date validation intentionally stays with the
    calculate/create commands.  This result owns only the chain state that
    tells a caller whether an Advance creation can be offered at all.
    """

    available: bool
    has_open_draft: bool
    remaining_buckets: tuple[AdvanceBucket, ...]


async def assess_advance_creation(
    session: AsyncSession, quote: Quote, *, lock_open_draft: bool = False
) -> AdvanceCreationAvailability:
    """Assess the authoritative, non-input-specific Advance creation state.

    ``lock_open_draft`` is true only for the Quote-locked create command.  A
    document-chain GET uses the same semantics with an ordinary read query;
    availability projection must never serialize readers behind a Draft row.
    """
    structurally_eligible = (
        QuoteStatus(quote.status) == QuoteStatus.ACCEPTED
        and quote.vat_treatment_code == "NL_DOMESTIC"
        and QuoteSettlementMode(quote.settlement_mode)
        in {QuoteSettlementMode.UNSET, QuoteSettlementMode.FORMAL_ADVANCE}
        and quote.converted_invoice_id is None
    )
    if not structurally_eligible:
        return AdvanceCreationAvailability(False, False, ())
    has_open_draft = await _open_draft(session, quote.id, lock=lock_open_draft) is not None
    if has_open_draft:
        # Create returns its stable competing-draft error before any input
        # capacity validation, and the read projection only needs ``false``.
        return AdvanceCreationAvailability(False, True, ())
    # D8: a Final DRAFT/issued Final freezes further Formal Advance issue.
    # Keeping this in the shared predicate also prevents creation of a draft
    # that could never be legally issued.
    has_final = await _has_active_final(session, quote.id)
    if has_final:
        return AdvanceCreationAvailability(False, False, ())
    remaining_buckets = tuple(await _remaining_buckets(session, quote))
    has_remaining_capacity = any(bucket.gross_amount > 0 for bucket in remaining_buckets)
    return AdvanceCreationAvailability(
        not has_open_draft and has_remaining_capacity,
        has_open_draft,
        remaining_buckets,
    )


async def _persist_advance(
    session: AsyncSession,
    *,
    quote: Quote,
    body: AdvanceDraftCreate | AdvanceDraftUpdate,
    calculation: AdvanceCalculationRead,
    creator_id: uuid.UUID | None,
    existing: Invoice | None = None,
) -> Invoice:
    if existing is not None:
        await session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == existing.id))
        await session.execute(delete(InvoiceTax).where(InvoiceTax.invoice_id == existing.id))
        invoice = existing
    else:
        invoice = Invoice(
            company_id=quote.company_id,
            customer_id=quote.customer_id,
            invoice_number=None,
            sequence_number=None,
            customer_sequence_number=None,
            status=InvoiceStatus.DRAFT,
            paid_status=InvoicePaidStatus.UNPAID,
            document_kind=InvoiceDocumentKind.ADVANCE,
            quote_id=quote.id,
            creator_id=creator_id,
            incoming_payment_total=Decimal("0"),
            credited_total=Decimal("0"),
            refunded_total=Decimal("0"),
            refund_due_amount=Decimal("0"),
            base_incoming_payment_total=Decimal("0"),
            base_credited_total=Decimal("0"),
            base_refunded_total=Decimal("0"),
            base_refund_due_amount=Decimal("0"),
            credit_status="NOT_CREDITED",
            settlement_status=InvoiceSettlementStatus.OPEN,
        )
        session.add(invoice)
    gross = calculation.gross_amount
    taxable = calculation.taxable_amount
    vat = calculation.vat_total
    _validate_advance_dates(body.invoice_date, body.due_date)
    invoice.customer_id = quote.customer_id
    invoice.reference_number = body.reference_number
    invoice.invoice_date = body.invoice_date
    invoice.due_date = body.due_date
    invoice.supply_or_advance_date = body.supply_or_advance_date
    invoice.advance_input_mode = body.input_mode
    # The stored gross command is the authoritative amount after the frozen
    # currency-minor-unit normalization, not the arbitrary precision supplied
    # over HTTP.  That makes create/update -> DB -> issue an exact replay.
    invoice.advance_gross_amount = (
        calculation.requested_gross_amount
        if body.input_mode == AdvanceInputMode.GROSS_AMOUNT
        else None
    )
    invoice.advance_percentage = body.percentage
    invoice.currency = quote.currency
    invoice.exchange_rate = Decimal("1")
    invoice.tax_mode = InvoiceTaxMode.LINE
    invoice.amounts_include_vat = True
    invoice.vat_treatment_id = quote.vat_treatment_id
    invoice.document_vat_rate_id = None
    invoice.vat_treatment_code = quote.vat_treatment_code
    invoice.vat_treatment_label = quote.vat_treatment_label
    invoice.vat_treatment_effect = quote.vat_treatment_effect
    invoice.vat_treatment_requires_icp = quote.vat_treatment_requires_icp
    invoice.discount_type = DiscountType.NONE
    invoice.discount_value = Decimal("0")
    invoice.document_discount_amount = Decimal("0")
    invoice.subtotal_excl_vat = taxable
    invoice.line_discount_total = Decimal("0")
    invoice.taxable_amount = taxable
    invoice.vat_total = vat
    invoice.total_incl_vat = gross
    invoice.due_amount = gross
    invoice.payable_before_payments = gross
    invoice.base_subtotal_excl_vat = taxable
    invoice.base_line_discount_total = Decimal("0")
    invoice.base_taxable_amount = taxable
    invoice.base_vat_total = vat
    invoice.base_total_incl_vat = gross
    invoice.base_due_amount = gross
    invoice.base_payable_before_payments = gross
    invoice.notes = quote.notes
    invoice.warranty_text = quote.warranty_text
    invoice.terms_text = quote.terms_text
    invoice.bank_text = quote.bank_text
    invoice.payment_terms_text = quote.payment_terms_text
    await session.flush()
    for position, bucket in enumerate(calculation.buckets):
        line = InvoiceLine(
            invoice_id=invoice.id,
            sort_order=position,
            name="Advance invoice",
            description=None,
            quantity=Decimal("1"),
            unit_id=None,
            unit_name=None,
            unit_price=bucket.taxable_amount,
            discount_type=DiscountType.NONE,
            discount_value=Decimal("0"),
            vat_rate_id=bucket.vat_rate_id,
            vat_rate_label=bucket.vat_rate_label,
            vat_rate_percent=bucket.vat_rate_percent,
            subtotal_excl_vat=bucket.taxable_amount,
            subtotal_incl_vat=bucket.gross_amount,
            line_discount_amount=Decimal("0"),
            document_discount_share=Decimal("0"),
            taxable_amount=bucket.taxable_amount,
            vat_total=bucket.vat_amount,
            total_incl_vat=bucket.gross_amount,
        )
        session.add(line)
        await session.flush()
        session.add(
            InvoiceLineTax(
                invoice_line_id=line.id,
                vat_rate_id=bucket.vat_rate_id,
                vat_rate_label=bucket.vat_rate_label,
                vat_rate_percent=bucket.vat_rate_percent,
                effective_vat_percent=bucket.vat_rate_percent,
                taxable_amount=bucket.taxable_amount,
                tax_amount=bucket.vat_amount,
            )
        )
    await session.flush()
    return invoice


async def validate_advance_issue(session: AsyncSession, *, quote: Quote, invoice: Invoice) -> None:
    """Revalidate a DRAFT allocation under the Quote lock immediately before issue."""
    _validate_quote(quote)
    final_exists = (
        await session.scalar(
            select(Invoice.id)
            .where(
                Invoice.quote_id == quote.id,
                Invoice.document_kind == InvoiceDocumentKind.FINAL,
            )
            .limit(1)
        )
    ) is not None
    if final_exists:
        raise AdvanceConflictError("A Final DRAFT or issued Final freezes Advance issue.")
    if InvoiceDocumentKind(invoice.document_kind) != InvoiceDocumentKind.ADVANCE:
        raise AdvanceConflictError("Only Advance invoices can use Advance issue validation.")
    _validate_advance_dates(invoice.invoice_date, invoice.due_date)
    relation = await session.scalar(
        select(InvoiceRelation).where(InvoiceRelation.invoice_id == invoice.id)
    )
    if relation is not None:
        def related_buckets(document: Invoice) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
            buckets: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
            if InvoiceTaxMode(document.tax_mode) == InvoiceTaxMode.DOCUMENT:
                for tax in document.taxes:
                    old_net, old_vat = buckets.get(
                        tax.vat_rate_id, (Decimal("0"), Decimal("0"))
                    )
                    buckets[tax.vat_rate_id] = (
                        old_net + Decimal(str(tax.taxable_amount)),
                        old_vat + Decimal(str(tax.tax_amount)),
                    )
                return buckets
            for row in document.lines:
                if row.vat_rate_id is None:
                    raise AdvanceStaleError("A related Advance has an incomplete VAT bucket.")
                old_net, old_vat = buckets.get(
                    row.vat_rate_id, (Decimal("0"), Decimal("0"))
                )
                buckets[row.vat_rate_id] = (
                    old_net + Decimal(str(row.taxable_amount)),
                    old_vat + Decimal(str(row.vat_total)),
                )
            return buckets

        remaining = {
            bucket.vat_rate_id: bucket for bucket in await _remaining_buckets(session, quote)
        }
        invoice_buckets = related_buckets(invoice)
        for rate_id, (net, vat) in invoice_buckets.items():
            capacity = remaining.get(rate_id)
            if capacity is None or net > capacity.taxable_amount or vat > capacity.vat_amount:
                raise AdvanceStaleError(
                    "Related Advance VAT basis no longer fits the locked Quote capacity."
                )
        if InvoiceRelationType(relation.relation_type) == InvoiceRelationType.COMPENSATES_CREDIT:
            credit = (
                await session.execute(
                    select(Invoice).where(Invoice.id == relation.related_credit_note_id)
                )
            ).scalar_one()
            credit_buckets = related_buckets(credit)
            if invoice_buckets != credit_buckets:
                raise AdvanceStaleError(
                    "A compensating Advance must retain its issued Credit tax basis."
                )
        elif InvoiceRelationType(relation.relation_type) == InvoiceRelationType.REPLACEMENT_OF:
            credit = (
                await session.execute(
                    select(Invoice).where(Invoice.id == relation.related_credit_note_id)
                )
            ).scalar_one()
            _assert_replacement_dates(
                invoice_date=invoice.invoice_date,
                supply_or_advance_date=invoice.supply_or_advance_date,
                credit=credit,
            )
        if (
            sum((value[0] for value in invoice_buckets.values()), Decimal("0"))
            != Decimal(str(invoice.taxable_amount))
            or sum((value[1] for value in invoice_buckets.values()), Decimal("0"))
            != Decimal(str(invoice.vat_total))
            or Decimal(str(invoice.taxable_amount)) + Decimal(str(invoice.vat_total))
            != Decimal(str(invoice.total_incl_vat))
        ):
            raise AdvanceStaleError("Related Advance totals do not close over its VAT buckets.")
        return
    if invoice.advance_input_mode is None:
        raise AdvanceStaleError(
            "Advance DRAFT has no persisted input intent and cannot be safely issued."
        )
    try:
        input_mode = AdvanceInputMode(invoice.advance_input_mode)
    except ValueError as exc:
        raise AdvanceStaleError("Advance DRAFT has an invalid persisted input intent.") from exc
    gross_amount = (
        Decimal(str(invoice.advance_gross_amount))
        if invoice.advance_gross_amount is not None
        else None
    )
    percentage = (
        Decimal(str(invoice.advance_percentage)) if invoice.advance_percentage is not None else None
    )
    try:
        intent = AdvanceCalculationRequest(
            input_mode=input_mode, gross_amount=gross_amount, percentage=percentage
        )
    except ValueError as exc:
        raise AdvanceStaleError("Advance DRAFT has incomplete persisted input intent.") from exc
    try:
        expected = _read_calculation(
            intent,
            Decimal(str(quote.total_incl_vat)),
            await _remaining_buckets(session, quote),
        )
    except AdvanceValidationError:
        raise
    except ValueError as exc:
        raise AdvanceStaleError("Advance intent exceeds the locked Quote capacity.") from exc
    rows = list(
        (
            await session.execute(
                select(InvoiceLine)
                .where(InvoiceLine.invoice_id == invoice.id)
                .order_by(InvoiceLine.sort_order, InvoiceLine.id)
            )
        ).scalars()
    )
    if len(rows) != len(expected.buckets):
        raise AdvanceStaleError(
            "Advance persisted VAT buckets no longer match its accepted Quote intent."
        )
    for row, bucket in zip(rows, expected.buckets, strict=True):
        if row.vat_rate_id is None or row.vat_rate_label is None or row.vat_rate_percent is None:
            raise AdvanceStaleError("Advance has an incomplete persisted VAT bucket.")
        if (
            row.vat_rate_id != bucket.vat_rate_id
            or row.vat_rate_label != bucket.vat_rate_label
            or Decimal(str(row.vat_rate_percent)) != bucket.vat_rate_percent
            or Decimal(str(row.taxable_amount)) != bucket.taxable_amount
            or Decimal(str(row.vat_total)) != bucket.vat_amount
            or Decimal(str(row.total_incl_vat)) != bucket.gross_amount
        ):
            raise AdvanceStaleError(
                "Advance allocation is stale against the locked Quote capacity."
            )
    if (
        Decimal(str(invoice.taxable_amount)) != expected.taxable_amount
        or Decimal(str(invoice.vat_total)) != expected.vat_total
        or Decimal(str(invoice.total_incl_vat)) != expected.gross_amount
    ):
        raise AdvanceStaleError("Advance totals are stale against its persisted input intent.")


async def create_advance_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    quote_id: uuid.UUID,
    body: AdvanceDraftCreate,
    creator_id: uuid.UUID | None,
) -> InvoiceRead:
    await set_rls_company(session, company_id)
    try:
        quote = await _locked_quote(session, company_id, quote_id)
        if await _has_active_final(session, quote.id):
            raise AdvanceConflictError("A Final DRAFT or issued Final freezes Advance creation.")
        availability = await assess_advance_creation(session, quote, lock_open_draft=True)
        if availability.has_open_draft:
            raise AdvanceConflictError("Only one open Advance DRAFT is allowed per Quote.")
        calculation = _read_calculation(
            body, Decimal(str(quote.total_incl_vat)), list(availability.remaining_buckets)
        )
        await lock_quote_mode(
            session, quote, QuoteSettlementMode.FORMAL_ADVANCE, actor_user_id=creator_id
        )
        invoice = await _persist_advance(
            session, quote=quote, body=body, calculation=calculation, creator_id=creator_id
        )
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id,
            invoice_id=invoice.id,
            actor_user_id=creator_id,
            event_type=DocumentChainEventType.INVOICE_CREATED,
            metadata={"document_kind": "ADVANCE"},
        )
        await session.flush()
        result = await _load_invoice_read(session, invoice)
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        # The Quote row lock is the friendly serialization path; the partial
        # unique index is still authoritative when another writer bypasses it.
        if is_advance_draft_conflict(exc):
            raise AdvanceConflictError("Only one open Advance DRAFT is allowed per Quote.") from exc
        raise
    except DBAPIError as exc:
        await session.rollback()
        if is_retryable_transaction_conflict(exc):
            raise AdvanceConflictError(
                "Concurrent Formal Advance mutation; retry the command."
            ) from exc
        raise
    except Exception:
        await session.rollback()
        raise


async def update_advance_draft(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    body: AdvanceDraftUpdate,
    actor_user_id: uuid.UUID | None,
) -> InvoiceRead | None:
    await set_rls_company(session, company_id)
    try:
        probe = (
            await session.execute(
                select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == company_id)
            )
        ).scalar_one_or_none()
        if probe is None:
            return None
        if (
            InvoiceDocumentKind(probe.document_kind) != InvoiceDocumentKind.ADVANCE
            or probe.quote_id is None
        ):
            raise ValueError("ADVANCE_KIND_REQUIRED: Only Advance drafts use this endpoint.")
        quote = await _locked_quote(session, company_id, probe.quote_id)
        invoice = (
            await session.execute(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
        ).scalar_one()
        if InvoiceStatus(invoice.status) != InvoiceStatus.DRAFT:
            raise AdvanceConflictError("Only a DRAFT Advance can be updated.")
        relation = await session.scalar(
            select(InvoiceRelation).where(InvoiceRelation.invoice_id == invoice.id)
        )
        if (
            relation is not None
            and InvoiceRelationType(relation.relation_type)
            == InvoiceRelationType.COMPENSATES_CREDIT
        ):
            raise AdvanceConflictError(
                "A compensating Advance mirrors its Credit basis and cannot be repriced."
            )
        if (
            relation is not None
            and InvoiceRelationType(relation.relation_type) == InvoiceRelationType.REPLACEMENT_OF
        ):
            credit = (
                await session.execute(
                    select(Invoice).where(Invoice.id == relation.related_credit_note_id)
                )
            ).scalar_one()
            _assert_replacement_dates(
                invoice_date=body.invoice_date,
                supply_or_advance_date=body.supply_or_advance_date,
                credit=credit,
            )
        calculation = _read_calculation(
            body, Decimal(str(quote.total_incl_vat)), await _remaining_buckets(session, quote)
        )
        invoice = await _persist_advance(
            session,
            quote=quote,
            body=body,
            calculation=calculation,
            creator_id=invoice.creator_id,
            existing=invoice,
        )
        await append_document_chain_event(
            session,
            company_id=company_id,
            quote_id=quote.id,
            invoice_id=invoice.id,
            actor_user_id=actor_user_id,
            event_type=DocumentChainEventType.INVOICE_UPDATED,
            metadata={"document_kind": "ADVANCE"},
        )
        await session.flush()
        result = await _load_invoice_read(session, invoice)
        await session.commit()
        return result
    except DBAPIError as exc:
        await session.rollback()
        if is_retryable_transaction_conflict(exc):
            raise AdvanceConflictError(
                "Concurrent Formal Advance mutation; retry the command."
            ) from exc
        raise
    except Exception:
        await session.rollback()
        raise
