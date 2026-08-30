"""Payment API routes – record, list, read, edit, and delete payments (M7).

Endpoints:
  POST   /api/v1/invoices/{invoice_id}/payments  – record a payment → 201
  GET    /api/v1/invoices/{invoice_id}/payments  – list payments for invoice → 200
  GET    /api/v1/payments                        – global payments overview → 200 (step 3)
  GET    /api/v1/payments/{id}                   – get a single payment → 200
  PUT    /api/v1/payments/{id}                   – edit a payment → 200
  DELETE /api/v1/payments/{id}                   – delete a payment → 200 (aggregate body)
"""
# ruff: noqa: E501

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from jai.auth.deps import current_mfa_user
from jai.db import get_session, set_rls_company
from jai.models._enums import InvoiceDocumentKind, PaymentDirection
from jai.models.user import User
from jai.schemas.artifact import DocumentArtifactListResponse, DocumentArtifactRead
from jai.schemas.email_log import DocumentSendRequest, EmailLogRead
from jai.schemas.payment import (
    InvoicePaymentsResponse,
    PaymentInput,
    PaymentInputErrorResponse,
    PaymentListResponse,
    PaymentMutationResponse,
    PaymentRead,
    QuotePaymentsResponse,
    RefundCollectionRead,
)
from jai.services.document_chain import ModeConflictError
from jai.services.payment import (
    RefundSettlementError,
    SettlementConflictError,
    delete_payment,
    get_payment,
    list_credit_refunds,
    list_invoice_payments,
    list_payments,
    list_quote_payments,
    record_payment,
    record_quote_payment,
    record_refund,
    update_payment,
)

router = APIRouter(prefix="/api/v1", tags=["payments"])

_PAYMENT_MUTATION_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": PaymentInputErrorResponse},
    status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": PaymentInputErrorResponse},
}


def _owner_only(user: User) -> None:
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required."
        )


def _require_company_id(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no company associated.",
        )
    return user.company_id


def _refund_error(exc: RefundSettlementError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


# ---------------------------------------------------------------------------
# Record a payment
# ---------------------------------------------------------------------------


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=InvoicePaymentsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment_endpoint(
    invoice_id: uuid.UUID,
    body: PaymentInput,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoicePaymentsResponse:
    """Record one payment for an invoice and return the updated aggregate."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await record_payment(
            session,
            invoice_id=invoice_id,
            company_id=company_id,
            body=body,
            creator_id=user.id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SettlementConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SETTLEMENT_STALE", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/quotes/{quote_id}/payments",
    response_model=QuotePaymentsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_quote_payment_endpoint(
    quote_id: uuid.UUID,
    body: PaymentInput,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuotePaymentsResponse:
    """Record one deposit on an accepted domestic quote."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await record_quote_payment(
            session,
            quote_id=quote_id,
            company_id=company_id,
            body=body,
            creator_id=user.id,
        )
    except ModeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SettlementConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SETTLEMENT_STALE", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/credit-notes/{credit_note_id}/refunds",
    response_model=RefundCollectionRead,
    responses=_PAYMENT_MUTATION_ERROR_RESPONSES,
)
async def list_credit_refunds_endpoint(
    credit_note_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> RefundCollectionRead:
    _owner_only(user)
    try:
        return await list_credit_refunds(session, credit_note_id, _require_company_id(user))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RefundSettlementError as exc:
        raise _refund_error(exc) from exc


@router.post(
    "/credit-notes/{credit_note_id}/refunds",
    response_model=RefundCollectionRead,
    status_code=status.HTTP_201_CREATED,
    responses=_PAYMENT_MUTATION_ERROR_RESPONSES,
)
async def record_refund_endpoint(
    credit_note_id: uuid.UUID,
    body: PaymentInput,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> RefundCollectionRead:
    _owner_only(user)
    try:
        return await record_refund(
            session, credit_note_id=credit_note_id, company_id=_require_company_id(user),
            body=body, creator_id=user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SettlementConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SETTLEMENT_STALE", "message": str(exc)},
        ) from exc
    except RefundSettlementError as exc:
        raise _refund_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "REFUND_INVALID_INPUT", "message": str(exc)},
        ) from exc


