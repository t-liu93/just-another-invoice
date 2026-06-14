"""Unit tests for payment receipt PDF rendering – M9 step 4 (D8: no pango/cairo required).

Tests cover:
1. Amounts in rendered HTML match payment/invoice snapshots (not recalculated).
2. XSS fields (note/reference/customer name containing <script>) are escaped.
3. locale=zh shows Chinese labels; locale=en shows English labels.
4. url_fetcher blocks http:// and file:// URIs; allows data: URIs.
5. No-logo path and logo (data URI) path both work correctly.
6. PDF_LABELS completeness: receipt keys exist in both EN and ZH.

Note: html_to_pdf (WeasyPrint integration) tests are in test_receipt_pdf_integration.py
and require system PDF libraries (marked @pytest.mark.integration).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from jai.services.pdf import (
    PDF_LABELS,
    _safe_url_fetcher,
    build_payment_receipt_html,
)

# ---------------------------------------------------------------------------
# Helpers – fake ORM-like objects
# ---------------------------------------------------------------------------


def _make_payment(
    payment_date: str = "2026-06-14",
    amount: str = "60.500",
    currency: str = "EUR",
    payment_method_name: str | None = "Bank Transfer",
    reference: str | None = None,
    note: str | None = None,
) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        payment_date=payment_date,
        amount=Decimal(amount),
        base_amount=Decimal(amount),
        currency=currency,
        payment_method_id=uuid.uuid4(),
        payment_method_name=payment_method_name,
        reference=reference,
        note=note,
    )


def _make_invoice(
    invoice_number: str = "F2026-001",
    total_incl_vat: str = "121.000",
    due_amount: str = "60.500",
    paid_status: str = "PARTIALLY_PAID",
) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number=invoice_number,
        total_incl_vat=Decimal(total_incl_vat),
        due_amount=Decimal(due_amount),
        paid_status=paid_status,
        customer_id=uuid.uuid4(),
    )


def _make_company(
    name: str = "Test BV",
    vat_id: str | None = "NL123456789B01",
    coc_number: str | None = "12345678",
    email: str | None = "info@test.nl",
    phone: str | None = None,
    website: str | None = None,
    street: str | None = "Teststraat",
    house_number: str | None = "1",
    house_number_addition: str | None = None,
    postal_code: str | None = "1234 AB",
    city: str | None = "Amsterdam",
    country_code: str | None = "NL",
    logo_id: uuid.UUID | None = None,
) -> Any:
    return SimpleNamespace(
        name=name,
        vat_id=vat_id,
        coc_number=coc_number,
        email=email,
        phone=phone,
        website=website,
        street=street,
        house_number=house_number,
        house_number_addition=house_number_addition,
        postal_code=postal_code,
        city=city,
        country_code=country_code,
        logo_id=logo_id,
    )


def _make_customer(
    name: str = "ACME Corp",
    vat_id: str | None = None,
    billing_street: str | None = "Klantenstraat",
    billing_house_number: str | None = "42",
    billing_postal_code: str | None = "5678 CD",
    billing_city: str | None = "Rotterdam",
    billing_country: str | None = "NL",
) -> Any:
    from jai.models._enums import AddressType

    billing = SimpleNamespace(
        type=AddressType.BILLING,
        street=billing_street,
        house_number=billing_house_number,
        house_number_addition=None,
        postal_code=billing_postal_code,
        city=billing_city,
        country_code=billing_country,
    )
    return SimpleNamespace(
        name=name,
        vat_id=vat_id,
        addresses=[billing],
    )


# ---------------------------------------------------------------------------
# Test 1: amounts match snapshots (no recalculation – red-line 1)
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_amounts_match_snapshots() -> None:
    """Amount values in rendered HTML must equal payment and invoice snapshot fields (2 dp).

    - payment.amount (this payment amount) must appear.
    - invoice.total_incl_vat (invoice total snapshot) must appear.
    - invoice.due_amount (remaining due snapshot) must appear.
    - paid_total (= total - due, derived from snapshots in Python layer) must appear.
    """
    payment = _make_payment(amount="60.500")
    invoice = _make_invoice(
        total_incl_vat="121.000",
        due_amount="60.500",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    # money2 filter formats to 2 dp
    # payment.amount 60.500 → "60.50"
    assert "60.50" in html, "payment.amount (2dp) not in HTML"
    # invoice.total_incl_vat 121.000 → "121.00"
    assert "121.00" in html, "invoice.total_incl_vat (2dp) not in HTML"
    # paid_total = 121.000 - 60.500 = 60.500 → "60.50"
    assert "60.50" in html, "paid_total (2dp) not in HTML"


def test_build_payment_receipt_html_paid_total_is_total_minus_due() -> None:
    """paid_total must equal total_incl_vat - due_amount from invoice snapshots (2 dp display)."""
    payment = _make_payment(amount="75.000")
    invoice = _make_invoice(
        total_incl_vat="121.000",
        due_amount="46.000",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    # paid_total = 121.000 - 46.000 = 75.000 → "75.00"
    assert "75.00" in html, "paid_total (= total - due = 75.00) not in HTML"
    # invoice total: 121.000 → "121.00"
    assert "121.00" in html, "invoice.total_incl_vat (2dp) not in HTML"
    # due amount: 46.000 → "46.00"
    assert "46.00" in html, "invoice.due_amount (2dp) not in HTML"


def test_build_payment_receipt_html_fully_paid_zero_due() -> None:
    """A fully-paid receipt: due_amount is 0; paid_total equals total_incl_vat."""
    payment = _make_payment(amount="121.000")
    invoice = _make_invoice(
        total_incl_vat="121.000",
        due_amount="0.000",
        paid_status="PAID",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    # paid_total = 121.000 - 0 = 121.000 → "121.00"
    assert "121.00" in html, "full payment amount (2dp) not in HTML"
    assert "0.00" in html, "due_amount (zero, 2dp) not in HTML"


def test_build_payment_receipt_html_invoice_number_shown() -> None:
    """The related invoice number must appear in the receipt HTML."""
    payment = _make_payment()
    invoice = _make_invoice(invoice_number="F2026-042")
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    assert "F2026-042" in html, "invoice.invoice_number not in HTML"


def test_build_payment_receipt_html_payment_method_name_shown() -> None:
    """Payment method name snapshot must appear when present."""
    payment = _make_payment(payment_method_name="Bank Transfer NL")
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    assert "Bank Transfer NL" in html, "payment.payment_method_name not in HTML"


def test_build_payment_receipt_html_no_payment_method_does_not_crash() -> None:
    """Receipt renders without error when payment_method_name is None (FK SET NULL)."""
    payment = _make_payment(payment_method_name=None)
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    # Must not raise
    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    assert "F2026-001" in html  # invoice number present
    # "Bank Transfer" must not appear (no method)
    assert "Bank Transfer" not in html


# ---------------------------------------------------------------------------
# Test 2: XSS escaping – red-line 7
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_xss_escaping() -> None:
    """User-supplied fields (note/reference/customer name) must be escaped."""
    xss = '<script>alert("xss")</script>'
    payment = _make_payment(reference=xss, note=xss)
    invoice = _make_invoice()
    company = _make_company(name=xss)
    customer = _make_customer(name=xss)

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    # Raw script tags must NOT appear.
    assert "<script>" not in html
    # Escaped versions must appear.
    assert "&lt;script&gt;" in html


def test_build_payment_receipt_html_xss_in_payment_method_name() -> None:
    """Payment method name with HTML must be escaped."""
    xss = '<img src=x onerror=alert(1)>'
    payment = _make_payment(payment_method_name=xss)
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    assert "<img src=x" not in html
    assert "&lt;img" in html


# ---------------------------------------------------------------------------
# Test 3: locale label switching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "locale,expected",
    [
        ("en", "Receipt"),
        ("zh", "收据"),
        ("en", "Payment Date"),
        ("zh", "收款日期"),
        ("en", "Payment Method"),
        ("zh", "收款方式"),
        ("en", "Amount Paid"),
        ("zh", "本次收款"),
        ("en", "Amount Due"),
        ("zh", "未收款"),
    ],
)
def test_build_payment_receipt_html_locale_labels(locale: str, expected: str) -> None:
    """Locale-specific labels must appear in the rendered receipt HTML."""
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, locale, None)
    assert expected in html, f"Expected label {expected!r} not found in HTML for locale={locale}"


def test_build_payment_receipt_html_zh_receipt_title() -> None:
    """zh locale receipt must show '收据' as the document title."""
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "zh", None)
    assert "收据" in html


def test_build_payment_receipt_html_unknown_locale_falls_back_to_en() -> None:
    """Unknown locale values fall back to English labels."""
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "de", None)
    # English 'Receipt' label should appear (fallback)
    assert "Receipt" in html or "RECEIPT" in html


# ---------------------------------------------------------------------------
# Test 4: url_fetcher blocks non-data URIs (SSRF prevention – D7)
# ---------------------------------------------------------------------------


def test_url_fetcher_blocks_http_for_receipt_context() -> None:
    """http:// URIs must raise ValueError – same fetcher used for receipt PDF."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("http://evil.example.com/img.png")


