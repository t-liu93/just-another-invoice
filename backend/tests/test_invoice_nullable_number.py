"""DB-layer tests for nullable invoice number/sequence (deferred numbering fix).

Background: ``docs/plan/fixes/invoice-number-on-issue.md`` step 1. The invoice
legal number is allocated at the DRAFT -> SENT (issue) transition, not at
create, so a DRAFT carries NO number and deleting it leaves no gap.

This module verifies the *data-model* guarantees that make that safe:
- a draft with ``invoice_number = NULL`` and ``sequence_number = NULL`` can be
  inserted (DB accepts NULL);
- two NULL-numbered drafts coexist in one company (the unique constraint
  ``uq_invoice_company_number`` treats NULLs as distinct in Postgres);
- a duplicate *non-NULL* ``invoice_number`` within one company still violates
  the unique constraint (uniqueness is enforced once a number exists).

Requires a running PostgreSQL instance (``pytest -m integration``).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models._enums import (
    InvoicePaidStatus,
    InvoiceStatus,
    InvoiceTaxMode,
    VatTreatmentEffect,
    VatTreatmentSide,
)
from jai.models.company import Company
from jai.models.customer import Customer
from jai.models.invoice import Invoice
from jai.models.vat import VatTreatment

pytestmark = pytest.mark.integration

_ZERO = Decimal("0")


async def _seed_company_customer_treatment(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, VatTreatment]:
    """Insert a minimal company + customer + SALES VAT treatment.

    Returns ``(company_id, customer_id, treatment)``.
    """
    company_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    treatment = VatTreatment(
        id=uuid.uuid4(),
        company_id=company_id,
        code="NL_DOMESTIC",
        label="NL domestic",
        side=VatTreatmentSide.SALES,
        effect=VatTreatmentEffect.APPLY_RATE,
        requires_icp=False,
    )
    session.add(Company(id=company_id, name="Co", base_currency="EUR"))
    session.add(Customer(id=customer_id, company_id=company_id, name="Cust"))
    session.add(treatment)
    await session.flush()
    return company_id, customer_id, treatment


def _make_invoice(
    *,
    company_id: uuid.UUID,
    customer_id: uuid.UUID,
    treatment: VatTreatment,
    invoice_number: str | None,
    sequence_number: int | None,
) -> Invoice:
    """Build a minimal valid Invoice row with the given numbering values.

    Every NOT NULL column without a server_default is populated; numbering
    fields are passed through verbatim so a test can insert an unnumbered
    (NULL) draft or a numbered invoice.
    """
    inv = Invoice()
    inv.id = uuid.uuid4()
    inv.company_id = company_id
    inv.customer_id = customer_id
    inv.invoice_number = invoice_number
    inv.sequence_number = sequence_number
    inv.customer_sequence_number = None
    inv.invoice_date = date(2026, 6, 23)
    inv.status = InvoiceStatus.DRAFT
    inv.paid_status = InvoicePaidStatus.UNPAID
    inv.currency = "EUR"
    inv.tax_mode = InvoiceTaxMode.LINE
    inv.amounts_include_vat = False
    inv.vat_treatment_id = treatment.id
    inv.vat_treatment_code = treatment.code
    inv.vat_treatment_label = treatment.label
    inv.vat_treatment_effect = treatment.effect.value
    inv.vat_treatment_requires_icp = treatment.requires_icp
    # Calculated amounts (zero is a valid persisted value)
    inv.subtotal_excl_vat = _ZERO
    inv.line_discount_total = _ZERO
    inv.taxable_amount = _ZERO
    inv.vat_total = _ZERO
    inv.total_incl_vat = _ZERO
    inv.due_amount = _ZERO
    inv.base_subtotal_excl_vat = _ZERO
    inv.base_line_discount_total = _ZERO
    inv.base_taxable_amount = _ZERO
    inv.base_vat_total = _ZERO
    inv.base_total_incl_vat = _ZERO
    inv.base_due_amount = _ZERO
    return inv


class TestNullableInvoiceNumber:
    """Verify the DB accepts NULL number/sequence and uniqueness still holds."""

    async def test_insert_unnumbered_draft_succeeds(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A draft with NULL invoice_number/sequence_number is accepted."""
        async with db_session_maker() as session:
            company_id, customer_id, treatment = (
                await _seed_company_customer_treatment(session)
            )
            inv = _make_invoice(
                company_id=company_id,
                customer_id=customer_id,
                treatment=treatment,
                invoice_number=None,
                sequence_number=None,
            )
            session.add(inv)
            await session.commit()

            stored = await session.get(Invoice, inv.id)
            assert stored is not None
            assert stored.invoice_number is None
            assert stored.sequence_number is None

    async def test_two_null_drafts_coexist_same_company(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """Two NULL-numbered drafts in one company do not collide.

        Postgres treats NULLs as distinct, so the unique constraint
        ``uq_invoice_company_number`` does not block multiple unnumbered drafts.
        """
        async with db_session_maker() as session:
            company_id, customer_id, treatment = (
                await _seed_company_customer_treatment(session)
            )
            inv_a = _make_invoice(
                company_id=company_id,
                customer_id=customer_id,
                treatment=treatment,
                invoice_number=None,
                sequence_number=None,
            )
            inv_b = _make_invoice(
                company_id=company_id,
                customer_id=customer_id,
                treatment=treatment,
                invoice_number=None,
                sequence_number=None,
            )
            session.add_all([inv_a, inv_b])
            # Must NOT raise IntegrityError.
            await session.commit()

            assert inv_a.id != inv_b.id

    async def test_duplicate_non_null_number_violates_unique(
        self,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A duplicate non-NULL invoice_number within a company is rejected."""
        async with db_session_maker() as session:
            company_id, customer_id, treatment = (
                await _seed_company_customer_treatment(session)
            )
            inv_a = _make_invoice(
                company_id=company_id,
                customer_id=customer_id,
                treatment=treatment,
                invoice_number="INV-0001",
                sequence_number=1,
            )
            session.add(inv_a)
            await session.commit()

            inv_b = _make_invoice(
                company_id=company_id,
                customer_id=customer_id,
                treatment=treatment,
                invoice_number="INV-0001",  # same number, same company
                sequence_number=2,
            )
            session.add(inv_b)
            with pytest.raises(IntegrityError):
                await session.commit()
