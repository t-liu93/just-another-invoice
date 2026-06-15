"""PDF rendering service – M9 step 1 / step 3 / step 4.

Public API
----------
- ``build_invoice_html``          – pure function: assemble Jinja2 context + render template.
- ``build_quote_html``            – pure function: assemble Jinja2 context + render quote template.
- ``build_payment_receipt_html``  – pure function: assemble Jinja2 context + render receipt.
- ``html_to_pdf``                 – thin WeasyPrint wrapper (lazily imported; D8).
- ``render_invoice_pdf``          – DB-backed orchestrator: load invoice, build HTML, render PDF.
- ``render_quote_pdf``            – DB-backed orchestrator: load quote, build HTML, render PDF.
- ``render_payment_receipt_pdf``  – DB-backed orchestrator: load payment, build HTML, render PDF.

Architecture decisions (from M9 D1–D8)
---------------------------------------
D1: WeasyPrint + Jinja2.  Font stack: Noto Sans / Noto Sans CJK SC (Docker runtime).
D5: Immediate rendering on every request – no on-disk cache.
D7: Custom url_fetcher blocks all non-data URIs (SSRF prevention).
    Logo is inlined as a data: URI from binary_asset.content (never a URL).
D8: build_*_html is a pure function with no system-library deps (default pytest).
    html_to_pdf imports WeasyPrint lazily so unit tests run without pango/cairo.

Red-line compliance
-------------------
1. Amounts come from DB snapshots – never recalculated.
2. company_id always injected by caller; cross-company → 404.
7. Jinja2 autoescape ON; url_fetcher blocks http/https/file.
"""

from __future__ import annotations

import base64
import logging
import re
import unicodedata
import urllib.parse
import uuid
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("jai.pdf")

# ---------------------------------------------------------------------------
# Template environment (autoescape ON – red-line 7)
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "pdf"
_CSS_PATH = _TEMPLATES_DIR / "base.css"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "htm"]),
    keep_trailing_newline=True,
)

# ---------------------------------------------------------------------------
# Jinja2 display filters (display only – red-line 1: no recalculation)
# ---------------------------------------------------------------------------


def _filter_money2(value: Any) -> str:
    """Format a monetary value to exactly 2 decimal places (display only).

    Uses ROUND_HALF_UP – same rounding rule as the pricing pipeline.
    Safe for None / empty: returns "0.00".
    Never recalculates; purely formats the snapshot value for display.
    """
    if value is None:
        return "0.00"
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return str(d)
    except Exception:
        return "0.00"


def _filter_pct(value: Any) -> str:
    """Format a VAT percentage, stripping insignificant trailing zeros.

    Examples: 21.000 → '21', 9.000 → '9', 21.500 → '21.5', None → '0'.
    """
    if value is None:
        return "0"
    try:
        d = Decimal(str(value)).normalize()
        # normalize() can produce scientific notation for very small/large numbers;
        # convert back to a plain string representation.
        return format(d, "f")
    except Exception:
        return str(value)


_jinja_env.filters["money2"] = _filter_money2
_jinja_env.filters["pct"] = _filter_pct


# ---------------------------------------------------------------------------
# Billing name helper (pure function)
# ---------------------------------------------------------------------------


def resolve_billing_name(customer: Any) -> str:
    """Derive the billing name for client-facing document headers.

    Priority: company_name → contact_name → name.

    ``name`` is the internal nickname (never printed directly on documents);
    it is used only as a guaranteed-non-null fallback.

    Empty strings are treated the same as ``None`` because the service layer
    normalises them to ``None`` before persisting, but callers may pass raw
    ORM objects where the DB value is already ``None``.
    """
    return (
        (customer.company_name or "").strip()
        or (customer.contact_name or "").strip()
        or (customer.name or "").strip()
    )


# ---------------------------------------------------------------------------
# i18n label table (backend-only; not reusing vue-i18n)
# ---------------------------------------------------------------------------

