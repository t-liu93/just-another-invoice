"""Tests for the company legal_name trade-name disclosure in the page footer.

When a company has a ``legal_name``, the per-page running footer leads with the
sentence ``{trade} is a trade name of {legal}`` (locale-aware) instead of the
bare trade name, followed by the existing ``· VAT · KvK · email`` segments.

Covers:
1. ``_legal_disclosure`` helper – returns None when unset, formats per locale.
2. Footer rendering – invoice / quote / receipt × has/lacks legal_name × en/zh;
   the sentence must appear inside the ``doc-footer`` block.
3. Escaping regression (red-line 7) – name / legal_name with <script> / & are
   autoescaped (no ``| safe``).
4. Blank legal_name normalisation (schema validator → None).
5. Company schema / service round-trip for legal_name.

No running DB required – all tests use mocks or SimpleNamespace fakes.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jai.schemas.company import CompanyRead, CompanyWrite
from jai.services.company import company_to_read
from jai.services.pdf import (
    PDF_LABELS,
    _legal_disclosure,
    build_invoice_html,
    build_payment_receipt_html,
    build_quote_html,
)

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


def _make_company(
    name: str = "Acme Handel BV",
    legal_name: str | None = None,
    vat_id: str | None = "NL123456789B01",
    coc_number: str | None = "12345678",
    email: str | None = "info@acme.nl",
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
        legal_name=legal_name,
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
    name: str = "Client Corp",
    contact_name: str | None = None,
    company_name: str | None = None,
    vat_id: str | None = None,
) -> Any:
    from jai.models._enums import AddressType

    billing = SimpleNamespace(
        type=AddressType.BILLING,
        street="Klantenstraat",
        house_number="42",
        house_number_addition=None,
        postal_code="5678 CD",
        city="Rotterdam",
        country_code="NL",
    )
    return SimpleNamespace(
        name=name,
        contact_name=contact_name,
        company_name=company_name,
        vat_id=vat_id,
        addresses=[billing],
    )


def _make_invoice(
    invoice_number: str = "F2026-001",
    invoice_date: str = "2026-06-29",
    due_date: str | None = "2026-07-29",
    currency: str = "EUR",
    subtotal_excl_vat: str = "100.000",
    document_discount_amount: str = "0.000",
    vat_total: str = "21.000",
    total_incl_vat: str = "121.000",
    due_amount: str = "121.000",
    notes: str | None = None,
) -> Any:
    line = SimpleNamespace(
        name="Widget",
        description=None,
        quantity=Decimal("1.000"),
        unit_name="pcs",
        unit_price=Decimal("100.000"),
        subtotal_excl_vat=Decimal("100.000"),
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
    )
    tax = SimpleNamespace(
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        taxable_amount=Decimal("100.000"),
        tax_amount=Decimal("21.000"),
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        reference_number=None,
        currency=currency,
        subtotal_excl_vat=Decimal(subtotal_excl_vat),
        document_discount_amount=Decimal(document_discount_amount),
        vat_total=Decimal(vat_total),
        total_incl_vat=Decimal(total_incl_vat),
        due_amount=Decimal(due_amount),
        lines=[line],
        taxes=[tax],
        notes=notes,
        bank_text=None,
        payment_terms_text=None,
        terms_text=None,
        warranty_text=None,
    )


def _make_quote() -> Any:
    line = SimpleNamespace(
        name="Widget",
        description=None,
        quantity=Decimal("1.000"),
        unit_name="pcs",
        unit_price=Decimal("100.000"),
        subtotal_excl_vat=Decimal("100.000"),
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        line_taxes=[],
    )
    tax = SimpleNamespace(
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        taxable_amount=Decimal("100.000"),
        tax_amount=Decimal("21.000"),
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        quote_number="Q2026-001",
        quote_date="2026-06-29",
        valid_until=None,
        reference_number=None,
        status="OPEN",
        currency="EUR",
        subtotal_excl_vat=Decimal("100.000"),
        document_discount_amount=Decimal("0.000"),
        vat_total=Decimal("21.000"),
        total_incl_vat=Decimal("121.000"),
        lines=[line],
        taxes=[tax],
        notes=None,
        bank_text=None,
        payment_terms_text=None,
        terms_text=None,
        warranty_text=None,
    )


def _make_payment() -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        payment_date="2026-06-29",
        amount=Decimal("60.500"),
        base_amount=Decimal("60.500"),
        currency="EUR",
        payment_method_id=uuid.uuid4(),
        payment_method_name="Bank Transfer",
        reference=None,
        note=None,
    )


def _make_receipt_invoice() -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number="F2026-001",
        total_incl_vat=Decimal("121.000"),
        due_amount=Decimal("60.500"),
        paid_status="PARTIALLY_PAID",
        customer_id=uuid.uuid4(),
    )


def _footer(html: str) -> str:
    """Return the inner text of the ``doc-footer`` running element."""
    m = re.search(r'<div class="doc-footer">(.*?)</div>', html, re.S)
    assert m is not None, "doc-footer block not found"
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. _legal_disclosure helper unit tests
# ---------------------------------------------------------------------------


class TestLegalDisclosureHelper:
    """Unit tests for the _legal_disclosure() pure function."""

    def test_returns_none_when_legal_name_absent(self) -> None:
        company = _make_company(name="Acme BV", legal_name=None)
        assert _legal_disclosure(company, PDF_LABELS["en"]) is None

    def test_returns_none_when_legal_name_empty_string(self) -> None:
        company = _make_company(name="Acme BV", legal_name="")
        assert _legal_disclosure(company, PDF_LABELS["en"]) is None

    def test_en_format(self) -> None:
        company = _make_company(name="Acme Handel", legal_name="Acme B.V.")
        result = _legal_disclosure(company, PDF_LABELS["en"])
        assert result == "Acme Handel is a trade name of Acme B.V."

    def test_zh_format(self) -> None:
        company = _make_company(name="安客商贸", legal_name="安客有限公司")
        result = _legal_disclosure(company, PDF_LABELS["zh"])
        assert result == "安客商贸 是 安客有限公司 的商号名称"

    def test_company_without_legal_name_attribute(self) -> None:
        company = SimpleNamespace(name="Acme")  # no legal_name attribute
        assert _legal_disclosure(company, PDF_LABELS["en"]) is None


# ---------------------------------------------------------------------------
# 2. Footer rendering – disclosure sentence leads the footer
# ---------------------------------------------------------------------------

_EN_SENTENCE = "Acme Handel is a trade name of Acme Enterprises B.V."
_ZH_SENTENCE = "安客商贸 是 安客有限公司 的商号名称"


class TestInvoiceFooterDisclosure:
    def test_absent_when_no_legal_name_en(self) -> None:
        company = _make_company(name="Acme BV", legal_name=None)
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "en", None)
        assert "is a trade name of" not in html

    def test_absent_when_no_legal_name_zh(self) -> None:
        company = _make_company(name="安客商贸", legal_name=None)
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "zh", None)
        assert "的商号名称" not in html

    def test_present_in_footer_en(self) -> None:
        company = _make_company(name="Acme Handel", legal_name="Acme Enterprises B.V.")
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "en", None)
        assert _EN_SENTENCE in _footer(html)

    def test_present_in_footer_zh(self) -> None:
        company = _make_company(name="安客商贸", legal_name="安客有限公司")
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "zh", None)
        assert _ZH_SENTENCE in _footer(html)


class TestQuoteFooterDisclosure:
    def test_absent_when_no_legal_name(self) -> None:
        company = _make_company(name="Acme BV", legal_name=None)
        html = build_quote_html(_make_quote(), company, _make_customer(), "en", None)
        assert "is a trade name of" not in html

    def test_present_in_footer_en(self) -> None:
        company = _make_company(name="Acme Handel", legal_name="Acme Enterprises B.V.")
        html = build_quote_html(_make_quote(), company, _make_customer(), "en", None)
        assert _EN_SENTENCE in _footer(html)

    def test_present_in_footer_zh(self) -> None:
        company = _make_company(name="安客商贸", legal_name="安客有限公司")
        html = build_quote_html(_make_quote(), company, _make_customer(), "zh", None)
        assert _ZH_SENTENCE in _footer(html)


class TestReceiptFooterDisclosure:
    def test_absent_when_no_legal_name(self) -> None:
        company = _make_company(name="Acme BV", legal_name=None)
        html = build_payment_receipt_html(
            _make_payment(), _make_receipt_invoice(), company, _make_customer(), "en", None
        )
        assert "is a trade name of" not in html

    def test_present_in_footer_en(self) -> None:
        company = _make_company(name="Acme Handel", legal_name="Acme Enterprises B.V.")
        html = build_payment_receipt_html(
            _make_payment(), _make_receipt_invoice(), company, _make_customer(), "en", None
        )
        assert _EN_SENTENCE in _footer(html)

    def test_present_in_footer_zh(self) -> None:
        company = _make_company(name="安客商贸", legal_name="安客有限公司")
        html = build_payment_receipt_html(
            _make_payment(), _make_receipt_invoice(), company, _make_customer(), "zh", None
        )
        assert _ZH_SENTENCE in _footer(html)


# ---------------------------------------------------------------------------
# 3. Escaping regression (red-line 7) – XSS in name / legal_name
# ---------------------------------------------------------------------------

_TEMPLATES = ("invoice.html", "quote.html", "receipt.html")


class TestFooterEscaping:
    """HTML-special chars in name/legal_name must be escaped by autoescape."""

    def test_disclosure_not_marked_safe_in_templates(self) -> None:
        """No template may render legal_disclosure with the | safe filter."""
        base = Path(__file__).resolve().parent.parent / "src/jai/templates/pdf"
        for tpl in _TEMPLATES:
            text = (base / tpl).read_text(encoding="utf-8")
            assert "legal_disclosure | safe" not in text
            assert "legal_disclosure|safe" not in text

    def test_invoice_xss_in_legal_name_escaped(self) -> None:
        xss = '<script>alert("xss")</script>'
        company = _make_company(name="Acme BV", legal_name=xss)
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "en", None)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_invoice_xss_in_name_escaped(self) -> None:
        xss = '<script>alert("xss")</script>'
        company = _make_company(name=xss, legal_name="Acme B.V.")
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "en", None)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_invoice_ampersand_in_legal_name_escaped(self) -> None:
        company = _make_company(name="Acme", legal_name="A & B Holdings B.V.")
        html = build_invoice_html(_make_invoice(), company, _make_customer(), "en", None)
        assert "&amp;" in html

    def test_quote_xss_in_legal_name_escaped(self) -> None:
        xss = '<script>alert("xss")</script>'
        company = _make_company(name="Acme BV", legal_name=xss)
        html = build_quote_html(_make_quote(), company, _make_customer(), "en", None)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_receipt_xss_in_legal_name_escaped(self) -> None:
        xss = '<script>alert("xss")</script>'
        company = _make_company(name="Acme BV", legal_name=xss)
        html = build_payment_receipt_html(
            _make_payment(), _make_receipt_invoice(), company, _make_customer(), "en", None
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# 4. Schema validator – blank legal_name normalised to None
# ---------------------------------------------------------------------------


class TestCompanyWriteLegalNameNormalisation:
    """Tests for the legal_name field_validator on CompanyWrite."""

    def test_none_stays_none(self) -> None:
        cw = CompanyWrite(name="Acme", base_currency="EUR", legal_name=None)
        assert cw.legal_name is None

    def test_blank_string_normalised_to_none(self) -> None:
        cw = CompanyWrite(name="Acme", base_currency="EUR", legal_name="   ")
        assert cw.legal_name is None

    def test_empty_string_normalised_to_none(self) -> None:
        cw = CompanyWrite(name="Acme", base_currency="EUR", legal_name="")
        assert cw.legal_name is None

    def test_valid_legal_name_stripped_of_whitespace(self) -> None:
        cw = CompanyWrite(name="Acme", base_currency="EUR", legal_name="  Acme B.V.  ")
        assert cw.legal_name == "Acme B.V."

    def test_valid_legal_name_preserved(self) -> None:
        cw = CompanyWrite(
            name="Acme", base_currency="EUR", legal_name="Acme Enterprises B.V."
        )
        assert cw.legal_name == "Acme Enterprises B.V."


# ---------------------------------------------------------------------------
# 5. company_to_read service – legal_name round-trip
# ---------------------------------------------------------------------------


def _make_company_orm(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "Test Company",
        "legal_name": None,
        "vat_id": None,
        "coc_number": None,
        "email": None,
        "phone": None,
        "website": None,
        "street": None,
        "house_number": None,
        "house_number_addition": None,
        "postal_code": None,
        "city": None,
        "province": None,
        "country_code": None,
        "base_currency": "EUR",
        "logo_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCompanyToReadLegalName:
    """Tests for company_to_read mapping of legal_name field."""

    def test_legal_name_none_maps_to_none(self) -> None:
        read = company_to_read(_make_company_orm(legal_name=None))
        assert read.legal_name is None

    def test_legal_name_value_maps_correctly(self) -> None:
        read = company_to_read(_make_company_orm(legal_name="Acme B.V."))
        assert read.legal_name == "Acme B.V."

    def test_legal_name_in_schema_type(self) -> None:
        read = company_to_read(_make_company_orm(legal_name="Test Legal Name"))
        assert isinstance(read, CompanyRead)


# ---------------------------------------------------------------------------
# 6. PDF_LABELS completeness – trade_name_disclosure key in both locales
# ---------------------------------------------------------------------------


def test_pdf_labels_trade_name_disclosure_in_both_locales() -> None:
    assert "trade_name_disclosure" in PDF_LABELS["en"]
    assert "trade_name_disclosure" in PDF_LABELS["zh"]


def test_pdf_labels_trade_name_disclosure_en_format() -> None:
    template = PDF_LABELS["en"]["trade_name_disclosure"]
    formatted = template.format(trade="X", legal="Y")
    assert "X" in formatted
    assert "Y" in formatted
    assert "trade name" in template


def test_pdf_labels_trade_name_disclosure_zh_format() -> None:
    template = PDF_LABELS["zh"]["trade_name_disclosure"]
    formatted = template.format(trade="X", legal="Y")
    assert "X" in formatted
    assert "Y" in formatted
    assert "商号名称" in template
