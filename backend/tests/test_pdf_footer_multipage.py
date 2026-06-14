"""Integration tests: footer appears on every page of multi-page PDFs.

Root cause: WeasyPrint running-element mechanism (`position:running(footer)`)
only propagates the value to pages *after* the element is first encountered in
the document flow.  When `<div class="doc-footer">` sat at the *end* of the
body, pages 2…(N-1) never received the footer value.  Fix: move the footer
element to the **top** of `<body>` (it is already out-of-flow, so the visual
layout is unchanged).

These tests render a PDF with enough lines to span at least 3 pages and then
use pypdfium2 to extract text from each page, asserting that the footer
identifier (KvK number / email) appears on every page.

Run with:
    pytest -m integration -k "footer or multipage"
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pypdfium2
import pytest

# ---------------------------------------------------------------------------
# Helpers shared with test_pdf.py / test_quote_pdf.py (inline copies so this
# file stays self-contained and the test outcome is unambiguous).
# ---------------------------------------------------------------------------

_COC = "87654321"
_EMAIL = "footer-test@example.nl"
_COMPANY_NAME = "Footer Test BV"


def _make_company() -> Any:
    return SimpleNamespace(
        name=_COMPANY_NAME,
        vat_id="NL123456789B01",
        coc_number=_COC,
        email=_EMAIL,
        phone=None,
        website=None,
        street="Teststraat",
        house_number="1",
        house_number_addition=None,
        postal_code="1234 AB",
        city="Amsterdam",
        country_code="NL",
        logo_id=None,
    )


def _make_customer() -> Any:
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
        name="ACME Corp",
        vat_id=None,
        addresses=[billing],
    )


def _make_line(idx: int) -> Any:
    return SimpleNamespace(
        name=f"Service item {idx:03d}",
        description=f"Detailed description for line {idx:03d} – consulting hours",
        quantity=Decimal("2.000"),
        unit_name="hrs",
        unit_price=Decimal("95.000"),
        subtotal_excl_vat=Decimal("190.000"),
        subtotal_incl_vat=Decimal("229.900"),
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        total_incl_vat=Decimal("229.900"),
        vat_total=Decimal("39.900"),
        taxable_amount=Decimal("190.000"),
        line_discount_amount=Decimal("0.000"),
        document_discount_share=Decimal("0.000"),
    )


def _make_tax(n_lines: int) -> Any:
    base = Decimal("190.000") * n_lines
    tax = (base * Decimal("21") / Decimal("100")).quantize(Decimal("0.001"))
    return SimpleNamespace(
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        effective_vat_percent=Decimal("21.000"),
        taxable_amount=base,
        tax_amount=tax,
    )


def _make_invoice(n_lines: int = 40) -> Any:
    lines = [_make_line(i) for i in range(1, n_lines + 1)]
    tax = _make_tax(n_lines)
    base = Decimal("190.000") * n_lines
    vat = tax.tax_amount
    total = base + vat
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number="F2026-FOOTER-001",
        invoice_date="2026-06-14",
        due_date="2026-07-14",
        reference_number=None,
        currency="EUR",
        subtotal_excl_vat=base,
        document_discount_amount=Decimal("0.000"),
        vat_total=vat,
        total_incl_vat=total,
        due_amount=total,
        lines=lines,
        taxes=[tax],
        notes=None,
        bank_text=None,
        payment_terms_text=None,
        terms_text=None,
        warranty_text=None,
    )


def _make_quote_line(idx: int) -> Any:
    return SimpleNamespace(
        name=f"Quote item {idx:03d}",
        description=f"Scope of work line {idx:03d}",
        quantity=Decimal("1.000"),
        unit_name="pcs",
        unit_price=Decimal("100.000"),
        subtotal_excl_vat=Decimal("100.000"),
        subtotal_incl_vat=Decimal("121.000"),
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        total_incl_vat=Decimal("121.000"),
        vat_total=Decimal("21.000"),
        taxable_amount=Decimal("100.000"),
        line_discount_amount=Decimal("0.000"),
        document_discount_share=Decimal("0.000"),
        line_taxes=[],
    )


def _make_quote(n_lines: int = 40) -> Any:
    lines = [_make_quote_line(i) for i in range(1, n_lines + 1)]
    base = Decimal("100.000") * n_lines
    vat = (base * Decimal("21") / Decimal("100")).quantize(Decimal("0.001"))
    total = base + vat
    return SimpleNamespace(
        id=uuid.uuid4(),
        quote_number="QUO-FOOTER-001",
        quote_date="2026-06-14",
        valid_until="2026-07-14",
        status="DRAFT",
        reference_number=None,
        currency="EUR",
        subtotal_excl_vat=base,
        document_discount_amount=Decimal("0.000"),
        vat_total=vat,
        total_incl_vat=total,
        lines=lines,
        taxes=[SimpleNamespace(
            vat_rate_label="21%",
            vat_rate_percent=Decimal("21.000"),
            effective_vat_percent=Decimal("21.000"),
            taxable_amount=base,
            tax_amount=vat,
        )],
        notes=None,
        bank_text=None,
        payment_terms_text=None,
        terms_text=None,
        warranty_text=None,
    )


# ---------------------------------------------------------------------------
# Helpers: extract per-page text using pypdfium2
# ---------------------------------------------------------------------------


def _extract_page_texts(pdf_bytes: bytes) -> list[str]:
    """Return a list of text strings, one entry per PDF page."""
    doc = pypdfium2.PdfDocument(pdf_bytes)
    texts: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
    finally:
        doc.close()
    return texts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_invoice_footer_appears_on_every_page() -> None:
    """A multi-page invoice PDF must show the footer on every single page.

    We build an invoice with 40 lines (guaranteed > 3 pages at A4) and assert
    that the company KvK number and email appear in the extracted text of
    every page — not just the first and last.
    """
    from jai.services.pdf import build_invoice_html, html_to_pdf

    invoice = _make_invoice(n_lines=40)
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)
    pdf_bytes = html_to_pdf(html)

    page_texts = _extract_page_texts(pdf_bytes)
    n_pages = len(page_texts)

    assert n_pages >= 3, (
        f"Expected at least 3 pages for a 40-line invoice, got {n_pages}. "
        "Increase line count or check page size."
    )

    for page_idx, page_text in enumerate(page_texts):
        assert _COC in page_text, (
            f"KvK number {_COC!r} missing from page {page_idx + 1}/{n_pages}. "
            "Footer is not showing on this page."
        )
        assert _EMAIL in page_text, (
            f"Email {_EMAIL!r} missing from page {page_idx + 1}/{n_pages}. "
            "Footer is not showing on this page."
        )


@pytest.mark.integration
def test_quote_footer_appears_on_every_page() -> None:
    """A multi-page quote PDF must show the footer on every single page.

    Same principle as the invoice test: 40 lines → at least 3 pages.
    """
    from jai.services.pdf import build_quote_html, html_to_pdf

    quote = _make_quote(n_lines=40)
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)
    pdf_bytes = html_to_pdf(html)

    page_texts = _extract_page_texts(pdf_bytes)
    n_pages = len(page_texts)

    assert n_pages >= 3, (
        f"Expected at least 3 pages for a 40-line quote, got {n_pages}. "
        "Increase line count or check page size."
    )

    for page_idx, page_text in enumerate(page_texts):
        assert _COC in page_text, (
            f"KvK number {_COC!r} missing from page {page_idx + 1}/{n_pages}. "
            "Footer is not showing on this page."
        )
        assert _EMAIL in page_text, (
            f"Email {_EMAIL!r} missing from page {page_idx + 1}/{n_pages}. "
            "Footer is not showing on this page."
        )


# ---------------------------------------------------------------------------
# Structural unit test: doc-footer precedes main content in HTML
# ---------------------------------------------------------------------------


def test_invoice_html_footer_before_main_content() -> None:
    """doc-footer div must appear in HTML before the items table.

    This is a structural regression guard: if the footer is ever moved back
    to the bottom of the body, this test catches it immediately — no
    WeasyPrint required.
    """
    from jai.services.pdf import build_invoice_html

    invoice = _make_invoice(n_lines=2)
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    footer_pos = html.find('class="doc-footer"')
    items_table_pos = html.find('class="items-table"')

    assert footer_pos != -1, "doc-footer not found in rendered HTML"
    assert items_table_pos != -1, "items-table not found in rendered HTML"
    assert footer_pos < items_table_pos, (
        "doc-footer must appear before items-table in the HTML; "
        f"footer at char {footer_pos}, items-table at char {items_table_pos}"
    )


def test_quote_html_footer_before_main_content() -> None:
    """doc-footer div must appear in quote HTML before the items table."""
    from jai.services.pdf import build_quote_html

    quote = _make_quote(n_lines=2)
    company = _make_company()
    customer = _make_customer()

    html = build_quote_html(quote, company, customer, "en", None)

    footer_pos = html.find('class="doc-footer"')
    items_table_pos = html.find('class="items-table"')

    assert footer_pos != -1, "doc-footer not found in rendered HTML"
    assert items_table_pos != -1, "items-table not found in rendered HTML"
    assert footer_pos < items_table_pos, (
        "doc-footer must appear before items-table in the quote HTML; "
        f"footer at char {footer_pos}, items-table at char {items_table_pos}"
    )
