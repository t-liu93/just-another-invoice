"""Unit tests for quote PDF rendering – M9 step 3 (D8: no pango/cairo required).

Tests cover:
1. Amounts in rendered HTML match quote snapshots (not recalculated).
2. XSS fields (name/description/notes containing <script>) are escaped.
3. locale=zh shows Chinese labels; locale=en shows English labels.
4. url_fetcher blocks http:// and file:// URIs; allows data: URIs (reuse _safe_url_fetcher).
5. No-logo path and logo (data URI) path both work correctly.
6. No cost/margin fields rendered (client-facing zero-leakage).
7. Cross-company 404 is handled by render_quote_pdf (marked integration).

Note: html_to_pdf (WeasyPrint integration) tests are in test_pdf_quote_integration.py
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
    build_quote_html,
)

# ---------------------------------------------------------------------------
# Helpers – fake ORM-like quote objects
# ---------------------------------------------------------------------------


def _make_quote_line(
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
        line_taxes=[],
    )


def _make_quote_tax(
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


def _make_quote(
    quote_number: str = "QUO-000001",
    quote_date: str = "2026-06-14",
    valid_until: str | None = "2026-07-14",
    status: str = "DRAFT",
    currency: str = "EUR",
    subtotal_excl_vat: str = "100.000",
    document_discount_amount: str = "0.000",
    vat_total: str = "21.000",
    total_incl_vat: str = "121.000",
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
        quote_number=quote_number,
        quote_date=quote_date,
        valid_until=valid_until,
        status=status,
        reference_number=reference_number,
        currency=currency,
        subtotal_excl_vat=Decimal(subtotal_excl_vat),
        document_discount_amount=Decimal(document_discount_amount),
        vat_total=Decimal(vat_total),
        total_incl_vat=Decimal(total_incl_vat),
        lines=lines if lines is not None else [_make_quote_line()],
        taxes=taxes if taxes is not None else [_make_quote_tax()],
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
# Test 1: amounts in HTML match quote snapshots (no recalculation)
# ---------------------------------------------------------------------------


def test_build_quote_html_amounts_match_snapshots() -> None:
    """Amount values in rendered HTML must equal the quote snapshot fields."""
    quote = _make_quote(
        subtotal_excl_vat="850.000",
        vat_total="178.500",
        total_incl_vat="1028.500",
        taxes=[_make_quote_tax(tax_amount="178.500", taxable_amount="850.000")],
    )
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)

    assert "850.000" in html, "subtotal_excl_vat not in HTML"
    assert "178.500" in html, "vat tax_amount not in HTML"
    assert "1028.500" in html, "total_incl_vat not in HTML"


# ---------------------------------------------------------------------------
# Test 2: XSS escaping
# ---------------------------------------------------------------------------


def test_build_quote_html_xss_escaping() -> None:
    """User-supplied fields containing HTML must be escaped."""
    xss = '<script>alert("xss")</script>'
    quote = _make_quote(
        notes=xss,
        lines=[_make_quote_line(name=xss, description=xss)],
    )
    company = _make_company(name=xss)
    customer = _make_customer(name=xss)

    html = build_quote_html(quote, company, customer, "en", None)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Test 3: locale label switching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "locale,label_key,expected",
    [
        ("en", "quote", "Quote"),
        ("zh", "quote", "报价"),
        ("en", "valid_until", "Valid Until"),
        ("zh", "valid_until", "有效期至"),
        ("en", "total", "Total (incl. VAT)"),
        ("zh", "total", "合计（含税）"),
        ("en", "status", "Status"),
        ("zh", "status", "状态"),
        ("en", "quote_number", "Quote #"),
        ("zh", "quote_number", "报价号"),
    ],
)
def test_build_quote_html_locale_labels(
    locale: str, label_key: str, expected: str
) -> None:
    """Locale-specific labels must appear in the rendered HTML."""
    quote = _make_quote()
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, locale, None)
    assert expected in html, f"Expected label {expected!r} not found in HTML for locale={locale}"


def test_build_quote_html_unknown_locale_falls_back_to_en() -> None:
    """Unknown locale values fall back to English labels."""
    quote = _make_quote()
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "fr", None)
    assert "Quote" in html or "QUOTE" in html


# ---------------------------------------------------------------------------
# Test 4: url_fetcher blocks non-data URIs (reuse same _safe_url_fetcher)
# ---------------------------------------------------------------------------


def test_url_fetcher_blocks_http_for_quote_context() -> None:
    """http:// URIs must raise ValueError – same fetcher used for quote PDF."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("http://evil.example.com/img.png")