@router.get(
    "/payments/{refund_id}/refund-confirmation/preview",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def preview_refund_confirmation_endpoint(
    refund_id: uuid.UUID,
    locale: Literal["en", "zh"] | None = Query(default=None),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Render a live Refund Confirmation without retaining an artifact."""
    _owner_only(user)
    from jai.services.pdf import build_content_disposition, render_refund_confirmation_pdf
    pdf_bytes, filename, _, _ = await render_refund_confirmation_pdf(
        session, refund_id, _require_company_id(user), locale
    )
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": build_content_disposition(filename)})


@router.get(
    "/payments/{refund_id}/refund-confirmation",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_refund_confirmation_endpoint(
    refund_id: uuid.UUID,
    locale: Literal["en", "zh"] | None = Query(default=None),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Render, retain, then return exactly the retained PDF bytes."""
    _owner_only(user)
    from jai.models._enums import DocumentArtifactReason
    from jai.services.artifacts import retain_refund_artifact
    from jai.services.pdf import build_content_disposition, render_refund_confirmation_pdf
    company_id = _require_company_id(user)
    pdf_bytes, filename, resolved_locale, render_fingerprint = await render_refund_confirmation_pdf(session, refund_id, company_id, locale)
    artifact, _ = await retain_refund_artifact(session, refund_id=refund_id, company_id=company_id, pdf_bytes=pdf_bytes, render_fingerprint=render_fingerprint, locale=resolved_locale, filename=filename, reason=DocumentArtifactReason.DOWNLOAD)
    await session.commit()
    return Response(content=artifact.pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": build_content_disposition(artifact.filename)})


@router.post(
    "/payments/{refund_id}/send-refund-confirmation",
    response_model=EmailLogRead,
)
async def send_refund_confirmation_endpoint(
    refund_id: uuid.UUID,
    body: DocumentSendRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EmailLogRead:
    """Send the exact retained Refund Confirmation attachment."""
    _owner_only(user)
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from jai.models._enums import DocumentArtifactReason, EmailRelatedType
    from jai.models.company import Company
    from jai.models.customer import Customer
    from jai.models.document import InvoiceCorrection
    from jai.models.invoice import Invoice
    from jai.models.payment import Payment
    from jai.services.artifacts import retain_refund_artifact
    from jai.services.email import (
        failed_email_log_values,
        persist_failed_email_log,
        send_document_email,
    )
    from jai.services.pdf import render_refund_confirmation_pdf, snapshot_presentation

    company_id = _require_company_id(user)
    pdf_bytes, filename, resolved_locale, render_fingerprint = await render_refund_confirmation_pdf(session, refund_id, company_id, body.locale)
    refund = await session.scalar(select(Payment).where(Payment.id == refund_id, Payment.company_id == company_id, Payment.direction == PaymentDirection.REFUND, Payment.deleted_at.is_(None)))
    if refund is None or refund.credit_note_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found.")
    credit = await session.scalar(select(Invoice).where(Invoice.id == refund.credit_note_id, Invoice.company_id == company_id).options(selectinload(Invoice.party_snapshot)))
    correction = await session.scalar(select(InvoiceCorrection).where(InvoiceCorrection.credit_note_id == refund.credit_note_id))
    if credit is None or correction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund Credit Note not found.")
    source = await session.scalar(select(Invoice).where(Invoice.id == correction.source_invoice_id))
    company = await session.scalar(select(Company).where(Company.id == company_id))
    customer = await session.scalar(select(Customer).where(Customer.id == credit.customer_id))
    if source is None or company is None or customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund party not found.")
    presentation_company, presentation_customer = snapshot_presentation(
        credit.party_snapshot, company, customer
    )
    email_doc = SimpleNamespace(id=refund.id, invoice_number=credit.invoice_number, credit_note_number=credit.invoice_number, source_document_number=source.invoice_number, invoice_date=refund.payment_date, due_date=None, due_amount=credit.refund_due_amount, total_incl_vat=refund.amount, currency=refund.currency)
    cc = [item.strip() for item in body.cc.split(",") if item.strip()] if body.cc else None
    failed_values: dict[str, object] | None = None
    async with session.begin_nested() as artifact_savepoint:
        artifact, artifact_created = await retain_refund_artifact(session, refund_id=refund_id, company_id=company_id, pdf_bytes=pdf_bytes, render_fingerprint=render_fingerprint, locale=resolved_locale, filename=filename, reason=DocumentArtifactReason.SEND)
        log = await send_document_email(session, EmailRelatedType.REFUND, email_doc, presentation_company, presentation_customer, str(body.to), cc, resolved_locale, body.subject, body.body, artifact.pdf_bytes, filename, creator_id=user.id, artifact_id=artifact.id)
        if log.status.value == "FAILED" and artifact_created:
            failed_values = failed_email_log_values(log)
            await artifact_savepoint.rollback()
    if failed_values is not None:
        log = await persist_failed_email_log(session, failed_values)
    await session.commit()
    return EmailLogRead.model_validate(log)


@router.get("/payments/{refund_id}/artifacts", response_model=DocumentArtifactListResponse)
async def list_refund_artifacts_endpoint(refund_id: uuid.UUID, user: User = Depends(current_mfa_user), session: AsyncSession = Depends(get_session)) -> DocumentArtifactListResponse:
    _owner_only(user)
    from jai.services.artifacts import list_refund_artifacts
    rows = await list_refund_artifacts(session, refund_id=refund_id, company_id=_require_company_id(user))
    return DocumentArtifactListResponse(items=[DocumentArtifactRead.model_validate(row) for row in rows])


@router.get("/payments/{refund_id}/artifacts/{artifact_id}", response_class=Response)
async def download_refund_artifact_endpoint(refund_id: uuid.UUID, artifact_id: uuid.UUID, user: User = Depends(current_mfa_user), session: AsyncSession = Depends(get_session)) -> Response:
    _owner_only(user)
    from jai.services.artifacts import get_refund_artifact
    from jai.services.pdf import build_content_disposition
    artifact = await get_refund_artifact(session, refund_id=refund_id, artifact_id=artifact_id, company_id=_require_company_id(user))
    return Response(content=artifact.pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": build_content_disposition(artifact.filename)})


# ---------------------------------------------------------------------------
# List payments for an invoice
# ---------------------------------------------------------------------------


@router.get(
    "/invoices/{invoice_id}/payments",
    response_model=InvoicePaymentsResponse,
)
async def list_invoice_payments_endpoint(
    invoice_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> InvoicePaymentsResponse:
    """Return all payments for an invoice plus current payment state."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await list_invoice_payments(session, invoice_id, company_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get(
    "/quotes/{quote_id}/payments",
    response_model=QuotePaymentsResponse,
)
async def list_quote_payments_endpoint(
    quote_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> QuotePaymentsResponse:
    """Return quote-origin payments, including after conversion."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await list_quote_payments(session, quote_id, company_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Global payments overview (step 3)
# Note: this route MUST be registered before /payments/{payment_id} so that
# FastAPI matches the literal "/payments" path before the parameterised one.
# ---------------------------------------------------------------------------


@router.get("/payments", response_model=PaymentListResponse)
async def list_payments_endpoint(
    q: str | None = Query(
        default=None,
        description="Search invoice number, quote number, or customer name.",
    ),
    customer_id: uuid.UUID | None = Query(default=None),
    payment_method_id: uuid.UUID | None = Query(default=None),
    direction: PaymentDirection | None = Query(default=None),
    document_kind: InvoiceDocumentKind | None = Query(default=None),
    date_from: date | None = Query(
        default=None, description="Inclusive lower bound on payment_date."
    ),
    date_to: date | None = Query(
        default=None, description="Inclusive upper bound on payment_date."
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="payment_date", pattern="^(payment_date|created_at)$"),
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentListResponse:
    """Return a paginated global payments list filtered by the given parameters."""
    _owner_only(user)
    company_id = _require_company_id(user)
    return await list_payments(
        session,
        company_id,
        q=q,
        customer_id=customer_id,
        payment_method_id=payment_method_id,
        direction=direction,
        document_kind=document_kind,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )


# ---------------------------------------------------------------------------
# Get a single payment
# ---------------------------------------------------------------------------


@router.get("/payments/{payment_id}", response_model=PaymentRead)
async def get_payment_endpoint(
    payment_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentRead:
    """Fetch a single payment by id (scoped to caller's company)."""
    _owner_only(user)
    company_id = _require_company_id(user)
    p = await get_payment(session, payment_id, company_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found."
        )
    return p


# ---------------------------------------------------------------------------
# Payment receipt email
# ---------------------------------------------------------------------------


@router.post(
    "/payments/{payment_id}/send-receipt",
    response_model=EmailLogRead,
    status_code=status.HTTP_200_OK,
)
async def send_payment_receipt_email_endpoint(
    payment_id: uuid.UUID,
    body: DocumentSendRequest,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> EmailLogRead:
    """Send a localized payment-receipt PDF and audit it on its source document.

    Quote-origin payments always use their immutable quote/customer provenance,
    even after conversion.  Invoice-origin payments use their invoice source.
    The attachment is rendered by ``render_payment_receipt_pdf`` using the
    exact resolved locale chosen for the email.  The EmailLog related target
    likewise remains the source quote or invoice, so existing document email
    logs remain the complete audit surface.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from jai.models._enums import EmailRelatedType, SettingLevel
    from jai.models.company import Company
    from jai.models.customer import Customer
    from jai.models.invoice import Invoice
    from jai.models.payment import Payment
    from jai.models.quote import Quote
    from jai.schemas.setting import SETTING_KEY_DOCUMENT_DEFAULTS, DocumentDefaultsSetting
    from jai.services.email import (
        get_configured_smtp_config,
        payment_receipt_email_template,
        send_document_email,
    )
    from jai.services.pdf import render_payment_receipt_pdf, resolve_document_locale
    from jai.services.settings import get_setting

    _owner_only(user)
    company_id = _require_company_id(user)
    creator_id = user.id
    await set_rls_company(session, company_id)

    payment_result = await session.execute(
        select(Payment).where(
            Payment.id == payment_id, Payment.company_id == company_id, Payment.deleted_at.is_(None)
        )
    )
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found.")
    source_quote_id = payment.quote_id
    source_invoice_id = payment.invoice_id

    # Fail before renderer/receipt locks and before any audit write.  Retain
    # the validated config so the later SMTP operation performs no DB read.
    smtp_config = await get_configured_smtp_config(session)
    if smtp_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP is not configured.  Please set up SMTP settings before sending emails.",
        )
    await session.rollback()

    # quote_id is permanent provenance.  Never use a converted invoice as the
    # receipt source for a quote-origin payment.
    if source_quote_id is not None:
        source_result = await session.execute(
            select(Quote)
            .where(Quote.id == source_quote_id, Quote.company_id == company_id)
            .options(selectinload(Quote.lines), selectinload(Quote.taxes))
            .with_for_update(read=True)
        )
        source_document = source_result.scalar_one_or_none()
        related_type = EmailRelatedType.QUOTE
        not_found_detail = "Quote not found."
    elif source_invoice_id is not None:
        source_result = await session.execute(
            select(Invoice)
            .where(Invoice.id == source_invoice_id, Invoice.company_id == company_id)
            .options(selectinload(Invoice.lines), selectinload(Invoice.taxes))
            .with_for_update(read=True)
        )
        source_document = source_result.scalar_one_or_none()
        related_type = EmailRelatedType.INVOICE
        not_found_detail = "Invoice not found."
    else:
        source_document = None
        related_type = EmailRelatedType.INVOICE
        not_found_detail = "Payment document not found."
    if source_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)

    company_result = await session.execute(select(Company).where(Company.id == company_id))
    company = company_result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found.")

    customer_result = await session.execute(
        select(Customer)
        .where(
            Customer.id == source_document.customer_id,
            Customer.company_id == company_id,
        )
        .options(selectinload(Customer.addresses))
    )
    customer = customer_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")

    defaults = await get_setting(
        session,
        SETTING_KEY_DOCUMENT_DEFAULTS,
        level=SettingLevel.COMPANY,
        scope_id=company_id,
        value_type=DocumentDefaultsSetting,
    )
    company_locale: str | None = defaults.locale if defaults is not None else None
    resolved_locale = resolve_document_locale(body.locale, customer.locale, company_locale)

    # This is intentionally the same renderer as preview/download.  Passing
    # the resolved value makes the attachment and the email log locale match.
    pdf_bytes, filename = await render_payment_receipt_pdf(
        session=session,
        payment_id=payment_id,
        company_id=company_id,
        locale=resolved_locale,
    )

    # The renderer's parent FOR SHARE lock and the source lock above define the
    # receipt snapshot.  Freeze the small set of fields mail rendering needs,
    # then end this read transaction *before* SMTP network I/O.  Rollback is
    # intentional for this read-only transaction; get_session uses
    # expire_on_commit=False, while these plain snapshots also remain immune to
    # SQLAlchemy rollback expiration.
    if related_type == EmailRelatedType.INVOICE:
        assert isinstance(source_document, Invoice)
        source_snapshot = SimpleNamespace(
            id=source_document.id,
            invoice_number=source_document.invoice_number,
            invoice_date=source_document.invoice_date,
            due_date=source_document.due_date,
            currency=source_document.currency,
            total_incl_vat=source_document.total_incl_vat,
            due_amount=source_document.due_amount,
        )
    else:
        assert isinstance(source_document, Quote)
        source_snapshot = SimpleNamespace(
            id=source_document.id,
            quote_number=source_document.quote_number,
            quote_date=source_document.quote_date,
            valid_until=source_document.valid_until,
            currency=source_document.currency,
            total_incl_vat=source_document.total_incl_vat,
        )
    company_snapshot = SimpleNamespace(id=company.id, name=company.name)
    customer_snapshot = SimpleNamespace(name=customer.name)
    await session.rollback()

    # ``set_rls_company`` is transaction-local. The deliberate rollback above
    # releases the renderer's locks before SMTP I/O, so establish the tenant
    # again for the subsequent EmailLog INSERT under FORCE RLS.
    await set_rls_company(session, company_id)

    cc = [part.strip() for part in (body.cc or "").split(",") if part.strip()] or None
    log = await send_document_email(
        session=session,
        related_type=related_type,
        doc=source_snapshot,
        company=company_snapshot,
        customer=customer_snapshot,
        to=str(body.to),
        cc=cc,
        locale=resolved_locale,
        subject=body.subject,
        body=body.body,
        pdf_bytes=pdf_bytes,
        filename=filename,
        creator_id=creator_id,
        default_template=payment_receipt_email_template(resolved_locale),
        smtp_config=smtp_config,
    )
    await session.commit()
    return EmailLogRead.model_validate(log)


# ---------------------------------------------------------------------------
# Edit a payment (step 2)
# ---------------------------------------------------------------------------


@router.put(
    "/payments/{payment_id}",
    response_model=PaymentMutationResponse,
    responses=_PAYMENT_MUTATION_ERROR_RESPONSES,
)
async def update_payment_endpoint(
    payment_id: uuid.UUID,
    body: PaymentInput,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentMutationResponse:
    """Edit a payment and return every affected aggregate."""
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await update_payment(session, payment_id, company_id, body, actor_user_id=user.id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SettlementConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SETTLEMENT_STALE", "message": str(exc)},
        ) from exc
    except RefundSettlementError as exc:
        raise _refund_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PAYMENT_INVALID_INPUT", "message": str(exc)},
        ) from exc


# ---------------------------------------------------------------------------
# Delete a payment (step 2)
# ---------------------------------------------------------------------------


@router.delete(
    "/payments/{payment_id}",
    response_model=PaymentMutationResponse,
    responses=_PAYMENT_MUTATION_ERROR_RESPONSES,
)
async def delete_payment_endpoint(
    payment_id: uuid.UUID,
    user: User = Depends(current_mfa_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentMutationResponse:
    """Delete a payment and return the updated invoice aggregate.

    Returns 200 with the aggregate body (not 204) so the front-end gets the
    new due_amount / paid_status / status in a single round-trip.
    """
    _owner_only(user)
    company_id = _require_company_id(user)
    try:
        return await delete_payment(session, payment_id, company_id, actor_user_id=user.id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SettlementConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SETTLEMENT_STALE", "message": str(exc)},
        ) from exc
    except RefundSettlementError as exc:
        raise _refund_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PAYMENT_INVALID_INPUT", "message": str(exc)},
        ) from exc
