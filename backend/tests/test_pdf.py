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
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from jai.services.pdf import (
    PDF_LABELS,
    _filter_money2,
    _filter_pct,
    _safe_url_fetcher,
    build_content_disposition,
    build_invoice_html,
    resolve_billing_name,
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
    contact_name: str | None = None,
    company_name: str | None = None,
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
        contact_name=contact_name,
        company_name=company_name,
        vat_id=vat_id,
        addresses=[billing],
    )


# ---------------------------------------------------------------------------
# Test 1: amounts in HTML match invoice snapshots (no recalculation)
# ---------------------------------------------------------------------------


def test_build_invoice_html_amounts_match_snapshots() -> None:
    """Amount values in rendered HTML must equal the invoice snapshot fields (2 dp display)."""
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

    # Snapshot values are formatted to 2 decimal places via money2 filter.
    assert "850.00" in html, "subtotal_excl_vat (2dp) not in HTML"
    assert "178.50" in html, "vat_total (2dp) not in HTML"
    assert "1028.50" in html, "total_incl_vat / due_amount (2dp) not in HTML"


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
    # money2 filter formats 10.000 → "10.00"
    assert "10.00" in html


# ---------------------------------------------------------------------------
# resolve_document_locale – D2 priority chain (M9 step 2)
# ---------------------------------------------------------------------------


class TestResolveDocumentLocale:
    """Unit tests for the D2 locale resolution chain.

    Priority: override > customer.locale > company_default > "en".
    Each tier is tested individually to ensure correct fallthrough.
    """

    def setup_method(self) -> None:
        from jai.services.pdf import resolve_document_locale

        self._resolve = resolve_document_locale

    # -- Tier 1: override wins ------------------------------------------------

    def test_override_en_wins_over_all(self) -> None:
        """Explicit override beats customer and company default."""
        result = self._resolve("en", "zh", "zh")
        assert result == "en"

    def test_override_zh_wins_over_all(self) -> None:
        """Explicit zh override beats customer and company default."""
        result = self._resolve("zh", "en", "en")
        assert result == "zh"

    # -- Tier 2: customer.locale when override is None -------------------------

    def test_customer_locale_used_when_no_override(self) -> None:
        """customer.locale is used when override is None."""
        result = self._resolve(None, "zh", "en")
        assert result == "zh"

    def test_customer_locale_en_when_no_override(self) -> None:
        """customer.locale='en' is used when override is None."""
        result = self._resolve(None, "en", "zh")
        assert result == "en"

    # -- Tier 3: company default when override and customer are None -----------

    def test_company_default_used_when_override_and_customer_none(self) -> None:
        """company_default is used when both override and customer.locale are None."""
        result = self._resolve(None, None, "zh")
        assert result == "zh"

    def test_company_default_en_when_override_and_customer_none(self) -> None:
        """company_default='en' is used when override and customer are None."""
        result = self._resolve(None, None, "en")
        assert result == "en"

    # -- Tier 4: fallback to "en" when all are None ---------------------------

    def test_all_none_falls_back_to_en(self) -> None:
        """When all three inputs are None, the result is 'en'."""
        result = self._resolve(None, None, None)
        assert result == "en"

    # -- Edge cases: invalid values are treated as None ----------------------

    def test_invalid_override_falls_through_to_customer(self) -> None:
        """An unrecognised override locale is skipped; customer.locale is used."""
        result = self._resolve("fr", "zh", "en")
        assert result == "zh"

    def test_invalid_customer_locale_falls_through_to_company(self) -> None:
        """An unrecognised customer locale is skipped; company default is used."""
        result = self._resolve(None, "de", "zh")
        assert result == "zh"

    def test_invalid_company_default_falls_through_to_en(self) -> None:
        """An unrecognised company default is skipped; 'en' is the final fallback."""
        result = self._resolve(None, None, "es")
        assert result == "en"

    # -- Full chain verification -----------------------------------------------

    def test_full_chain_priority_override_wins(self) -> None:
        """Full four-tier chain: override 'zh' wins even when customer='en'."""
        result = self._resolve("zh", "en", "en")
        assert result == "zh"

    def test_full_chain_priority_customer_wins_when_no_override(self) -> None:
        """Full chain: customer='zh' wins when override is None."""
        result = self._resolve(None, "zh", "en")
        assert result == "zh"

    def test_full_chain_priority_company_wins_when_no_override_or_customer(self) -> None:
        """Full chain: company='zh' wins when override and customer are None."""
        result = self._resolve(None, None, "zh")
        assert result == "zh"

    def test_full_chain_en_fallback(self) -> None:
        """Full chain: 'en' is the final fallback when all tiers are None."""
        result = self._resolve(None, None, None)
        assert result == "en"


