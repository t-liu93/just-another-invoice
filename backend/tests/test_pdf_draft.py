"""Unit tests for rendering unnumbered draft invoices (fix: invoice-number-on-issue, step 3).

An invoice now receives its legal number only at the DRAFT → SENT transition; a
DRAFT carries no ``invoice_number``.  When the number is ``None`` the PDF must
show the "Concept" / "草稿" placeholder in BOTH the ``<title>`` and the number
row — and must never fabricate or preview a number (Frozen Decision D4).

These are pure-function tests on ``build_invoice_html`` (D8: no pango/cairo).
The ``concept.pdf`` filename fallback in ``render_invoice_pdf`` is a one-liner
covered by inspection; the rendering behaviour is the load-bearing part.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from jai.services.pdf import PDF_LABELS, build_invoice_html

# ---------------------------------------------------------------------------
# Helpers – fake ORM-like objects (mirrors test_pdf.py construction style)
# ---------------------------------------------------------------------------


def _make_line() -> Any:
    return SimpleNamespace(
        name="Widget",
        description=None,
        quantity=Decimal("1.000"),
        unit_name="pcs",
        unit_price=Decimal("100.000"),
        subtotal_excl_vat=Decimal("100.000"),
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
    )


def _make_tax() -> Any:
    return SimpleNamespace(
        vat_rate_label="21%",
        vat_rate_percent=Decimal("21.000"),
        taxable_amount=Decimal("100.000"),
        tax_amount=Decimal("21.000"),
    )


def _make_invoice(invoice_number: str | None) -> Any:
    """Build a fake invoice; ``invoice_number=None`` models an unnumbered draft."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_number=invoice_number,
        invoice_date="2026-06-23",
        due_date=None,
        reference_number=None,
        currency="EUR",
        subtotal_excl_vat=Decimal("100.000"),
        document_discount_amount=Decimal("0.000"),
        vat_total=Decimal("21.000"),
        total_incl_vat=Decimal("121.000"),
        due_amount=Decimal("121.000"),
        lines=[_make_line()],
        taxes=[_make_tax()],
        notes=None,
        bank_text=None,
        payment_terms_text=None,
        terms_text=None,
        warranty_text=None,
    )


def _make_company() -> Any:
    return SimpleNamespace(
        name="Test BV",
        vat_id="NL123456789B01",
        coc_number="12345678",
        email="info@test.nl",
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
        contact_name=None,
        company_name=None,
        vat_id=None,
        addresses=[billing],
    )


# ---------------------------------------------------------------------------
# Draft (unnumbered) rendering – D4: show the Concept placeholder, never a number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "locale,expected_label",
    [
        ("en", "Concept"),
        ("zh", "草稿"),
    ],
)
def test_unnumbered_draft_shows_concept_label(locale: str, expected_label: str) -> None:
    """An unnumbered draft (invoice_number=None) shows the Concept/草稿 label.

    Asserts the label appears in BOTH render spots — the <title> and the
    document number row — and that no fabricated number leaks (no literal
    "None", and the company-number style like "F2026-..." never appears).
    """
    invoice = _make_invoice(invoice_number=None)
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, locale, None)

    # 1) The placeholder must appear at least twice (title + number row).
    assert html.count(expected_label) >= 2, (
        f"Expected {expected_label!r} in both the <title> and the number row "
        f"(locale={locale})"
    )

    # 2) The <title> uses the placeholder, not an empty / fabricated value.
    title_label = PDF_LABELS[locale]["invoice"]
    assert f"<title>{title_label} {expected_label}</title>" in html

    # 3) The number row value cell shows the placeholder.
    assert f'<td class="value">{expected_label}</td>' in html

    # 4) No fabricated number leaked: the literal "None" must not appear,
    #    and no real invoice-number style (e.g. "F2026-001") is rendered.
    assert "None" not in html
    assert "F2026-" not in html


def test_unnumbered_draft_keeps_doc_type_label_invoice() -> None:
    """The document type header still reads INVOICE on a draft (only the number defers)."""
    invoice = _make_invoice(invoice_number=None)
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    assert '<div class="doc-type-label">INVOICE</div>' in html


# ---------------------------------------------------------------------------
# Numbered (issued) invoice – regression: real number must NOT be replaced
# ---------------------------------------------------------------------------


def test_issued_invoice_shows_real_number() -> None:
    """A numbered (issued) invoice renders its real number, not the Concept label."""
    invoice = _make_invoice(invoice_number="F2026-001")
    company = _make_company()
    customer = _make_customer()

    html = build_invoice_html(invoice, company, customer, "en", None)

    # Real number is present in both spots.
    assert "<title>Invoice F2026-001</title>" in html
    assert '<td class="value">F2026-001</td>' in html
    # The Concept fallback must NOT appear when a number exists.
    assert "Concept" not in html


# ---------------------------------------------------------------------------
# Label table completeness for the new key
# ---------------------------------------------------------------------------


def test_draft_label_present_in_en_and_zh() -> None:
    """The ``draft`` placeholder key exists for both real locales (en, zh)."""
    assert PDF_LABELS["en"]["draft"] == "Concept"
    assert PDF_LABELS["zh"]["draft"] == "草稿"