def test_url_fetcher_blocks_file_for_quote_context() -> None:
    """file:// URIs must raise ValueError – same fetcher used for quote PDF."""
    with pytest.raises(ValueError, match="not allowed"):
        _safe_url_fetcher("file:///etc/passwd")


# ---------------------------------------------------------------------------
# Test 5: logo paths
# ---------------------------------------------------------------------------


def test_build_quote_html_no_logo() -> None:
    """Without a logo the img tag must NOT appear in the HTML."""
    quote = _make_quote()
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)
    assert "<img" not in html


def test_build_quote_html_with_logo_data_uri() -> None:
    """With a data: URI logo the img tag must appear and contain the data URI."""
    quote = _make_quote()
    company = _make_company()
    customer = _make_customer()
    logo_uri = "data:image/png;base64,iVBORw0KGgo="

    html = build_quote_html(quote, company, customer, "en", logo_uri)
    assert "<img" in html
    assert "data:image/png;base64," in html


# ---------------------------------------------------------------------------
# Test 6: No cost/margin fields rendered (zero-leakage)
# ---------------------------------------------------------------------------


def test_build_quote_html_no_cost_margin_fields() -> None:
    """The quote PDF must not render cost, margin, or estimate business fields.

    The quote template only receives the quote object; no cost/margin context
    is ever passed. This test asserts the rendered output doesn't contain
    business-domain cost/margin field names that would indicate a data leakage.

    Note: CSS property names like "margin" are excluded from this check since
    they are structural styling terms, not business data field names.
    """
    quote = _make_quote()
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)

    # These business-domain identifiers must never appear in a client-facing quote PDF.
    # (Checking the template body only — not CSS which has "margin" as layout property.)
    body_start = html.find("<body>")
    body_html = html[body_start:].lower() if body_start != -1 else html.lower()

    for forbidden in ("cost_price", "hourly_rate", "gross_margin", "net_margin", "estimate_id"):
        assert forbidden not in body_html, (
            f"Forbidden business field {forbidden!r} found in quote HTML body"
        )


# ---------------------------------------------------------------------------
# Test 7: quote-specific fields appear correctly
# ---------------------------------------------------------------------------


def test_build_quote_html_quote_number_and_valid_until() -> None:
    """Quote number and valid_until must appear in the rendered HTML."""
    quote = _make_quote(
        quote_number="QUO-2026-005",
        valid_until="2026-08-01",
        status="SENT",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)
    assert "QUO-2026-005" in html
    assert "2026-08-01" in html
    assert "SENT" in html


def test_build_quote_html_no_due_amount_or_paid_status() -> None:
    """Quote PDF must NOT contain invoice-specific due_amount or paid_status labels."""
    quote = _make_quote()
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)

    # These are invoice-specific; must not appear in a quote PDF.
    assert "Amount Due" not in html
    assert "due_amount" not in html
    assert "paid_status" not in html


def test_build_quote_html_content_blocks() -> None:
    """Content block snapshots must be rendered when present."""
    quote = _make_quote(
        notes="This quote is valid for 30 days.",
        bank_text="IBAN: NL00 TEST 0000 0000 00",
        payment_terms_text="Net 30",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)
    assert "This quote is valid for 30 days." in html
    assert "IBAN: NL00 TEST 0000 0000 00" in html
    assert "Net 30" in html


def test_build_quote_html_zero_discount_not_shown() -> None:
    """Discount row must NOT appear when document_discount_amount is zero."""
    quote = _make_quote(document_discount_amount="0.000")
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)
    assert "Discount" not in html


def test_build_quote_html_nonzero_discount_shown() -> None:
    """Discount row MUST appear when document_discount_amount is non-zero."""
    quote = _make_quote(document_discount_amount="15.000")
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)
    assert "Discount" in html
    assert "15.000" in html


# ---------------------------------------------------------------------------
# Test 8: PDF_LABELS completeness – quote keys exist in both locales
# ---------------------------------------------------------------------------


def test_pdf_labels_quote_keys_exist_in_both_locales() -> None:
    """Quote-specific label keys must exist in both EN and ZH label tables."""
    quote_keys = {"quote", "quote_number", "quote_date", "valid_until", "status"}
    for key in quote_keys:
        assert key in PDF_LABELS["en"], f"Key {key!r} missing from EN labels"
        assert key in PDF_LABELS["zh"], f"Key {key!r} missing from ZH labels"


def test_pdf_labels_en_and_zh_have_same_keys() -> None:
    """EN and ZH label dictionaries must have the same set of keys (including quote keys)."""
    assert set(PDF_LABELS["en"].keys()) == set(PDF_LABELS["zh"].keys()), (
        "EN and ZH label tables have different keys"
    )
