"""Invoice numbering engine – concurrency-safe sequence allocation + template rendering.

Public API
----------
- ``render_template``           – pure renderer (no DB); substitutes whitelist
                                  placeholders with context values.
- ``needs_customer_sequence``   – inspect template to determine CUSTOMER scope
                                  allocation requirement.
- ``validate_template``         – verify that all placeholders in a template are
                                  recognised; raises ``ValueError`` on unknown tokens.
- ``get_next_sequence_info``    – read current next_value + preview number without
                                  mutating the sequence (read-only).
- ``advance_sequence``          – skip the company sequence forward (only forward;
                                  raises ``ValueError`` if new value ≤ current).
- ``allocate_invoice_number``   – within an open transaction: allocate and return
                                  ``(invoice_number, seq_number, customer_seq|None)``.

Design notes
------------
Row-level locking (``SELECT … FOR UPDATE``) within the caller's transaction
guarantees that concurrent invoice creation never duplicates a sequence number.
The ``ON CONFLICT DO NOTHING`` upsert initialises the row only on the very
first call; subsequent calls acquire the lock on the existing row directly.

Red-line 4 compliance: no ``max+1`` anywhere; DB sequence + unique constraint
enforce uniqueness.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from typing import NoReturn

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import NumberSequenceScope
from jai.models.invoice import Invoice
from jai.models.number_sequence import NumberSequence
from jai.schemas.setting import CreditNumberingConfig, InvoiceNumberingConfig, QuoteNumberingConfig

# ---------------------------------------------------------------------------
# Placeholder regex
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")

# Allowed characters in a DATE strftime format: only % + alphanumeric + hyphen/underscore/space.
# Deliberately excludes / and . to block path-traversal-looking inputs.
_SAFE_DATE_FORMAT_RE = re.compile(r"^[A-Za-z0-9%\-_ ]+$")

# Document type constants.
DOCUMENT_TYPE_INVOICE = "INVOICE"
DOCUMENT_TYPE_QUOTE = "QUOTE"
DOCUMENT_TYPE_CREDIT_NOTE = "CREDIT_NOTE"
POSTGRES_BIGINT_MAX = 9_223_372_036_854_775_807


class NumberSequenceExhaustedError(ValueError):
    """A legal document-number series has no representable free candidate."""

    code = "NUMBER_SEQUENCE_EXHAUSTED"


def _raise_sequence_exhausted() -> NoReturn:
    raise NumberSequenceExhaustedError(
        "No document number is available in this sequence. Advance or change its template."
    )


async def _lock_invoice_number_namespace(
    session: AsyncSession, company_id: uuid.UUID
) -> None:
    """Serialize every writer of the shared ``invoice_number`` namespace.

    Invoice and Credit Note counters intentionally remain independent, but a
    custom template can render the same string from either counter.  A
    transaction-scoped advisory lock covers the check, allocation and caller's
    eventual INSERT, so a Standard/Credit race is observed as a normal occupied
    number and advances the losing counter instead of leaking an IntegrityError.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:company_id AS text), 0))"),
        {"company_id": str(company_id)},
    )


# ---------------------------------------------------------------------------
# Pure helpers – no DB access
# ---------------------------------------------------------------------------


def validate_template(template: str) -> None:
    """Raise ``ValueError`` if the template contains any unknown placeholder.

    Only whitelisted placeholder forms are accepted; arbitrary expressions are
    rejected to prevent template-injection (red-line 7 intent for numbering).
    """
    for m in _PLACEHOLDER_RE.finditer(template):
        _parse_token(m.group(1).strip())  # raises on unknown token


def needs_customer_sequence(template: str) -> bool:
    """Return ``True`` if the template references ``{{CUSTOMER_SEQUENCE:n}}``."""
    for m in _PLACEHOLDER_RE.finditer(template):
        token = m.group(1).strip()
        if re.fullmatch(r"CUSTOMER_SEQUENCE:\d+", token):
            return True
    return False


def needs_sequence_placeholder(template: str) -> bool:
    """Return ``True`` if the template contains at least one company-level ``{{SEQUENCE:n}}``.

    ``{{CUSTOMER_SEQUENCE:n}}`` is intentionally *not* sufficient: customer
    sequences are independent per-customer counters starting at 1, so two
    different customers would both receive sequence number 1 on their first
    invoice, producing duplicate invoice strings.  Only the company-level
    ``{{SEQUENCE:n}}`` counter is global within the company and guarantees
    uniqueness across all invoices.
    """
    for m in _PLACEHOLDER_RE.finditer(template):
        token = m.group(1).strip()
        if re.fullmatch(r"SEQUENCE:\d+", token):
            return True
    return False