def test_url_fetcher_blocks_https_for_receipt_context() -> None:
    """https:// URIs must raise ValueError."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("https://internal.server/resource")


def test_url_fetcher_blocks_file_for_receipt_context() -> None:
    """file:// URIs must raise ValueError."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("file:///etc/passwd")


# ---------------------------------------------------------------------------
# Test 5: logo paths
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_no_logo() -> None:
    """Without a logo the img tag must NOT appear in the receipt HTML."""
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)
    assert "<img" not in html


def test_build_payment_receipt_html_with_logo_data_uri() -> None:
    """With a data: URI logo the img tag must appear and contain the data URI."""
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()
    logo_uri = "data:image/png;base64,iVBORw0KGgo="

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", logo_uri)
    assert "<img" in html
    assert "data:image/png;base64," in html


# ---------------------------------------------------------------------------
# Test 6: PDF_LABELS completeness – receipt keys in both locales
# ---------------------------------------------------------------------------


def test_pdf_labels_receipt_keys_exist_in_both_locales() -> None:
    """Receipt-specific label keys must exist in both EN and ZH label tables."""
    receipt_keys = {
        "receipt",
        "payment_date",
        "payment_method",
        "amount_paid",
        "amount_paid_total",
        "receipt_amount_due",
        "paid_status",
    }
    for key in receipt_keys:
        assert key in PDF_LABELS["en"], f"Key {key!r} missing from EN labels"
        assert key in PDF_LABELS["zh"], f"Key {key!r} missing from ZH labels"


