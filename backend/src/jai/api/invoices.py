"""Invoice API routes – calculate + CRUD + status + product options (M5 steps 1 & 3).

Endpoints:
  POST   /api/v1/invoices/calculate      – pricing preview (no persistence)
  GET    /api/v1/invoices                – paginated list with filters
  POST   /api/v1/invoices                – create invoice (allocate number)
  GET    /api/v1/invoices/{id}           – fetch single invoice
  PUT    /api/v1/invoices/{id}           – update invoice (preserve number)
  DELETE /api/v1/invoices/{id}           – delete invoice
  POST   /api/v1/invoices/{id}/status    – transition lifecycle status
  GET    /api/v1/invoice-product-options – customer-safe product search
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session, set_rls_company
from jai.models._enums import InvoiceDocumentKind
from jai.models.user import User
from jai.schemas.document_chain import DocumentChainRead
from jai.schemas.email_log import DocumentSendRequest, EmailLogListResponse, EmailLogRead
from jai.schemas.invoice import (
    AdvanceCalculationRead,
    AdvanceCalculationRequest,
    AdvanceDraftCreate,
    AdvanceDraftUpdate,
    CreditCalculationRead,
    CreditCalculationRequest,
    InvoiceCalculationRead,
    InvoiceCalculationRequest,
    InvoiceListResponse,
    InvoiceRead,
    InvoiceStatusWrite,
    InvoiceWrite,
    ProductInvoiceOptionListResponse,
)
from jai.services.advance import (
    AdvanceConflictError,
    AdvanceStaleError,
    AdvanceValidationError,
    calculate_advance,
    create_advance_draft,
    update_advance_draft,
)
from jai.services.document_chain import ModeConflictError, get_invoice_document_chain
from jai.services.invoice import (
    InvoiceDedicatedUpdateRequiredError,
    InvoiceLifecycleConflictError,
    create_invoice,
    delete_invoice,
    get_invoice,
    list_invoice_product_options,
    list_invoices,
    transition_status,
    update_invoice,
)
from jai.services.numbering import NumberSequenceExhaustedError
from jai.services.pricing import calculate_invoice

router = APIRouter(prefix="/api/v1", tags=["invoices"])


def _owner_only(user: User) -> None:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required.")


def _require_company_id(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


def _advance_error(status_code: int, code: str, exc: Exception) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": str(exc)})


async def _require_company(
    user: User,
    session: AsyncSession,
) -> tuple[uuid.UUID, str]:
    """Return (company_id, base_currency); raises 400 if no company is set."""
    from sqlalchemy import select

    from jai.models.company import Company

    company_id = _require_company_id(user)
    stmt = select(Company).where(Company.id == company_id)
    result = await session.execute(stmt)
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company not found.",
        )
    return company_id, company.base_currency


# ---------------------------------------------------------------------------
# Pricing preview (step 1)
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/calculate",
    response_model=InvoiceCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_invoice_endpoint(
    body: InvoiceCalculationRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceCalculationRead:
    """Preview invoice pricing without persisting (red-line 1)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await calculate_invoice(session, company_id=company_id, request=body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post(
    "/quotes/{quote_id}/advance-invoices/calculate",
    response_model=AdvanceCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_advance_endpoint(
    quote_id: uuid.UUID,
    body: AdvanceCalculationRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> AdvanceCalculationRead:
    """Preview a Formal Advance from immutable accepted-Quote snapshots."""
    _owner_only(user)
    try:
        return await calculate_advance(
            session, company_id=_require_company_id(user), quote_id=quote_id, request=body
        )
    except ModeConflictError as exc:
        raise _advance_error(409, exc.code, exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Quote not found.") from exc
    except (AdvanceStaleError, AdvanceConflictError) as exc:
        raise _advance_error(409, exc.code, exc) from exc
    except (AdvanceValidationError, ValueError) as exc:
        raise _advance_error(422, getattr(exc, "code", "ADVANCE_INVALID"), exc) from exc


@router.post(
    "/quotes/{quote_id}/advance-invoices",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_advance_endpoint(
    quote_id: uuid.UUID,
    body: AdvanceDraftCreate,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    try:
        return await create_advance_draft(
            session,
            company_id=_require_company_id(user),
            quote_id=quote_id,
            body=body,
            creator_id=user.id,
        )
    except ModeConflictError as exc:
        raise _advance_error(409, exc.code, exc) from exc
    except AdvanceConflictError as exc:
        raise _advance_error(409, exc.code, exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Quote not found.") from exc
    except (AdvanceValidationError, ValueError) as exc:
        raise _advance_error(422, getattr(exc, "code", "ADVANCE_INVALID"), exc) from exc


@router.put("/advance-invoices/{invoice_id}", response_model=InvoiceRead)
async def update_advance_endpoint(
    invoice_id: uuid.UUID,
    body: AdvanceDraftUpdate,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    try:
        result = await update_advance_draft(
            session,
            company_id=_require_company_id(user),
            invoice_id=invoice_id,
            body=body,
            actor_user_id=user.id,
        )
    except ModeConflictError as exc:
        raise _advance_error(409, exc.code, exc) from exc
    except AdvanceConflictError as exc:
        raise _advance_error(409, exc.code, exc) from exc
    except (AdvanceValidationError, ValueError) as exc:
        raise _advance_error(422, getattr(exc, "code", "ADVANCE_INVALID"), exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return result


@router.post(
    "/invoices/{source_invoice_id}/credit-notes/calculate",
    response_model=CreditCalculationRead,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def calculate_credit_placeholder(
    source_invoice_id: uuid.UUID,
    body: CreditCalculationRequest,
    user: User = Depends(current_mfa_user),
) -> CreditCalculationRead:
    """Publish the dedicated M12 intent without silently accepting it in Standard pricing."""
    _owner_only(user)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Credit calculation lands in M12 Step 5.",
    )


# ---------------------------------------------------------------------------
# Invoice CRUD (step 3)
# ---------------------------------------------------------------------------


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_invoices_endpoint(
    q: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    paid_status_filter: str | None = Query(default=None, alias="paid_status"),
    document_kind: InvoiceDocumentKind | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="invoice_date",
        pattern="^(invoice_date|created_at|invoice_number)$",
    ),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceListResponse:
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_invoices(
        session,
        company_id,
        q=q,
        customer_id=customer_id,
        status=status_filter,
        paid_status=paid_status_filter,
        document_kind=document_kind,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


@router.post(
    "/invoices",
    response_model=InvoiceRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_invoice_endpoint(
    body: InvoiceWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    """Create invoice: allocates number, calculates amounts, persists."""
    _owner_only(user)
    company_id, base_currency = await _require_company(user, session)
    try:
        return await create_invoice(
            session,
            body,
            company_id=company_id,
            company_currency=base_currency,
            creator_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/invoices/{invoice_id}", response_model=InvoiceRead)
async def get_invoice_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    inv = await get_invoice(session, invoice_id, company_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return inv


@router.get("/invoices/{invoice_id}/document-chain", response_model=DocumentChainRead)
async def get_invoice_document_chain_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentChainRead:
    _owner_only(user)
    result = await get_invoice_document_chain(
        session, company_id=_require_company_id(user), invoice_id=invoice_id
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return result


@router.put("/invoices/{invoice_id}", response_model=InvoiceRead)
async def update_invoice_endpoint(
    invoice_id: uuid.UUID,
    body: InvoiceWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    company_id, base_currency = await _require_company(user, session)
    try:
        inv = await update_invoice(
            session,
            invoice_id,
            body,
            company_id=company_id,
            company_currency=base_currency,
            actor_user_id=user.id,
        )
    except InvoiceDedicatedUpdateRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return inv


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        deleted = await delete_invoice(session, invoice_id, company_id, actor_user_id=user.id)
    except (AdvanceConflictError, InvoiceLifecycleConflictError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")


@router.post("/invoices/{invoice_id}/status", response_model=InvoiceRead)
async def transition_status_endpoint(
    invoice_id: uuid.UUID,
    body: InvoiceStatusWrite,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoiceRead:
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        inv = await transition_status(
            session, invoice_id, company_id, body, issued_by_user_id=user.id
        )
    except NumberSequenceExhaustedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except InvoiceLifecycleConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except AdvanceConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except AdvanceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return inv


# ---------------------------------------------------------------------------
# Invoice product options (step 3) – customer-safe projection
# ---------------------------------------------------------------------------


@router.get(
    "/invoice-product-options",
    response_model=ProductInvoiceOptionListResponse,
)
async def list_invoice_product_options_endpoint(
    q: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> ProductInvoiceOptionListResponse:
    """Customer-safe product search for invoice line auto-fill.

    Returns only id/name/unit/default_vat_rate_id – never cost/margin/supplier.
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_invoice_product_options(session, company_id, q=q, limit=limit)


# ---------------------------------------------------------------------------
# Invoice email send + log (M9 step 6)
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/{invoice_id}/send",
    response_model=EmailLogRead,
    status_code=status.HTTP_200_OK,
)
async def send_invoice_email_endpoint(
    invoice_id: uuid.UUID,
    body: DocumentSendRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EmailLogRead:
    """Send an invoice email with PDF attachment.

    Renders the PDF immediately (D5 – no cache), resolves the email template
    for the requested locale (D4), and sends via the configured SMTP (D6 –
    synchronous, no auto-retry).  Always writes an EmailLog row (SENT or FAILED).

    Returns the EmailLog row so the caller can display the send result.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from jai.models._enums import EmailRelatedType, SettingLevel
    from jai.models.company import Company
    from jai.models.customer import Customer
    from jai.models.invoice import Invoice
    from jai.schemas.setting import SETTING_KEY_DOCUMENT_DEFAULTS, DocumentDefaultsSetting
    from jai.services.email import send_document_email
    from jai.services.pdf import render_invoice_pdf, resolve_document_locale
    from jai.services.settings import get_setting

    _owner_only(user)
    company_id = _require_company_id(user)
    await set_rls_company(session, company_id)

    # -- Load invoice scoped to company (cross-company → 404) ------------------
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.company_id == company_id)
        .options(selectinload(Invoice.lines), selectinload(Invoice.taxes))
    )
    result = await session.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    # -- Issue-gated guard (D8) -----------------------------------------------
    # An unnumbered DRAFT carries no legal invoice number; it must be issued
    # (marked as Sent) before it can be emailed to the customer.
    if invoice.invoice_number is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Cannot email an unissued draft invoice; issue it (mark as Sent) first."),
        )

    # -- Load company ----------------------------------------------------------
    company_result = await session.execute(select(Company).where(Company.id == company_id))
    company = company_result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company not found.",
        )

    # -- Load customer (with addresses) ----------------------------------------
    customer_result = await session.execute(
        select(Customer)
        .where(Customer.id == invoice.customer_id)
        .options(selectinload(Customer.addresses))
    )
    customer = customer_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    # -- Resolve locale via D2 chain -------------------------------------------
    company_default_setting = await get_setting(
        session,
        SETTING_KEY_DOCUMENT_DEFAULTS,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=DocumentDefaultsSetting,
    )
    company_default_locale: str | None = (
        company_default_setting.locale if company_default_setting is not None else None
    )
    customer_locale: str | None = getattr(customer, "locale", None)
    resolved_locale = resolve_document_locale(body.locale, customer_locale, company_default_locale)

    # -- Render PDF immediately (D5) -------------------------------------------
    pdf_bytes, filename = await render_invoice_pdf(
        session, invoice_id, company_id, locale=resolved_locale
    )

    # -- Normalise CC ----------------------------------------------------------
    cc_list: list[str] | None = None
    if body.cc:
        raw_parts = [p.strip() for p in body.cc.split(",") if p.strip()]
        cc_list = raw_parts if raw_parts else None

    # -- Send email + write log ------------------------------------------------
    log = await send_document_email(
        session=session,
        related_type=EmailRelatedType.INVOICE,
        doc=invoice,
        company=company,
        customer=customer,
        to=str(body.to),
        cc=cc_list,
        locale=resolved_locale,
        subject=body.subject,
        body=body.body,
        pdf_bytes=pdf_bytes,
        filename=filename,
        creator_id=user.id,
    )

    await session.commit()
    return EmailLogRead.model_validate(log)


@router.get(
    "/invoices/{invoice_id}/emails",
    response_model=EmailLogListResponse,
)
async def list_invoice_emails_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EmailLogListResponse:
    """Return the email send log for an invoice (newest first)."""
    from sqlalchemy import select

    from jai.models._enums import EmailRelatedType
    from jai.models.email_log import EmailLog
    from jai.models.invoice import Invoice

    _owner_only(user)
    company_id = _require_company_id(user)

    # Verify invoice belongs to company (cross-company → 404).
    inv_result = await session.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == company_id)
    )
    if inv_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    stmt = (
        select(EmailLog)
        .where(
            EmailLog.company_id == company_id,
            EmailLog.related_type == EmailRelatedType.INVOICE,
            EmailLog.related_id == invoice_id,
        )
        .order_by(EmailLog.created_at.desc())
    )
    rows_result = await session.execute(stmt)
    rows = list(rows_result.scalars().all())

    return EmailLogListResponse(
        items=[EmailLogRead.model_validate(r) for r in rows],
        total=len(rows),
    )