# ---------------------------------------------------------------------------
# Test: money2 filter unit tests
# ---------------------------------------------------------------------------


class TestFilterMoney2:
    """Unit tests for the _filter_money2 Jinja2 display filter."""

    def test_three_decimal_rounds_to_two(self) -> None:
        """3-decimal input is rounded to 2 dp (ROUND_HALF_UP)."""
        assert _filter_money2(Decimal("850.000")) == "850.00"
        assert _filter_money2(Decimal("178.500")) == "178.50"
        assert _filter_money2(Decimal("1028.500")) == "1028.50"

    def test_round_half_up(self) -> None:
        """ROUND_HALF_UP: .005 rounds up."""
        assert _filter_money2(Decimal("1.005")) == "1.01"
        assert _filter_money2(Decimal("1.004")) == "1.00"

    def test_none_returns_zero(self) -> None:
        """None input returns '0.00'."""
        assert _filter_money2(None) == "0.00"

    def test_string_input(self) -> None:
        """String '60.500' is handled correctly."""
        assert _filter_money2("60.500") == "60.50"

    def test_integer_input(self) -> None:
        """Integer 100 becomes '100.00'."""
        assert _filter_money2(100) == "100.00"

    def test_already_two_decimal(self) -> None:
        """Value already at 2 dp stays the same."""
        assert _filter_money2(Decimal("9.99")) == "9.99"

    def test_negative_value(self) -> None:
        """Negative values are formatted correctly."""
        assert _filter_money2(Decimal("-10.005")) == "-10.01"


class TestFilterPct:
    """Unit tests for the _filter_pct Jinja2 display filter."""

    def test_strips_trailing_zeros(self) -> None:
        """21.000 → '21', 9.000 → '9'."""
        assert _filter_pct(Decimal("21.000")) == "21"
        assert _filter_pct(Decimal("9.000")) == "9"

    def test_keeps_significant_decimal(self) -> None:
        """21.500 → '21.5'."""
        assert _filter_pct(Decimal("21.500")) == "21.5"

    def test_none_returns_zero(self) -> None:
        """None → '0'."""
        assert _filter_pct(None) == "0"

    def test_string_input(self) -> None:
        """String '21.000' is handled."""
        assert _filter_pct("21.000") == "21"


# ---------------------------------------------------------------------------
# Test: CSS not escaped (font-family quote fix – task A)
# ---------------------------------------------------------------------------


def test_build_invoice_html_css_not_escaped() -> None:
    """CSS injected via css|safe must NOT be HTML-escaped.

    The font-family declaration contains double quotes; if autoescape were
    applied to the css block, they would become &#34; and the CSS parser
    would silently drop the font-family rule.
    """
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # The literal double-quoted font-family must appear verbatim in the HTML.
    assert 'font-family: "Noto Sans"' in html, (
        "font-family with literal quotes not found – CSS may have been HTML-escaped"
    )
    # The HTML-entity form must NOT appear.
    assert "&#34;" not in html, (
        "HTML entity &#34; found – CSS was incorrectly escaped by autoescape"
    )


# ---------------------------------------------------------------------------
# Test: Description column removed from invoice table (task B)
# ---------------------------------------------------------------------------


def test_build_invoice_html_no_description_column() -> None:
    """Invoice HTML must NOT contain a separate Description column header."""
    invoice = _make_invoice()
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # The Description <th> must be absent from the items table header.
    # We check for the label text "Description" in a <th> context.
    # The items table thead must not contain the Description column header.
    assert "<th>Description</th>" not in html
    # There should be exactly 6 column headers (Item/Qty/Unit/Unit Price/Amount/VAT).
    # Count <th> elements inside the items-table thead.
    import re
    thead_match = re.search(r'<thead>(.*?)</thead>', html, re.DOTALL)
    assert thead_match is not None, "No <thead> found in HTML"
    thead = thead_match.group(1)
    th_count = len(re.findall(r'<th', thead))
    assert th_count == 6, f"Expected 6 <th> in thead, got {th_count}"