def validate_credit_template(template: str) -> None:
    """Validate the smaller, company-scoped Credit Note template language."""
    validate_template(template)
    for match in _PLACEHOLDER_RE.finditer(template):
        token = match.group(1).strip()
        if token == "CUSTOMER_SERIES" or re.fullmatch(r"CUSTOMER_SEQUENCE:\d+", token):
            raise ValueError(
                "Credit Note numbering does not support customer-scoped placeholders."
            )


def render_template(
    template: str,
    *,
    sequence: int,
    customer_series: str | None,
    customer_sequence: int | None,
    invoice_date: date,
) -> str:
    """Render an invoice number template with the given context values.

    Parameters
    ----------
    template:
        The numbering template, e.g. ``{{SERIES:INV}}-{{SEQUENCE:6}}``.
    sequence:
        The company-scope sequence number that has already been allocated.
    customer_series:
        The customer's ``invoice_prefix`` (used for ``{{CUSTOMER_SERIES}}``).
    customer_sequence:
        The customer-scope sequence number (used for ``{{CUSTOMER_SEQUENCE:n}}``).
        May be ``None`` if the template does not reference that placeholder.
    invoice_date:
        The invoice date used for ``{{DATE:format}}`` placeholders.

    Raises
    ------
    ValueError
        If the template contains an unrecognised placeholder.
    """

    def _replace(m: re.Match[str]) -> str:
        return _render_token(
            m.group(1).strip(),
            sequence=sequence,
            customer_series=customer_series,
            customer_sequence=customer_sequence,
            invoice_date=invoice_date,
        )

    return _PLACEHOLDER_RE.sub(_replace, template)


# ---------------------------------------------------------------------------
# Internal token helpers
# ---------------------------------------------------------------------------


def _parse_token(token: str) -> str:
    """Validate and return the canonical form of a placeholder token.

    Raises ``ValueError`` for unknown tokens.
    """
    if re.fullmatch(r"SERIES:.+", token):
        return "SERIES"
    if re.fullmatch(r"SEQUENCE:\d+", token):
        return "SEQUENCE"
    if token == "CUSTOMER_SERIES":
        return "CUSTOMER_SERIES"
    if re.fullmatch(r"CUSTOMER_SEQUENCE:\d+", token):
        return "CUSTOMER_SEQUENCE"
    if m := re.fullmatch(r"DATE:(.+)", token):
        fmt = m.group(1)
        if not _SAFE_DATE_FORMAT_RE.match(fmt):
            raise ValueError(
                f"DATE format contains disallowed characters: {fmt!r}. "
                "Only alphanumeric characters, %, -, /, _, ., and spaces are allowed."
            )
        return "DATE"
    raise ValueError(
        f"Unknown invoice number placeholder: {{{{{token}}}}}. "
        "Allowed: SERIES:VALUE, SEQUENCE:n, CUSTOMER_SERIES, "
        "CUSTOMER_SEQUENCE:n, DATE:format."
    )


def _render_token(
    token: str,
    *,
    sequence: int,
    customer_series: str | None,
    customer_sequence: int | None,
    invoice_date: date,
) -> str:
    """Render a single placeholder token to its string value."""
    if m := re.fullmatch(r"SERIES:(.+)", token):
        return m.group(1)

    if m := re.fullmatch(r"SEQUENCE:(\d+)", token):
        width = int(m.group(1))
        return str(sequence).zfill(width)

    if token == "CUSTOMER_SERIES":
        return customer_series or ""

    if m := re.fullmatch(r"CUSTOMER_SEQUENCE:(\d+)", token):
        width = int(m.group(1))
        if customer_sequence is None:
            raise ValueError(
                "Template references {{CUSTOMER_SEQUENCE}} but no customer "
                "sequence was allocated."
            )
        return str(customer_sequence).zfill(width)

    if m := re.fullmatch(r"DATE:(.+)", token):
        fmt = m.group(1)
        if not _SAFE_DATE_FORMAT_RE.match(fmt):
            raise ValueError(f"DATE format contains disallowed characters: {fmt!r}.")
        return invoice_date.strftime(fmt)

    raise ValueError(f"Unknown invoice number placeholder: {{{{{token}}}}}")


# ---------------------------------------------------------------------------
# DB helpers – sequence row management
# ---------------------------------------------------------------------------


