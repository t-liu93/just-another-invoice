"""Unit tests for services/pdf.py – M9 step 1 (D8: no pango/cairo required).

Tests cover:
1. Amounts in rendered HTML match invoice snapshots (not recalculated).
2. XSS fields (name/description/notes containing <script>) are escaped.
3. locale=zh shows Chinese labels; locale=en shows English labels.
4. url_fetcher blocks http:// and file:// URIs; allows data: URIs.
5. No-logo path and logo (data URI) path both work correctly.
6. Cross-company 404 is handled by render_invoice_pdf (integration test).

Note: html_to_pdf (WeasyPrint integration) tests are in test_pdf_integration.py
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
    build_content_disposition,
    build_invoice_html,
)

# ---------------------------------------------------------------------------
# Helpers – fake ORM-like objects
# ---------------------------------------------------------------------------


def _make_line(
    name: str = "Widget",
    description: str | None = "A fine widget",
    quantity: str = "2.000",
    unit_name: str | None = "pcs",
    unit_price: str = "50.000",
    subtotal_excl_vat: str = "100.000",
    vat_rate_label: str | None = "21%",
    vat_rate_percent: str | None = "21.000",
    total_incl_vat: str = "121.000",
) -> Any:
    return SimpleNamespace(
        name=name,
        description=description,
        quantity=Decimal(quantity),
        unit_name=unit_name,
        unit_price=Decimal(unit_price),
        subtotal_excl_vat=Decimal(subtotal_excl_vat),
        subtotal_incl_vat=Decimal(total_incl_vat),
        vat_rate_label=vat_rate_label,
        vat_rate_percent=Decimal(vat_rate_percent) if vat_rate_percent else None,
        total_incl_vat=Decimal(total_incl_vat),
        vat_total=Decimal("21.000"),
        taxable_amount=Decimal("100.000"),
        line_discount_amount=Decimal("0.000"),
        document_discount_share=Decimal("0.000"),
    )


def _make_tax(
    vat_rate_label: str = "21%",
    vat_rate_percent: str = "21.000",
    taxable_amount: str = "100.000",
    tax_amount: str = "21.000",
) -> Any:
    return SimpleNamespace(
        vat_rate_label=vat_rate_label,
        vat_rate_percent=Decimal(vat_rate_percent),
        effective_vat_percent=Decimal(vat_rate_percent),
        taxable_amount=Decimal(taxable_amount),
        tax_amount=Decimal(tax_amount),
    )


def _make_invoice(
    invoice_number: str = "F2026-001",
    invoice_date: str = "2026-06-14",
    due_date: str | None = "2026-07-14",
    currency: str = "EUR",
    subtotal_excl_vat: str = "100.000",
    document_discount_amount: str = "0.000",
    vat_total: str = "21.000",
    total_incl_vat: str = "121.000",
    due_amount: str = "121.000",
    lines: list[Any] | None = None,
    taxes: list[Any] | None = None,
    notes: str | None = None,
    bank_text: str | None = None,
    payment_terms_text: str | None = None,
    terms_text: str | None = None,
    warranty_text: str | None = None,
    reference_number: str | None = None,
) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        reference_number=reference_number,
        currency=currency,
        subtotal_excl_vat=Decimal(subtotal_excl_vat),
        document_discount_amount=Decimal(document_discount_amount),
        vat_total=Decimal(vat_total),
        total_incl_vat=Decimal(total_incl_vat),
        due_amount=Decimal(due_amount),
        lines=lines if lines is not None else [_make_line()],
        taxes=taxes if taxes is not None else [_make_tax()],
        notes=notes,
        bank_text=bank_text,
        payment_terms_text=payment_terms_text,
        terms_text=terms_text,
        warranty_text=warranty_text,
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
# Test 1: amounts in HTML match invoice snapshots (no recalculation)
# ---------------------------------------------------------------------------


def test_build_invoice_html_amounts_match_snapshots() -> None:
    """Amount values in rendered HTML must equal the invoice snapshot fields."""
    invoice = _make_invoice(
        subtotal_excl_vat="850.000",
        vat_total="178.500",
        total_incl_vat="1028.500",
        due_amount="1028.500",
        taxes=[_make_tax(tax_amount="178.500", taxable_amount="850.000")],
    )
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # Snapshot values should appear verbatim in the rendered HTML.
    assert "850.000" in html, "subtotal_excl_vat not in HTML"
    assert "178.500" in html, "vat_total not in HTML"
    assert "1028.500" in html, "total_incl_vat / due_amount not in HTML"


# ---------------------------------------------------------------------------
# Test 2: XSS escaping
# ---------------------------------------------------------------------------


def test_build_invoice_html_xss_escaping() -> None:
    """User-supplied fields containing HTML must be escaped."""
    xss = '<script>alert("xss")</script>'
    invoice = _make_invoice(
        notes=xss,
        lines=[_make_line(name=xss, description=xss)],
    )
    company = _make_company(name=xss)
    customer = _make_customer(name=xss)

    html = build_invoice_html(invoice, company, customer, "en", None)

    # Raw script tags must NOT appear.
    assert "<script>" not in html
    # Escaped versions must appear.
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Test 3: locale label switching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "locale,label_key,expected",
    [
        ("en", "invoice", "Invoice"),
        ("zh", "invoice", "发票"),
        ("en", "due_date", "Due Date"),
        ("zh", "due_date", "到期日期"),
        ("en", "total", "Total (incl. VAT)"),
        ("zh", "total", "合计（含税）"),
    ],
)
def test_build_invoice_html_locale_labels(
    locale: str, label_key: str, expected: str
) -> None:
    """Locale-specific labels must appear in the rendered HTML."""
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, locale, None)
    assert expected in html, f"Expected label {expected!r} not found in HTML for locale={locale}"


def test_build_invoice_html_unknown_locale_falls_back_to_en() -> None:
    """Unknown locale values fall back to English labels."""
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "de", None)
    # English label should appear
    assert "Invoice" in html or "INVOICE" in html


# ---------------------------------------------------------------------------
# Test 4: url_fetcher blocks non-data URIs
# ---------------------------------------------------------------------------


def test_url_fetcher_blocks_http() -> None:
    """http:// URIs must raise ValueError."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("http://evil.example.com/img.png")