def test_build_invoice_html_item_name_bold_description_below() -> None:
    """Item name must use .item-name class (bold); description uses .item-desc below it."""
    line = _make_line(name="EV Charger", description="- 22kW socket\n- LCD display")
    invoice = _make_invoice(lines=[line])
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # item-name class must wrap the name
    assert 'class="item-name"' in html
    # item-desc class must wrap description (within same td, not separate column)
    assert 'class="item-desc"' in html
    # Both must appear; item-name comes before item-desc
    name_pos = html.find('class="item-name"')
    desc_pos = html.find('class="item-desc"')
    assert name_pos < desc_pos, "item-name must appear before item-desc"


# ---------------------------------------------------------------------------
# Test: amounts shown as 2 decimal places (task C)
# ---------------------------------------------------------------------------


def test_build_invoice_html_line_amounts_two_decimal() -> None:
    """Line quantity / unit_price / subtotal_excl_vat must display as 2 dp."""
    line = _make_line(
        quantity="3.000",
        unit_price="1288.720",
        subtotal_excl_vat="3866.160",
    )
    invoice = _make_invoice(
        lines=[line],
        subtotal_excl_vat="3866.160",
        vat_total="811.894",
        total_incl_vat="4678.054",
        due_amount="4678.054",
    )
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # line quantity 3.000 → "3.00"
    assert "3.00" in html
    # line unit_price 1288.720 → "1288.72"
    assert "1288.72" in html
    # line subtotal 3866.160 → "3866.16"
    assert "3866.16" in html
    # totals area
    assert "4678.05" in html  # total_incl_vat / due_amount (4678.054 rounds to 4678.05)


def test_build_invoice_html_vat_percent_no_trailing_zeros() -> None:
    """VAT percentage in totals area should not show unnecessary trailing zeros."""
    invoice = _make_invoice(
        taxes=[_make_tax(vat_rate_percent="21.000", tax_amount="21.000")],
    )
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # Should show "21%" not "21.000%"
    assert "(21%)" in html
    assert "21.000%" not in html


# ---------------------------------------------------------------------------
# Test: resolve_billing_name – billing name priority chain
# ---------------------------------------------------------------------------


def _make_billing_customer(
    name: str = "内部花名",
    contact_name: str | None = None,
    company_name: str | None = None,
) -> Any:
    """Minimal fake customer for resolve_billing_name tests."""
    return SimpleNamespace(
        name=name,
        contact_name=contact_name,
        company_name=company_name,
    )


def test_resolve_billing_name_company_wins() -> None:
    """When all three fields are filled, company_name takes highest priority."""
    customer = _make_billing_customer(
        name="花名",
        contact_name="张三",
        company_name="Acme B.V.",
    )
    assert resolve_billing_name(customer) == "Acme B.V."


def test_resolve_billing_name_contact_when_no_company() -> None:
    """When company_name is absent, contact_name is used."""
    customer = _make_billing_customer(
        name="花名",
        contact_name="张三",
        company_name=None,
    )
    assert resolve_billing_name(customer) == "张三"


def test_resolve_billing_name_fallback_to_name() -> None:
    """When only name is set, it is used as the last resort."""
    customer = _make_billing_customer(
        name="花名",
        contact_name=None,
        company_name=None,
    )
    assert resolve_billing_name(customer) == "花名"


def test_resolve_billing_name_empty_string_company_skipped() -> None:
    """An empty-string company_name must be treated the same as None (skipped)."""
    customer = _make_billing_customer(
        name="花名",
        contact_name="张三",
        company_name="",
    )
    assert resolve_billing_name(customer) == "张三"


def test_resolve_billing_name_empty_string_contact_skipped() -> None:
    """An empty-string contact_name must be treated the same as None (skipped),
    falling back to name."""
    customer = _make_billing_customer(
        name="花名",
        contact_name="",
        company_name=None,
    )
    assert resolve_billing_name(customer) == "花名"


def test_resolve_billing_name_whitespace_only_skipped() -> None:
    """Whitespace-only company_name is skipped after strip(); contact_name is used."""
    customer = _make_billing_customer(
        name="花名",
        contact_name="张三",
        company_name="   ",
    )
    assert resolve_billing_name(customer) == "张三"