async def _upsert_company_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    document_type: str,
    sequence_start: int,
) -> None:
    """Insert the COMPANY-scope sequence row if it does not yet exist.

    Uses ``ON CONFLICT DO NOTHING`` to handle concurrent first-creation safely.
    """
    stmt = (
        pg_insert(NumberSequence)
        .values(
            company_id=company_id,
            document_type=document_type,
            scope=NumberSequenceScope.COMPANY,
            next_value=sequence_start,
        )
        .on_conflict_do_nothing(
            index_elements=["company_id", "document_type"],
            index_where=NumberSequence.scope == NumberSequenceScope.COMPANY,
        )
    )
    await session.execute(stmt)


async def _upsert_customer_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    document_type: str,
    customer_id: uuid.UUID,
) -> None:
    """Insert the CUSTOMER-scope sequence row if it does not yet exist.

    Customer-specific sequences always start at 1; the sequence_start setting
    only governs the company-scope counter.
    """
    stmt = (
        pg_insert(NumberSequence)
        .values(
            company_id=company_id,
            document_type=document_type,
            scope=NumberSequenceScope.CUSTOMER,
            customer_id=customer_id,
            next_value=1,
        )
        .on_conflict_do_nothing(
            index_elements=["company_id", "document_type", "customer_id"],
            index_where=NumberSequence.scope == NumberSequenceScope.CUSTOMER,
        )
    )
    await session.execute(stmt)


