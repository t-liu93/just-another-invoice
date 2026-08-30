"""ICP (Opgaaf ICP) report service (M10 step 3).

Public API
----------
``compute_icp(session, company, year, quarter)``
    Compute the ICP quarterly report for the given company, year, and quarter.
    Returns an ``IcpReport``.

Design decisions (M10.md D2/D3/D4/D5/D6)
-----------------------------------------
D1  Pure-read: only SELECT queries, no writes, no new tables.
D2  Basis = invoice_date, quarterly (Q1=Jan–Mar … Q4=Oct–Dec).
D3  Revenue source: status ∈ {SENT, COMPLETED}; DRAFT/CANCELLED excluded.
D4  All amounts from ``base_taxable_amount`` (EUR).
D5  ICP = live join to customer at report time (no customer snapshot on invoice).
    Warns on missing vat_id or missing BILLING country_code.
D6  Sum already-persisted to-the-cent amounts; no re-rounding of the aggregate.

ICP source
----------
Only invoices where ``vat_treatment_requires_icp = True`` are included.
In v1 this is exclusively the ``EU_B2B_REVERSE`` treatment.

Grouping
--------
One line per customer (D5): sum of ``base_taxable_amount`` across all
matching invoices.  Each invoice's amount is first rounded to minor unit
(quantize_to_minor_unit) then accumulated—matching how btw.py accumulates
box 3b—so that ``total_net == btw.box_3b.base`` for the same quarter.

Warnings (D5)
-------------
Each EU-B2B customer missing ``vat_id`` or lacking a BILLING address with
a ``country_code`` triggers an advisory warning.  The customer still appears
in ``lines``; warnings are for filing completeness only.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models._enums import AddressType, InvoiceDocumentKind, InvoiceStatus
from jai.models.address import Address
from jai.models.customer import Customer
from jai.models.document import InvoiceCorrection, InvoiceCorrectionLine, InvoiceCreditBasisLine
from jai.models.invoice import Invoice
from jai.schemas.report import (
    IcpLine,
    IcpReport,
    ReportDocumentReference,
    ReportWarning,
    ReportWarningCode,
)
from jai.services.money import quantize_to_minor_unit
from jai.services.reporting.events import quarter_label

if TYPE_CHECKING:
    from jai.models.company import Company

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Quarter date helpers (mirrors btw.py)
# ---------------------------------------------------------------------------


def _quarter_date_range(year: int, quarter: int) -> tuple[date, date]:
    """Return the inclusive [from, to] date range for the given quarter."""
    import calendar

    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    end_day = calendar.monthrange(year, end_month)[1]
    return date(year, start_month, 1), date(year, end_month, end_day)


# ---------------------------------------------------------------------------
# Main async orchestration
# ---------------------------------------------------------------------------


async def compute_icp(
    session: AsyncSession,
    company: Company,
    year: int,
    quarter: int,
) -> IcpReport:
    """Compute the ICP (Opgaaf ICP) quarterly report for *company*.

    Parameters
    ----------
    session:
        Read-only async SQLAlchemy session.
    company:
        Company ORM object (needs ``id``).
    year:
        Calendar year (e.g. 2026).
    quarter:
        Quarter 1–4.

    Returns
    -------
    IcpReport
        Lines (one per customer), total_net, and warnings.
    """
    date_from, date_to = _quarter_date_range(year, quarter)
    company_id: uuid.UUID = company.id

    # -----------------------------------------------------------------------
    # 1. Fetch all ICP-eligible invoices in the quarter.
    #    - vat_treatment_requires_icp = True  (D-ICP source)
    #    - status ∈ {SENT, COMPLETED}          (D3)
    #    - invoice_date in [date_from, date_to] (D2)
    # -----------------------------------------------------------------------
    revenue_statuses = [InvoiceStatus.SENT, InvoiceStatus.COMPLETED]
    stmt_inv = select(Invoice).where(
        and_(
            Invoice.company_id == company_id,
            # Step 3 compatibility boundary; formal document tax events land
            # in Step 8 rather than accidentally traversing this legacy path.
            Invoice.document_kind == InvoiceDocumentKind.STANDARD,
            Invoice.vat_treatment_requires_icp.is_(True),
            Invoice.status.in_(revenue_statuses),
            Invoice.invoice_date >= date_from,
            Invoice.invoice_date <= date_to,
        )
    )
    result_inv = await session.execute(stmt_inv)
    invoices = list(result_inv.scalars().all())

    # -----------------------------------------------------------------------
    # 2. Group by customer_id, accumulate net amounts (D5/D6).
    #    Per-invoice: round base_taxable_amount to minor unit first, then sum.
    # -----------------------------------------------------------------------
    customer_net: dict[uuid.UUID, Decimal] = {}
    source_documents: dict[uuid.UUID, list[ReportDocumentReference]] = {}
    for inv in invoices:
        cust_id = inv.customer_id
        amount = quantize_to_minor_unit(Decimal(str(inv.base_taxable_amount)))
        customer_net[cust_id] = customer_net.get(cust_id, _ZERO) + amount
        source_documents.setdefault(cust_id, []).append(ReportDocumentReference(
            document_id=str(inv.id), document_kind=InvoiceDocumentKind.STANDARD,
            document_number=inv.invoice_number, event_date=inv.invoice_date,
        ))

    # Credit Notes inherit the exact cross-border/ICP source snapshots and
    # enter as negative dated correction events.  Formal Advance/Final are
    # NL-only by design, so they cannot add positive ICP rows.
    from sqlalchemy.orm import aliased

    source_invoice = aliased(Invoice)
    credit_result = await session.execute(
        select(
            Invoice, InvoiceCorrection, InvoiceCorrectionLine, InvoiceCreditBasisLine,
            source_invoice.invoice_number, source_invoice.invoice_date,
            source_invoice.document_kind,
        )
        .join(InvoiceCorrection, InvoiceCorrection.credit_note_id == Invoice.id)
        .join(InvoiceCorrectionLine, InvoiceCorrectionLine.correction_id == InvoiceCorrection.id)
        .join(
            InvoiceCreditBasisLine,
            InvoiceCreditBasisLine.id == InvoiceCorrectionLine.source_basis_line_id,
        )
        .join(source_invoice, source_invoice.id == InvoiceCorrection.source_invoice_id)
        .where(
            Invoice.company_id == company_id,
            Invoice.document_kind == InvoiceDocumentKind.CREDIT_NOTE,
            Invoice.status.in_(revenue_statuses),
            Invoice.invoice_date >= date_from,
            Invoice.invoice_date <= date_to,
            InvoiceCreditBasisLine.vat_treatment_requires_icp.is_(True),
        )
    )
    correction_warnings_by_credit: dict[uuid.UUID, ReportWarning] = {}
    credit_source_ids: dict[uuid.UUID, set[uuid.UUID]] = {}
    for (
        credit,
        correction,
        line,
        _basis,
        source_number,
        source_date,
        source_kind,
    ) in credit_result.all():
        amount = -quantize_to_minor_unit(Decimal(str(line.base_net_amount)))
        customer_net[credit.customer_id] = customer_net.get(credit.customer_id, _ZERO) + amount
        document = ReportDocumentReference(
            document_id=str(credit.id),
            document_kind=InvoiceDocumentKind.CREDIT_NOTE,
            document_number=credit.invoice_number,
            event_date=credit.invoice_date,
            source_document_id=str(correction.source_invoice_id),
            source_document_kind=source_kind,
            source_document_number=source_number,
        )
        credit_ids = credit_source_ids.setdefault(credit.customer_id, set())
        if credit.id not in credit_ids:
            source_documents.setdefault(credit.customer_id, []).append(document)
            credit_ids.add(credit.id)
        if (
            quarter_label(source_date) != quarter_label(credit.invoice_date)
            and credit.id not in correction_warnings_by_credit
        ):
            correction_warnings_by_credit[credit.id] = ReportWarning(
                code=ReportWarningCode.CREDIT_CROSS_PERIOD,
                message=("This Credit Note is dated in a different VAT period from its source. "
                         "Review the applicable correction process with your accountant."),
                document=document,
                source=ReportDocumentReference(
                    document_id=str(correction.source_invoice_id), document_kind=source_kind,
                    document_number=source_number, event_date=source_date,
                ),
                event_period=quarter_label(credit.invoice_date),
                source_period=quarter_label(source_date),
                amount=-quantize_to_minor_unit(
                    Decimal(str(correction.issued_base_gross_amount))
                ),
            )

    correction_warnings = list(correction_warnings_by_credit.values())

    if not customer_net:
        # Empty quarter: return immediately.
        return IcpReport(
            year=year, quarter=quarter, lines=[], total_net=_ZERO, warnings=[],
            correction_warnings=correction_warnings,
        )

    # -----------------------------------------------------------------------
    # 3. Fetch customer records and their BILLING addresses (D5).
    # -----------------------------------------------------------------------
    customer_ids = list(customer_net.keys())
    stmt_cust = select(Customer).where(
        and_(
            Customer.company_id == company_id,
            Customer.id.in_(customer_ids),
        )
    )
    result_cust = await session.execute(stmt_cust)
    customers = {c.id: c for c in result_cust.scalars().all()}

    # Fetch BILLING addresses for those customers.
    stmt_addr = select(Address).where(
        and_(
            Address.customer_id.in_(customer_ids),
            Address.type == AddressType.BILLING,
        )
    )
    result_addr = await session.execute(stmt_addr)
    billing_addresses: dict[uuid.UUID, Address] = {
        a.customer_id: a for a in result_addr.scalars().all()
    }

    # -----------------------------------------------------------------------
    # 4. Build lines and warnings (D5).
    # -----------------------------------------------------------------------
    lines: list[IcpLine] = []
    warnings: list[str] = []

    for cust_id in sorted(customer_net.keys(), key=str):  # deterministic order
        customer = customers.get(cust_id)
        net_amount = customer_net[cust_id]

        if customer is None:
            # Should never happen (invoice references customer in same company),
            # but be defensive.
            warnings.append(
                f"Customer {cust_id} not found; {net_amount:.2f} EUR of ICP invoices "
                "could not be attributed."
            )
            continue

        # Billing country_code: from BILLING address (D5).
        billing_addr = billing_addresses.get(cust_id)
        country_code: str | None = (
            billing_addr.country_code if billing_addr is not None else None
        )

        vat_id: str | None = customer.vat_id or None

        # Warnings for missing required fields (D5).
        if not vat_id:
            warnings.append(
                f"Customer '{customer.name}' is missing a VAT ID, "
                "which is required for the Opgaaf ICP declaration."
            )
        if not country_code:
            warnings.append(
                f"Customer '{customer.name}' has no billing country code, "
                "which is required for the Opgaaf ICP declaration."
            )

        lines.append(
            IcpLine(
                customer_id=str(cust_id),
                customer_name=customer.name,
                country_code=country_code,
                vat_id=vat_id,
                net_amount=net_amount,
                source_documents=sorted(
                    source_documents.get(cust_id, []),
                    key=lambda document: (document.event_date, document.document_id),
                ),
            )
        )

    total_net = sum((line.net_amount for line in lines), _ZERO)

    return IcpReport(
        year=year,
        quarter=quarter,
        lines=lines,
        total_net=total_net,
        warnings=warnings,
        correction_warnings=sorted(
            correction_warnings,
            key=lambda warning: (warning.document.event_date, warning.document.document_id),
        ),
    )