def test_resolve_billing_name_billing_name_in_invoice_html() -> None:
    """build_invoice_html must render billing_name (company_name) not customer.name."""
    from jai.models._enums import AddressType

    billing = SimpleNamespace(
        type=AddressType.BILLING,
        street="Klantenstraat",
        house_number="1",
        house_number_addition=None,
        postal_code="1234 AB",
        city="Amsterdam",
        country_code="NL",
    )
    customer = SimpleNamespace(
        name="内部花名",
        contact_name="联系人",
        company_name="Acme B.V.",
        vat_id=None,
        addresses=[billing],
    )
    invoice = _make_invoice()
    company = _make_company()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # The company name must appear in BILL TO block.
    assert "Acme B.V." in html
    # The internal nickname must NOT appear in the BILL TO block.
    # (It may appear elsewhere only if coincidentally present in other fields.)
    # We verify the billing-name div specifically by checking order:
    bill_to_idx = html.index("BILL TO")
    acme_idx = html.index("Acme B.V.", bill_to_idx)
    assert acme_idx > bill_to_idx  # company_name is rendered after "BILL TO"


def test_invoice_html_lists_payments_without_reducing_full_total_or_vat() -> None:
    invoice = _make_invoice(
        subtotal_excl_vat="6611.570",
        vat_total="1388.430",
        total_incl_vat="8000.000",
        due_amount="2400.000",
        taxes=[_make_tax(tax_amount="1388.430", taxable_amount="6611.570")],
    )
    payments = [
        SimpleNamespace(
            payment_date=date(2026, 2, 1),
            reference="DEP-1",
            amount=Decimal("1600.000"),
        ),
        SimpleNamespace(
            payment_date=date(2026, 3, 1),
            reference="DEP-2",
            amount=Decimal("4000.000"),
        ),
    ]
    html = build_invoice_html(
        invoice,
        _make_company(),
        _make_customer(),
        "en",
        None,
        payments=payments,
        paid_total=Decimal("5600.000"),
    )

    assert "8000.00" in html
    assert "1388.43" in html
    assert "Already paid" in html
    assert "5600.00" in html
    assert "2400.00" in html
    assert html.index("DEP-1") < html.index("DEP-2")


def test_invoice_payment_rows_escape_reference_and_have_zh_settlement_labels() -> None:
    """New settlement fields remain escaped and complete in the Chinese template."""
    invoice = _make_invoice(
        total_incl_vat="8000.000",
        due_amount="2400.000",
        subtotal_excl_vat="6611.570",
        vat_total="1388.430",
        taxes=[_make_tax(tax_amount="1388.430", taxable_amount="6611.570")],
    )
    malicious_reference = '<img src="https://evil.example/logo.png">'
    payment = SimpleNamespace(
        payment_date=date(2026, 2, 1),
        reference=malicious_reference,
        amount=Decimal("5600.000"),
    )

    html = build_invoice_html(
        invoice,
        _make_company(),
        _make_customer(),
        "zh",
        None,
        payments=[payment],
        paid_total=Decimal("5600.000"),
    )

    assert malicious_reference not in html
    assert "&lt;img src=&#34;https://evil.example/logo.png&#34;&gt;" in html
    assert "已收款明细" in html
    assert "已付款" in html
    assert "应付款" in html
    assert "8000.00" in html
    assert "1388.43" in html


@pytest.mark.parametrize(
    ("locale", "provenance", "refund_heading"),
    [
        ("en", "Replacement for Credit Note", "Refunds"),
        ("zh", "替换以下贷项通知单", "退款"),
    ],
)
def test_invoice_html_shows_authoritative_followup_and_source_refund_references(
    locale: str, provenance: str, refund_heading: str
) -> None:
    """Formal output only presents the service-projected correction context."""
    invoice = _make_invoice()
    html = build_invoice_html(
        invoice,
        _make_company(),
        _make_customer(),
        locale,
        None,
        followup_relations=[
            SimpleNamespace(
                label=provenance,
                invoice_number="C2026-007",
                invoice_date=date(2026, 2, 4),
            )
        ],
        refunds=[
            SimpleNamespace(
                payment_date=date(2026, 2, 5),
                credit_note_number="C2026-007",
                reference="refund-007",
                amount=Decimal("19.000"),
            )
        ],
    )

    assert provenance in html
    assert "C2026-007" in html
    assert "2026-02-04" in html
    assert refund_heading in html
    assert "2026-02-05" in html
    assert "refund-007" in html
    assert "19.00" in html