async def _lock_company_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    document_type: str,
) -> NumberSequence:
    """SELECT … FOR UPDATE the COMPANY-scope row.  Row must already exist."""
    stmt = (
        select(NumberSequence)
        .where(
            NumberSequence.company_id == company_id,
            NumberSequence.document_type == document_type,
            NumberSequence.scope == NumberSequenceScope.COMPANY,
        )
        .with_for_update()
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise RuntimeError(
            "Company sequence row not found after upsert. This should never happen."
        )
    return row


async def _lock_customer_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    document_type: str,
    customer_id: uuid.UUID,
) -> NumberSequence:
    """SELECT … FOR UPDATE the CUSTOMER-scope row.  Row must already exist."""
    stmt = (
        select(NumberSequence)
        .where(
            NumberSequence.company_id == company_id,
            NumberSequence.document_type == document_type,
            NumberSequence.scope == NumberSequenceScope.CUSTOMER,
            NumberSequence.customer_id == customer_id,
        )
        .with_for_update()
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise RuntimeError(
            "Customer sequence row not found after upsert. This should never happen."
        )
    return row


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def allocate_invoice_number(
    session: AsyncSession,
    company_id: uuid.UUID,
    customer_id: uuid.UUID,
    invoice_date: date,
    *,
    numbering_config: InvoiceNumberingConfig,
    customer_invoice_prefix: str | None,
) -> tuple[str, int, int | None]:
    """Allocate the next invoice number within the caller's open transaction.

    Parameters
    ----------
    session:
        The open async session (transaction started by the caller).
    company_id:
        Owning company.
    customer_id:
        The customer the invoice is being issued to (needed for customer-scope
        sequences and for the ``{{CUSTOMER_SERIES}}`` prefix).
    invoice_date:
        The invoice date (used for ``{{DATE:…}}`` placeholders).
    numbering_config:
        The ``InvoiceNumberingConfig`` read from COMPANY-level settings.
    customer_invoice_prefix:
        The customer's ``invoice_prefix`` field value (may be ``None``).

    Returns
    -------
    tuple[str, int, int | None]
        ``(invoice_number, sequence_number, customer_sequence_number|None)``.
        ``customer_sequence_number`` is ``None`` unless the template references
        ``{{CUSTOMER_SEQUENCE:n}}``.

    Notes
    -----
    The caller **must not** commit before using the returned invoice_number in
    the persisted invoice row; committing releases the row lock.
    """
    template = numbering_config.template
    sequence_start = numbering_config.sequence_start

    # Validate template before touching the DB
    validate_template(template)
    await _lock_invoice_number_namespace(session, company_id)

    # --- Company-scope sequence -----------------------------------------
    await _upsert_company_sequence(
        session, company_id, DOCUMENT_TYPE_INVOICE, sequence_start
    )
    company_seq_row = await _lock_company_sequence(
        session, company_id, DOCUMENT_TYPE_INVOICE
    )
    # --- Customer-scope sequence (only if template demands it) ----------
    # Both Standard and Credit numbers share Invoice.invoice_number's unique
    # namespace.  Keep the advisory lock until the caller inserts/commits, and
    # advance *both* counters for every occupied candidate so a skipped company
    # suffix never becomes paired with a stale customer suffix.
    customer_seq_row: NumberSequence | None = None
    if needs_customer_sequence(template):
        await _upsert_customer_sequence(
            session, company_id, DOCUMENT_TYPE_INVOICE, customer_id
        )
        customer_seq_row = await _lock_customer_sequence(
            session, company_id, DOCUMENT_TYPE_INVOICE, customer_id
        )
    for _ in range(1000):
        allocated_company = company_seq_row.next_value
        if allocated_company > POSTGRES_BIGINT_MAX:
            _raise_sequence_exhausted()
        customer_seq_value: int | None = None
        if customer_seq_row is not None:
            customer_seq_value = customer_seq_row.next_value
            if customer_seq_value > POSTGRES_BIGINT_MAX:
                _raise_sequence_exhausted()
        invoice_number = render_template(
            template,
            sequence=allocated_company,
            customer_series=customer_invoice_prefix,
            customer_sequence=customer_seq_value,
            invoice_date=invoice_date,
        )
        # An existing Credit can have used a custom template which overlaps a
        # Standard template (or vice versa).  The namespace lock serialises
        # the check with both allocators; the caller's INSERT remains in this
        # transaction, so no post-allocation insertion window exists.
        exists = await session.scalar(
            select(Invoice.id).where(
                Invoice.company_id == company_id, Invoice.invoice_number == invoice_number
            ).limit(1)
        )
        if exists is None:
            # BIGINT's last legal value is allocatable.  Leave the counter at
            # that value; a subsequent request observes the occupied candidate
            # and returns the stable exhaustion error without an overflow write.
            if allocated_company < POSTGRES_BIGINT_MAX:
                company_seq_row.next_value = allocated_company + 1
            if customer_seq_row is not None and customer_seq_value is not None:
                if customer_seq_value < POSTGRES_BIGINT_MAX:
                    customer_seq_row.next_value = customer_seq_value + 1
            await session.flush()
            return invoice_number, allocated_company, customer_seq_value
        if (
            allocated_company == POSTGRES_BIGINT_MAX
            or customer_seq_value == POSTGRES_BIGINT_MAX
        ):
            _raise_sequence_exhausted()
        company_seq_row.next_value = allocated_company + 1
        if customer_seq_row is not None and customer_seq_value is not None:
            customer_seq_row.next_value = customer_seq_value + 1
    _raise_sequence_exhausted()


async def get_next_sequence_info(
    session: AsyncSession,
    company_id: uuid.UUID,
    invoice_date: date,
    *,
    numbering_config: InvoiceNumberingConfig,
) -> tuple[int, str]:
    """Return (next_sequence, preview_number) without mutating the sequence.

    If no sequence row exists yet the ``sequence_start`` from the config is
    used as the hypothetical next value.
    """
    template = numbering_config.template
    sequence_start = numbering_config.sequence_start

    # Try to validate template (best-effort; bad templates return a placeholder)
    try:
        validate_template(template)
    except ValueError:
        return sequence_start, f"<invalid template: {template}>"

    # Read current next value without locking
    stmt = select(NumberSequence).where(
        NumberSequence.company_id == company_id,
        NumberSequence.document_type == DOCUMENT_TYPE_INVOICE,
        NumberSequence.scope == NumberSequenceScope.COMPANY,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    next_seq = row.next_value if row is not None else sequence_start

    try:
        preview = render_template(
            template,
            sequence=next_seq,
            customer_series=None,
            customer_sequence=1 if needs_customer_sequence(template) else None,
            invoice_date=invoice_date,
        )
    except ValueError:
        preview = "<render error>"

    return next_seq, preview


async def advance_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    new_next_value: int,
    *,
    numbering_config: InvoiceNumberingConfig,
) -> tuple[int, str]:
    """Advance the company invoice sequence to ``new_next_value`` (forward only).

    Parameters
    ----------
    new_next_value:
        The desired new ``next_value``.  Must be strictly greater than the
        current ``next_value`` (or any positive integer if the row does not
        exist yet).

    Returns
    -------
    tuple[int, str]
        ``(new_next_value, preview_number)`` after the update.

    Raises
    ------
    ValueError
        If ``new_next_value ≤ current next_value`` (can only advance forward).
    """
    sequence_start = numbering_config.sequence_start

    # Ensure the row exists first (creates with sequence_start if absent)
    await _upsert_company_sequence(
        session, company_id, DOCUMENT_TYPE_INVOICE, sequence_start
    )

    # Lock and read
    seq_row = await _lock_company_sequence(session, company_id, DOCUMENT_TYPE_INVOICE)

    if new_next_value <= seq_row.next_value:
        raise ValueError(
            f"Can only advance the sequence forward. "
            f"Current next value is {seq_row.next_value}; "
            f"requested {new_next_value}."
        )

    seq_row.next_value = new_next_value
    await session.flush()

    # Build preview
    today = date.today()
    try:
        validate_template(numbering_config.template)
        preview = render_template(
            numbering_config.template,
            sequence=new_next_value,
            customer_series=None,
            customer_sequence=1 if needs_customer_sequence(numbering_config.template) else None,
            invoice_date=today,
        )
    except ValueError:
        preview = "<invalid template>"

    return new_next_value, preview


async def allocate_credit_number(
    session: AsyncSession,
    company_id: uuid.UUID,
    credit_date: date,
    *,
    numbering_config: CreditNumberingConfig,
) -> tuple[str, int]:
    """Allocate an independent, company-scoped Credit Note number safely."""
    validate_credit_template(numbering_config.template)
    await _lock_invoice_number_namespace(session, company_id)
    await _upsert_company_sequence(
        session, company_id, DOCUMENT_TYPE_CREDIT_NOTE, numbering_config.sequence_start
    )
    row = await _lock_company_sequence(session, company_id, DOCUMENT_TYPE_CREDIT_NOTE)
    for _ in range(1000):
        allocated = row.next_value
        if allocated > POSTGRES_BIGINT_MAX:
            _raise_sequence_exhausted()
        number = render_template(
            numbering_config.template,
            sequence=allocated,
            customer_series=None,
            customer_sequence=None,
            invoice_date=credit_date,
        )
        # The shared invoice number unique constraint covers Standard and
        # Credit strings.  A custom Credit template may overlap a legacy
        # Standard string, so advance under the locked sequence and retry.
        exists = await session.scalar(
            select(Invoice.id).where(
                Invoice.company_id == company_id, Invoice.invoice_number == number
            ).limit(1)
        )
        if exists is None:
            if allocated < POSTGRES_BIGINT_MAX:
                row.next_value = allocated + 1
            await session.flush()
            return number, allocated
        if allocated == POSTGRES_BIGINT_MAX:
            _raise_sequence_exhausted()
        row.next_value = allocated + 1
    _raise_sequence_exhausted()


async def get_next_credit_sequence_info(
    session: AsyncSession,
    company_id: uuid.UUID,
    credit_date: date,
    *,
    numbering_config: CreditNumberingConfig,
) -> tuple[int, str]:
    """Preview the separate Credit Note counter without mutating it."""
    try:
        validate_credit_template(numbering_config.template)
    except ValueError:
        return numbering_config.sequence_start, f"<invalid template: {numbering_config.template}>"
    row = (
        await session.execute(
            select(NumberSequence).where(
                NumberSequence.company_id == company_id,
                NumberSequence.document_type == DOCUMENT_TYPE_CREDIT_NOTE,
                NumberSequence.scope == NumberSequenceScope.COMPANY,
            )
        )
    ).scalar_one_or_none()
    next_value = row.next_value if row is not None else numbering_config.sequence_start
    return (
        next_value,
        render_template(
            numbering_config.template,
            sequence=next_value,
            customer_series=None,
            customer_sequence=None,
            invoice_date=credit_date,
        ),
    )


async def advance_credit_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    new_next_value: int,
    *,
    numbering_config: CreditNumberingConfig,
) -> tuple[int, str]:
    """Forward-skip the independent Credit Note counter."""
    validate_credit_template(numbering_config.template)
    await _upsert_company_sequence(
        session, company_id, DOCUMENT_TYPE_CREDIT_NOTE, numbering_config.sequence_start
    )
    row = await _lock_company_sequence(session, company_id, DOCUMENT_TYPE_CREDIT_NOTE)
    if new_next_value <= row.next_value:
        raise ValueError(
            f"Can only advance the sequence forward. Current next value is {row.next_value}; "
            f"requested {new_next_value}."
        )
    row.next_value = new_next_value
    await session.flush()
    _, preview = await get_next_credit_sequence_info(
        session, company_id, date.today(), numbering_config=numbering_config
    )
    return new_next_value, preview