PDF_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "invoice": "Invoice",
        "invoice_number": "Invoice #",
        "reference": "Reference",
        "date": "Invoice Date",
        "due_date": "Due Date",
        "currency": "Currency",
        "bill_to": "BILL TO",
        "vat_id": "VAT ID",
        "coc": "KvK",
        "item": "Item",
        "description": "Description",
        "quantity": "Qty",
        "unit": "Unit",
        "unit_price": "Unit Price",
        "amount": "Amount (excl. VAT)",
        "vat": "VAT",
        "subtotal": "Subtotal",
        "discount": "Discount",
        "vat_total": "VAT",
        "total": "Total (incl. VAT)",
        "amount_due": "Amount Due",
        "notes": "Notes",
        "payment_terms": "Payment Terms",
        "bank_details": "Bank Details",
        "terms": "Terms & Conditions",
        "warranty": "Warranty",
        # Quote-specific labels (M9 step 3)
        "quote": "Quote",
        "quote_number": "Quote #",
        "quote_date": "Quote Date",
        "valid_until": "Valid Until",
        "status": "Status",
        # Receipt-specific labels (M9 step 4)
        "receipt": "Receipt",
        "payment_date": "Payment Date",
        "payment_method": "Payment Method",
        "amount_paid": "Amount Paid",
        "amount_paid_total": "Amount Paid (Total)",
        "receipt_amount_due": "Amount Due",
        "paid_status": "Paid Status",
    },
    "zh": {
        "invoice": "发票",
        "invoice_number": "发票号",
        "reference": "参考号",
        "date": "开票日期",
        "due_date": "到期日期",
        "currency": "币种",
        "bill_to": "账单收件人",
        "vat_id": "增值税号",
        "coc": "工商号",
        "item": "项目",
        "description": "描述",
        "quantity": "数量",
        "unit": "单位",
        "unit_price": "单价",
        "amount": "金额（不含税）",
        "vat": "增值税",
        "subtotal": "小计",
        "discount": "折扣",
        "vat_total": "增值税合计",
        "total": "合计（含税）",
        "amount_due": "应付款",
        "notes": "备注",
        "payment_terms": "付款条款",
        "bank_details": "银行信息",
        "terms": "条款与条件",
        "warranty": "质保说明",
        # Quote-specific labels (M9 step 3)
        "quote": "报价",
        "quote_number": "报价号",
        "quote_date": "报价日期",
        "valid_until": "有效期至",
        "status": "状态",
        # Receipt-specific labels (M9 step 4)
        "receipt": "收据",
        "payment_date": "收款日期",
        "payment_method": "收款方式",
        "amount_paid": "本次收款",
        "amount_paid_total": "已收款合计",
        "receipt_amount_due": "未收款",
        "paid_status": "收款状态",
    },
}

# Fallback locale for unknown locale values
_DEFAULT_LOCALE = "en"


def _get_labels(locale: str) -> dict[str, str]:
    """Return the label dict for *locale*, falling back to English."""
    return PDF_LABELS.get(locale, PDF_LABELS[_DEFAULT_LOCALE])


# ---------------------------------------------------------------------------
# Locale resolution chain (D2 – M9 step 2)
# ---------------------------------------------------------------------------


def resolve_document_locale(
    override: str | None,
    customer_locale: str | None,
    company_default: str | None,
) -> str:
    """Resolve the document language following the D2 priority chain.

    Resolution order (each step falls through to the next when the value is
    ``None`` or not a recognised locale):

    1. ``override``        – explicit locale from the export / send-email request.
    2. ``customer_locale`` – per-customer default (``customer.locale``).
    3. ``company_default`` – company-level default locale setting.
    4. ``"en"``            – hard-coded fallback.

    Parameters
    ----------
    override:
        Locale supplied by the caller (e.g. query-param ``locale=zh``).
        ``None`` means "not supplied".
    customer_locale:
        Value of ``customer.locale`` on the related customer ORM instance.
        ``None`` means the customer has no explicit preference.
    company_default:
        Value of the ``document.default_locale`` company setting (read from
        ``DocumentDefaultsSetting.locale``).  ``None`` if the setting has
        never been written.

    Returns
    -------
    str
        ``"en"`` or ``"zh"``.  Always a recognised value – never ``None``.
    """
    _valid = frozenset(PDF_LABELS)
    for candidate in (override, customer_locale, company_default):
        if candidate and candidate in _valid:
            return candidate
    return _DEFAULT_LOCALE


# ---------------------------------------------------------------------------
# RFC 6266 / RFC 5987 compliant Content-Disposition builder
# ---------------------------------------------------------------------------