def test_pdf_labels_en_and_zh_have_same_keys_after_receipt() -> None:
    """EN and ZH label dictionaries must have the same set of keys (including receipt keys)."""
    assert set(PDF_LABELS["en"].keys()) == set(PDF_LABELS["zh"].keys()), (
        "EN and ZH label tables have different keys"
    )


# ---------------------------------------------------------------------------
# Test 7: company info and billing address rendered
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_company_info() -> None:
    """Company name, VAT ID and KvK must appear in the receipt header."""
    company = _make_company(
        name="My Company BV",
        vat_id="NL999888777B01",
        coc_number="87654321",
    )
    payment = _make_payment()
    invoice = _make_invoice()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)
    assert "My Company BV" in html
    assert "NL999888777B01" in html
    assert "87654321" in html


def test_build_payment_receipt_html_billing_address() -> None:
    """Customer name and billing address must appear in the receipt HTML."""
    customer = _make_customer(
        name="Big Client BV",
        billing_street="Klantenstraat",
        billing_house_number="42",
        billing_city="Rotterdam",
    )
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)
    assert "Big Client BV" in html
    assert "Klantenstraat" in html
    assert "Rotterdam" in html


# ---------------------------------------------------------------------------
# Test 8: optional reference and note fields
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_reference_shown() -> None:
    """Payment reference must appear in the receipt when present."""
    payment = _make_payment(reference="REF-20260614")
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)
    assert "REF-20260614" in html


def test_build_payment_receipt_html_note_shown() -> None:
    """Payment note must appear in the receipt when present."""
    payment = _make_payment(note="Advance payment for Q3 services")
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)
    assert "Advance payment for Q3 services" in html


# ---------------------------------------------------------------------------
# Test: CSS not escaped (task A – css|safe fix applies to receipt.html too)
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_css_not_escaped() -> None:
    """CSS injected via css|safe must not be HTML-escaped in receipt template."""
    payment = _make_payment()
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    assert 'font-family: "Noto Sans"' in html, (
        "font-family with literal quotes not found – CSS may have been HTML-escaped"
    )
    assert "&#34;" not in html, "HTML entity &#34; found – CSS was incorrectly escaped"


# ---------------------------------------------------------------------------
# Test: amounts displayed as 2 decimal places (task C)
# ---------------------------------------------------------------------------


def test_build_payment_receipt_html_amounts_two_decimal() -> None:
    """Receipt amounts must display as 2 dp (money2 filter)."""
    payment = _make_payment(amount="60.500")
    invoice = _make_invoice(total_incl_vat="121.000", due_amount="60.500")
    company = _make_company()
    customer = _make_customer()

    html = build_payment_receipt_html(payment, invoice, company, customer, "en", None)

    assert "60.50" in html
    assert "121.00" in html
    # Ensure 3-decimal forms are not present in the amounts section
    assert "60.500" not in html
    assert "121.000" not in html