# ---------------------------------------------------------------------------
# Quote numbering – wrappers that pass DOCUMENT_TYPE_QUOTE
# ---------------------------------------------------------------------------


async def allocate_quote_number(
    session: AsyncSession,
    company_id: uuid.UUID,
    customer_id: uuid.UUID,
    quote_date: date,
    *,
    numbering_config: QuoteNumberingConfig,
    customer_invoice_prefix: str | None,
) -> tuple[str, int, int | None]:
    """Allocate the next quote number within the caller's open transaction.

    Mirrors ``allocate_invoice_number`` but uses ``DOCUMENT_TYPE_QUOTE``.
    """
    template = numbering_config.template
    sequence_start = numbering_config.sequence_start

    validate_template(template)

    await _upsert_company_sequence(
        session, company_id, DOCUMENT_TYPE_QUOTE, sequence_start
    )
    company_seq_row = await _lock_company_sequence(
        session, company_id, DOCUMENT_TYPE_QUOTE
    )
    allocated_company = company_seq_row.next_value
    company_seq_row.next_value = allocated_company + 1
    await session.flush()

    customer_seq_value: int | None = None
    if needs_customer_sequence(template):
        await _upsert_customer_sequence(
            session, company_id, DOCUMENT_TYPE_QUOTE, customer_id
        )
        cust_seq_row = await _lock_customer_sequence(
            session, company_id, DOCUMENT_TYPE_QUOTE, customer_id
        )
        customer_seq_value = cust_seq_row.next_value
        cust_seq_row.next_value = customer_seq_value + 1
        await session.flush()

    quote_number = render_template(
        template,
        sequence=allocated_company,
        customer_series=customer_invoice_prefix,
        customer_sequence=customer_seq_value,
        invoice_date=quote_date,
    )

    return quote_number, allocated_company, customer_seq_value