# Characters unsafe in the quoted-string token of Content-Disposition filename.
# We strip C0/C1 control characters and DEL, and replace " and \ to prevent
# header injection or quote-boundary breakage.
_UNSAFE_CHARS_RE = re.compile(r'[\x00-\x1f\x7f-\x9f"\\]')


def _ascii_fallback(name: str) -> str:
    """Produce a latin-1-safe ASCII fallback filename.

    1. NFKD-normalise so accented chars decompose.
    2. Strip non-ASCII codepoints (covers CJK, emoji, accented residue …).
    3. Replace remaining unsafe chars (control chars, ``"``, ``\\``) with ``_``.
    4. Collapse to a non-empty string.
    """
    normalised = unicodedata.normalize("NFKD", name)
    ascii_only = normalised.encode("ascii", errors="ignore").decode("ascii")
    safe = _UNSAFE_CHARS_RE.sub("_", ascii_only)
    return safe or "invoice"


def build_content_disposition(filename: str) -> str:
    """Return a RFC 6266 / RFC 5987 ``Content-Disposition`` header value.

    Produces::

        attachment; filename="<ascii-safe>"; filename*=UTF-8''<percent-encoded>

    The ``filename=`` token is an ASCII/latin-1 safe fallback for older clients.
    The ``filename*=`` token carries the full UTF-8 filename percent-encoded per
    RFC 5987, and takes precedence in RFC 6266-compliant user-agents.

    The returned string is always latin-1 encodable (safe for ASGI header bytes).
    """
    ascii_name = _ascii_fallback(filename)
    # percent-encode the full original filename for filename* (RFC 5987 §3.2)
    # urllib.parse.quote encodes everything except unreserved chars by default.
    encoded_name = urllib.parse.quote(filename, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'


# ---------------------------------------------------------------------------
# SSRF-safe url_fetcher (D7)
# ---------------------------------------------------------------------------


def _safe_url_fetcher(url: str) -> dict[str, Any]:
    """WeasyPrint url_fetcher that only allows data: URIs.

    Any attempt to fetch http/https/file/ftp or any other scheme raises
    ValueError, preventing SSRF and local file reads.  Logo assets must be
    inlined as data: URIs before calling html_to_pdf.
    """
    if url.startswith("data:"):
        # Let WeasyPrint handle data URIs natively via its default fetcher.
        from weasyprint.urls import default_url_fetcher

        return default_url_fetcher(url)  # type: ignore[no-any-return]
    raise ValueError(
        f"URL scheme not allowed in PDF rendering (SSRF prevention): {url!r}"
    )


# ---------------------------------------------------------------------------
# Core rendering functions
# ---------------------------------------------------------------------------


def build_invoice_html(
    invoice: Any,
    company: Any,
    customer: Any,
    locale: str,
    logo_data_uri: str | None,
) -> str:
    """Render invoice HTML from ORM objects.

    This is a **pure function** – no I/O, no DB, no WeasyPrint.
    Amounts are read directly from snapshot fields; nothing is recalculated
    (red-line 1).  Jinja2 autoescape is ON (red-line 7).

    Parameters
    ----------
    invoice:
        ``Invoice`` ORM instance (with ``.lines`` and ``.taxes`` preloaded).
    company:
        ``Company`` ORM instance.
    customer:
        ``Customer`` ORM instance (with ``.addresses`` preloaded).
    locale:
        ``"en"`` or ``"zh"``.  Unknown values fall back to ``"en"``.
    logo_data_uri:
        A ``data:<mime>;base64,...`` string, or ``None`` if no logo is set.
        **Must never be a URL** (D7).

    Returns
    -------
    str
        Rendered HTML string (UTF-8).
    """
    from jai.models._enums import AddressType

    labels = _get_labels(locale)

    # -- Resolve billing address ----------------------------------------------
    billing_address: Any = None
    if hasattr(customer, "addresses"):
        for addr in customer.addresses:
            if addr.type == AddressType.BILLING:
                billing_address = addr
                break

    # -- Read CSS inline so WeasyPrint receives a self-contained HTML ---------
    css_text = _CSS_PATH.read_text(encoding="utf-8")

    # -- Build template context -----------------------------------------------
    context: dict[str, Any] = {
        "locale": locale if locale in PDF_LABELS else _DEFAULT_LOCALE,
        "labels": labels,
        "invoice": invoice,
        "company": company,
        "customer": customer,
        "billing_address": billing_address,
        "billing_name": resolve_billing_name(customer),
        "logo_data_uri": logo_data_uri,
        "css": css_text,
    }

    template = _jinja_env.get_template("invoice.html")
    return template.render(**context)


def html_to_pdf(html: str) -> bytes:
    """Convert an HTML string to PDF bytes using WeasyPrint.

    WeasyPrint is **lazily imported** inside this function so that default
    pytest unit tests (which test ``build_invoice_html``) don't require
    pango/cairo to be installed (D8).

    The custom ``_safe_url_fetcher`` is wired in to block all non-data URIs
    (D7 – SSRF prevention).

    Parameters
    ----------
    html:
        Fully rendered HTML string (e.g. from ``build_invoice_html``).

    Returns
    -------
    bytes
        Raw PDF bytes starting with ``%PDF-``.
    """
    # Lazy import – keeps the module importable without system PDF libs (D8).
    from weasyprint import HTML

    doc = HTML(string=html, url_fetcher=_safe_url_fetcher)
    pdf_bytes: bytes = doc.write_pdf()
    return pdf_bytes


async def render_invoice_pdf(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    company_id: uuid.UUID,
    locale: str | None = None,
) -> tuple[bytes, str]:
    """Load an invoice from the DB, render it as PDF, and return (bytes, filename).

    Steps
    -----
    1. Load Invoice (with lines + taxes via selectinload) scoped to company_id.
    2. Load Company and Customer.
    3. Resolve document locale via ``resolve_document_locale`` (D2 chain).
    4. Inline logo as data URI from binary_asset.content (never a URL).
    5. Call build_invoice_html → html_to_pdf.
    6. Return (pdf_bytes, "<invoice_number>.pdf").

    Parameters
    ----------
    locale:
        Explicit locale override (``"en"`` / ``"zh"``).  When ``None``, the
        locale is resolved from the D2 chain: customer.locale →
        company-default setting → ``"en"``.

    Raises
    ------
    HTTPException(404)
        If the invoice doesn't exist or belongs to a different company (red-line 2).
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from jai.models.binary_asset import BinaryAsset
    from jai.models.company import Company
    from jai.models.customer import Customer
    from jai.models.invoice import Invoice

    # -- Load invoice scoped to company (red-line 2) --------------------------
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.company_id == company_id)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.taxes),
        )
    )
    result = await session.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    # -- Load company ---------------------------------------------------------
    company_stmt = select(Company).where(Company.id == company_id)
    company_result = await session.execute(company_stmt)
    company = company_result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    # -- Load customer (with addresses) ---------------------------------------
    from sqlalchemy.orm import selectinload as _sil


    customer_stmt = (
        select(Customer)
        .where(Customer.id == invoice.customer_id)
        .options(_sil(Customer.addresses))
    )
    customer_result = await session.execute(customer_stmt)
    customer = customer_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    # -- Resolve locale via D2 chain (step 2) ---------------------------------
    # When locale is None (no explicit override), we read the company-level
    # document default and fall through: override → customer.locale →
    # company default → "en".
    from jai.models._enums import SettingLevel
    from jai.schemas.setting import SETTING_KEY_DOCUMENT_DEFAULTS, DocumentDefaultsSetting
    from jai.services.settings import get_setting

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
    resolved_locale = resolve_document_locale(locale, customer_locale, company_default_locale)

    # -- Inline logo as data: URI (D7 – never a URL) -------------------------
    logo_data_uri: str | None = None
    if company.logo_id is not None:
        asset_stmt = select(BinaryAsset).where(BinaryAsset.id == company.logo_id)
        asset_result = await session.execute(asset_stmt)
        asset = asset_result.scalar_one_or_none()
        if asset is not None:
            b64 = base64.b64encode(asset.content).decode("ascii")
            logo_data_uri = f"data:{asset.mime_type};base64,{b64}"

    # -- Build HTML + render PDF ----------------------------------------------
    html = build_invoice_html(
        invoice=invoice,
        company=company,
        customer=customer,
        locale=resolved_locale,
        logo_data_uri=logo_data_uri,
    )
    pdf_bytes = html_to_pdf(html)

    filename = f"{invoice.invoice_number}.pdf"
    return pdf_bytes, filename


# ---------------------------------------------------------------------------
# Quote PDF rendering (M9 step 3)
# ---------------------------------------------------------------------------


def build_quote_html(
    quote: Any,
    company: Any,
    customer: Any,
    locale: str,
    logo_data_uri: str | None,
) -> str:
    """Render quote HTML from ORM objects.

    This is a **pure function** – no I/O, no DB, no WeasyPrint.
    Amounts are read directly from snapshot fields; nothing is recalculated
    (red-line 1).  Jinja2 autoescape is ON (red-line 7).

    **Client-facing zero-leakage**: this function renders only the quote's
    own snapshot fields (lines, taxes, totals, content blocks).  Cost/margin/
    estimate data is never passed to the template and must never appear in the
    PDF output (M6.5 guard, extended to M9).

    Parameters
    ----------
    quote:
        ``Quote`` ORM instance (with ``.lines`` and ``.taxes`` preloaded).
    company:
        ``Company`` ORM instance.
    customer:
        ``Customer`` ORM instance (with ``.addresses`` preloaded).
    locale:
        ``"en"`` or ``"zh"``.  Unknown values fall back to ``"en"``.
    logo_data_uri:
        A ``data:<mime>;base64,...`` string, or ``None`` if no logo is set.
        **Must never be a URL** (D7).

    Returns
    -------
    str
        Rendered HTML string (UTF-8).
    """
    from jai.models._enums import AddressType

    labels = _get_labels(locale)

    # -- Resolve billing address ----------------------------------------------
    billing_address: Any = None
    if hasattr(customer, "addresses"):
        for addr in customer.addresses:
            if addr.type == AddressType.BILLING:
                billing_address = addr
                break

    # -- Read CSS inline so WeasyPrint receives a self-contained HTML ---------
    css_text = _CSS_PATH.read_text(encoding="utf-8")

    # -- Build template context (quote-only fields; no cost/margin) -----------
    context: dict[str, Any] = {
        "locale": locale if locale in PDF_LABELS else _DEFAULT_LOCALE,
        "labels": labels,
        "quote": quote,
        "company": company,
        "customer": customer,
        "billing_address": billing_address,
        "billing_name": resolve_billing_name(customer),
        "logo_data_uri": logo_data_uri,
        "css": css_text,
    }

    template = _jinja_env.get_template("quote.html")
    return template.render(**context)


# ---------------------------------------------------------------------------
# Payment receipt rendering (M9 step 4)
# ---------------------------------------------------------------------------


def build_payment_receipt_html(
    payment: Any,
    invoice: Any,
    company: Any,
    customer: Any,
    locale: str,
    logo_data_uri: str | None,
) -> str:
    """Render payment receipt HTML from ORM objects.

    This is a **pure function** – no I/O, no DB, no WeasyPrint.
    Amounts are read directly from snapshot fields; nothing is recalculated
    (red-line 1).  Jinja2 autoescape is ON (red-line 7).

    Snapshot fields used:
    - ``payment.amount``              – this payment's amount (snapshot).
    - ``payment.payment_date``        – date of this payment.
    - ``payment.payment_method_name`` – name snapshot (FK SET NULL safe).
    - ``payment.currency``            – currency of this payment.
    - ``payment.reference``           – optional reference (escaped).
    - ``payment.note``                – optional note (escaped).
    - ``invoice.invoice_number``      – related invoice identifier.
    - ``invoice.total_incl_vat``      – invoice total (snapshot).
    - ``invoice.due_amount``          – current amount due (snapshot).
    - ``invoice.paid_status``         – current paid status (snapshot).

    The "amount paid total" (= total − due) is derived from invoice snapshots
    only, **not recalculated** from summing payment rows.

    Parameters
    ----------
    payment:
        ``Payment`` ORM instance.
    invoice:
        ``Invoice`` ORM instance for the related invoice.
    company:
        ``Company`` ORM instance.
    customer:
        ``Customer`` ORM instance (with ``.addresses`` preloaded).
    locale:
        ``"en"`` or ``"zh"``.  Unknown values fall back to ``"en"``.
    logo_data_uri:
        A ``data:<mime>;base64,...`` string, or ``None`` if no logo is set.
        **Must never be a URL** (D7).

    Returns
    -------
    str
        Rendered HTML string (UTF-8).
    """
    from decimal import Decimal

    from jai.models._enums import AddressType

    labels = _get_labels(locale)

    # -- Resolve billing address ----------------------------------------------
    billing_address: Any = None
    if hasattr(customer, "addresses"):
        for addr in customer.addresses:
            if addr.type == AddressType.BILLING:
                billing_address = addr
                break

    # -- Read CSS inline so WeasyPrint receives a self-contained HTML ---------
    css_text = _CSS_PATH.read_text(encoding="utf-8")

    # -- Derive "paid total" from invoice snapshots (red-line 1: no re-sum) --
    # total_incl_vat and due_amount are both snapshot fields on invoice.
    # paid_total = total - due is a simple derived read-only value from the
    # same snapshots; we compute it here in Python (not in the template layer)
    # to keep arithmetic out of Jinja2.
    total_incl_vat: Any = invoice.total_incl_vat
    due_amount: Any = invoice.due_amount
    paid_total = Decimal(str(total_incl_vat)) - Decimal(str(due_amount))

    # -- Build template context (amounts from snapshots, not recalculated) ----
    context: dict[str, Any] = {
        "locale": locale if locale in PDF_LABELS else _DEFAULT_LOCALE,
        "labels": labels,
        "payment": payment,
        "invoice": invoice,
        "company": company,
        "customer": customer,
        "billing_address": billing_address,
        "billing_name": resolve_billing_name(customer),
        "logo_data_uri": logo_data_uri,
        "paid_total": paid_total,
        "css": css_text,
    }

    template = _jinja_env.get_template("receipt.html")
    return template.render(**context)


async def render_payment_receipt_pdf(
    session: AsyncSession,
    payment_id: uuid.UUID,
    company_id: uuid.UUID,
    locale: str | None = None,
) -> tuple[bytes, str]:
    """Load a payment from the DB, render a receipt PDF, and return (bytes, filename).

    Steps
    -----
    1. Load Payment scoped to company_id (cross-company → 404).
    2. Load related Invoice.
    3. Load Company and Customer (with addresses).
    4. Resolve document locale via ``resolve_document_locale`` (D2 chain).
    5. Inline logo as data URI from binary_asset.content (never a URL).
    6. Call build_payment_receipt_html → html_to_pdf.
    7. Return (pdf_bytes, "receipt-<invoice_number>-<payment_date>.pdf").

    Parameters
    ----------
    locale:
        Explicit locale override (``"en"`` / ``"zh"``).  When ``None``, the
        locale is resolved from the D2 chain: customer.locale →
        company-default setting → ``"en"``.

    Raises
    ------
    HTTPException(404)
        If the payment doesn't exist or belongs to a different company (red-line 2).
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from jai.models.binary_asset import BinaryAsset
    from jai.models.company import Company
    from jai.models.customer import Customer
    from jai.models.invoice import Invoice
    from jai.models.payment import Payment

    # -- Load payment scoped to company (red-line 2) --------------------------
    payment_stmt = select(Payment).where(
        Payment.id == payment_id, Payment.company_id == company_id
    )
    payment_result = await session.execute(payment_stmt)
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    # -- Load related invoice -------------------------------------------------
    invoice_stmt = select(Invoice).where(Invoice.id == payment.invoice_id)
    invoice_result = await session.execute(invoice_stmt)
    invoice = invoice_result.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    # -- Load company ---------------------------------------------------------
    company_stmt = select(Company).where(Company.id == company_id)
    company_result = await session.execute(company_stmt)
    company = company_result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    # -- Load customer (with addresses) ---------------------------------------
    from sqlalchemy.orm import selectinload as _sil

    customer_stmt = (
        select(Customer)
        .where(Customer.id == invoice.customer_id)
        .options(_sil(Customer.addresses))
    )
    customer_result = await session.execute(customer_stmt)
    customer = customer_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    # -- Resolve locale via D2 chain ------------------------------------------
    from jai.models._enums import SettingLevel
    from jai.schemas.setting import SETTING_KEY_DOCUMENT_DEFAULTS, DocumentDefaultsSetting
    from jai.services.settings import get_setting

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
    resolved_locale = resolve_document_locale(locale, customer_locale, company_default_locale)

    # -- Inline logo as data: URI (D7 – never a URL) -------------------------
    logo_data_uri: str | None = None
    if company.logo_id is not None:
        asset_stmt = select(BinaryAsset).where(BinaryAsset.id == company.logo_id)
        asset_result = await session.execute(asset_stmt)
        asset = asset_result.scalar_one_or_none()
        if asset is not None:
            b64 = base64.b64encode(asset.content).decode("ascii")
            logo_data_uri = f"data:{asset.mime_type};base64,{b64}"

    # -- Build HTML + render PDF ----------------------------------------------
    html = build_payment_receipt_html(
        payment=payment,
        invoice=invoice,
        company=company,
        customer=customer,
        locale=resolved_locale,
        logo_data_uri=logo_data_uri,
    )
    pdf_bytes = html_to_pdf(html)

    # Filename: receipt-<invoice_number>-<payment_date>.pdf
    filename = f"receipt-{invoice.invoice_number}-{payment.payment_date}.pdf"
    return pdf_bytes, filename


async def render_quote_pdf(
    session: AsyncSession,
    quote_id: uuid.UUID,
    company_id: uuid.UUID,
    locale: str | None = None,
) -> tuple[bytes, str]:
    """Load a quote from the DB, render it as PDF, and return (bytes, filename).

    Steps
    -----
    1. Load Quote (with lines + taxes via selectinload) scoped to company_id.
    2. Load Company and Customer.
    3. Resolve document locale via ``resolve_document_locale`` (D2 chain).
    4. Inline logo as data URI from binary_asset.content (never a URL).
    5. Call build_quote_html → html_to_pdf.
    6. Return (pdf_bytes, "<quote_number>.pdf").

    Parameters
    ----------
    locale:
        Explicit locale override (``"en"`` / ``"zh"``).  When ``None``, the
        locale is resolved from the D2 chain: customer.locale →
        company-default setting → ``"en"``.

    Raises
    ------
    HTTPException(404)
        If the quote doesn't exist or belongs to a different company (red-line 2).
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from jai.models.binary_asset import BinaryAsset
    from jai.models.company import Company
    from jai.models.customer import Customer
    from jai.models.quote import Quote, QuoteLine

    # -- Load quote scoped to company (red-line 2) ----------------------------
    stmt = (
        select(Quote)
        .where(Quote.id == quote_id, Quote.company_id == company_id)
        .options(
            selectinload(Quote.lines).selectinload(QuoteLine.line_taxes),
            selectinload(Quote.taxes),
        )
    )
    result = await session.execute(stmt)
    quote = result.scalar_one_or_none()
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found.",
        )

    # -- Load company ---------------------------------------------------------
    company_stmt = select(Company).where(Company.id == company_id)
    company_result = await session.execute(company_stmt)
    company = company_result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )

    # -- Load customer (with addresses) ---------------------------------------
    from sqlalchemy.orm import selectinload as _sil

    customer_stmt = (
        select(Customer)
        .where(Customer.id == quote.customer_id)
        .options(_sil(Customer.addresses))
    )
    customer_result = await session.execute(customer_stmt)
    customer = customer_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    # -- Resolve locale via D2 chain ------------------------------------------
    from jai.models._enums import SettingLevel
    from jai.schemas.setting import SETTING_KEY_DOCUMENT_DEFAULTS, DocumentDefaultsSetting
    from jai.services.settings import get_setting

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
    resolved_locale = resolve_document_locale(locale, customer_locale, company_default_locale)

    # -- Inline logo as data: URI (D7 – never a URL) -------------------------
    logo_data_uri: str | None = None
    if company.logo_id is not None:
        asset_stmt = select(BinaryAsset).where(BinaryAsset.id == company.logo_id)
        asset_result = await session.execute(asset_stmt)
        asset = asset_result.scalar_one_or_none()
        if asset is not None:
            b64 = base64.b64encode(asset.content).decode("ascii")
            logo_data_uri = f"data:{asset.mime_type};base64,{b64}"

    # -- Build HTML + render PDF ----------------------------------------------
    html = build_quote_html(
        quote=quote,
        company=company,
        customer=customer,
        locale=resolved_locale,
        logo_data_uri=logo_data_uri,
    )
    pdf_bytes = html_to_pdf(html)

    filename = f"{quote.quote_number}.pdf"
    return pdf_bytes, filename