def test_url_fetcher_blocks_https() -> None:
    """https:// URIs must raise ValueError."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("https://evil.example.com/img.png")


def test_url_fetcher_blocks_file() -> None:
    """file:// URIs must raise ValueError."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("file:///etc/passwd")


def test_url_fetcher_blocks_ftp() -> None:
    """ftp:// URIs must raise ValueError."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("ftp://internal.server/resource")


# ---------------------------------------------------------------------------
# Test 5: logo paths
# ---------------------------------------------------------------------------


def test_build_invoice_html_no_logo() -> None:
    """Without a logo the img tag must NOT appear in the HTML."""
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)
    assert "<img" not in html


def test_build_invoice_html_with_logo_data_uri() -> None:
    """With a data: URI logo the img tag must appear and contain the data URI."""
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()
    logo_uri = "data:image/png;base64,iVBORw0KGgo="

    html = build_invoice_html(invoice, company, customer, "en", logo_uri)
    assert "<img" in html
    assert "data:image/png;base64," in html


# ---------------------------------------------------------------------------
# Test 6: PDF_LABELS completeness
# ---------------------------------------------------------------------------


def test_pdf_labels_en_and_zh_have_same_keys() -> None:
    """EN and ZH label dictionaries must have the same set of keys."""
    assert set(PDF_LABELS["en"].keys()) == set(PDF_LABELS["zh"].keys()), (
        "EN and ZH label tables have different keys"
    )


# ---------------------------------------------------------------------------
# Test 7: content blocks appear in HTML when present
# ---------------------------------------------------------------------------


def test_build_invoice_html_content_blocks() -> None:
    """Content block snapshots must be rendered when present."""
    invoice = _make_invoice(
        notes="Please pay within 30 days.",
        bank_text="IBAN: NL00 TEST 0000 0000 00",
        payment_terms_text="Net 30",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)
    assert "Please pay within 30 days." in html
    assert "IBAN: NL00 TEST 0000 0000 00" in html
    assert "Net 30" in html


# ---------------------------------------------------------------------------
# Test 8: billing address is rendered
# ---------------------------------------------------------------------------


def test_build_invoice_html_billing_address() -> None:
    """Billing address fields must appear in the HTML."""
    customer = _make_customer(
        name="Big Client BV",
        billing_street="Klantenstraat",
        billing_house_number="42",
        billing_city="Rotterdam",
    )
    invoice = _make_invoice()
    company = _make_company()

    html = build_invoice_html(invoice, company, customer, "en", None)
    assert "Big Client BV" in html
    assert "Klantenstraat" in html
    assert "Rotterdam" in html


# ---------------------------------------------------------------------------
# Test 9: company info is rendered
# ---------------------------------------------------------------------------


def test_build_invoice_html_company_info() -> None:
    """Company name, VAT ID and KvK number must appear in the HTML."""
    company = _make_company(
        name="My Company BV",
        vat_id="NL999888777B01",
        coc_number="87654321",
    )
    invoice = _make_invoice()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)
    assert "My Company BV" in html
    assert "NL999888777B01" in html
    assert "87654321" in html


# ---------------------------------------------------------------------------
# Test 10: build_content_disposition – RFC 6266/5987 compliance
# ---------------------------------------------------------------------------


def test_content_disposition_plain_ascii() -> None:
    """Plain ASCII filename produces a well-formed header, latin-1 encodable."""
    hdr = build_content_disposition("F2026-001.pdf")
    # Must be latin-1 encodable (no UnicodeEncodeError in ASGI layer).
    hdr.encode("latin-1")
    assert 'filename="F2026-001.pdf"' in hdr
    # filename* segment must be present with UTF-8 prefix.
    assert "filename*=UTF-8''" in hdr
    assert "F2026-001.pdf" in hdr


def test_content_disposition_non_ascii_cjk() -> None:
    """Non-ASCII (CJK) filename must not crash; header must be latin-1 encodable."""
    hdr = build_content_disposition("发票2026-001.pdf")
    # latin-1 encodability is the key invariant (Starlette uses .encode("latin-1")).
    hdr.encode("latin-1")
    # ASCII fallback must be non-empty and free of raw CJK.
    assert 'filename="' in hdr
    fallback_start = hdr.index('filename="') + len('filename="')
    fallback_end = hdr.index('"', fallback_start)
    fallback = hdr[fallback_start:fallback_end]
    fallback.encode("latin-1")  # must be latin-1 safe
    # filename* must percent-encode the CJK chars.
    assert "filename*=UTF-8''" in hdr
    # CJK characters should NOT appear raw in the header value.
    assert "发票" not in hdr


def test_content_disposition_double_quote_in_name() -> None:
    """A filename containing ``"`` must not break the quoted-string boundary."""
    hdr = build_content_disposition('bad"name.pdf')
    # Must be latin-1 encodable.
    hdr.encode("latin-1")
    # The raw double-quote must not appear inside the filename="..." token.
    # Extract the fallback between the outer quotes.
    fallback_start = hdr.index('filename="') + len('filename="')
    fallback_end = hdr.index('"', fallback_start)
    fallback = hdr[fallback_start:fallback_end]
    assert '"' not in fallback, "Raw double-quote found inside filename= token"
    # filename* segment must still be present.
    assert "filename*=UTF-8''" in hdr


def test_content_disposition_header_is_attachment() -> None:
    """Header value must always start with ``attachment``."""
    hdr = build_content_disposition("invoice.pdf")
    assert hdr.startswith("attachment;")


def test_build_invoice_html_zero_discount_not_shown() -> None:
    """Discount row must NOT appear when document_discount_amount is zero (Decimal)."""
    invoice = _make_invoice(document_discount_amount="0.000")
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)
    # The discount label should not appear in the rendered HTML.
    assert "Discount" not in html


def test_build_invoice_html_nonzero_discount_shown() -> None:
    """Discount row MUST appear when document_discount_amount is non-zero."""
    invoice = _make_invoice(document_discount_amount="10.000")
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)
    assert "Discount" in html
    assert "10.000" in html