async def get_next_quote_sequence_info(
    session: AsyncSession,
    company_id: uuid.UUID,
    quote_date: date,
    *,
    numbering_config: QuoteNumberingConfig,
) -> tuple[int, str]:
    """Return (next_sequence, preview_number) without mutating the sequence."""
    template = numbering_config.template
    sequence_start = numbering_config.sequence_start

    try:
        validate_template(template)
    except ValueError:
        return sequence_start, f"<invalid template: {template}>"

    stmt = select(NumberSequence).where(
        NumberSequence.company_id == company_id,
        NumberSequence.document_type == DOCUMENT_TYPE_QUOTE,
        NumberSequence.scope == NumberSequenceScope.COMPANY,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    next_seq = row.next_value if row is not None else sequence_start

    try:
        preview = render_template(
            template,
            sequence=next_seq,
            customer_series=None,
            customer_sequence=1 if needs_customer_sequence(template) else None,
            invoice_date=quote_date,
        )
    except ValueError:
        preview = "<render error>"

    return next_seq, preview


async def advance_quote_sequence(
    session: AsyncSession,
    company_id: uuid.UUID,
    new_next_value: int,
    *,
    numbering_config: QuoteNumberingConfig,
) -> tuple[int, str]:
    """Advance the company quote sequence to ``new_next_value`` (forward only)."""
    sequence_start = numbering_config.sequence_start

    await _upsert_company_sequence(
        session, company_id, DOCUMENT_TYPE_QUOTE, sequence_start
    )

    seq_row = await _lock_company_sequence(session, company_id, DOCUMENT_TYPE_QUOTE)

    if new_next_value <= seq_row.next_value:
        raise ValueError(
            f"Can only advance the sequence forward. "
            f"Current next value is {seq_row.next_value}; "
            f"requested {new_next_value}."
        )

    seq_row.next_value = new_next_value
    await session.flush()

    today = date.today()
    try:
        validate_template(numbering_config.template)
        preview = render_template(
            numbering_config.template,
            sequence=new_next_value,
            customer_series=None,
            customer_sequence=1 if needs_customer_sequence(numbering_config.template) else None,
            invoice_date=today,
        )
    except ValueError:
        preview = "<invalid template>"

    return new_next_value, preview


# ---------------------------------------------------------------------------
# Utility: Decimal-compatible padding (not needed here, but kept for symmetry)
# ---------------------------------------------------------------------------

def _zfill_decimal(value: Decimal, width: int) -> str:
    """Left-pad an integer-valued Decimal with zeroes."""
    return str(int(value)).zfill(width)
