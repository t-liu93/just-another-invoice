"""Unit tests for the numbering engine (M5 step 2).

Tests ``services.numbering`` pure functions (no DB).

Coverage:
- ``validate_template``: accepted and rejected placeholder forms.
- ``needs_customer_sequence``: detection of CUSTOMER_SEQUENCE placeholder.
- ``render_template``: every placeholder type, padding, dates, edge cases.
"""

from __future__ import annotations

from datetime import date

import pytest

from jai.services.numbering import (
    needs_customer_sequence,
    needs_sequence_placeholder,
    render_template,
    validate_credit_template,
    validate_template,
)

# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------


class TestValidateTemplate:
    def test_series_placeholder(self) -> None:
        validate_template("{{SERIES:INV}}-{{SEQUENCE:6}}")  # no error

    def test_sequence_placeholder(self) -> None:
        validate_template("{{SEQUENCE:4}}")  # no error

    def test_customer_series_placeholder(self) -> None:
        validate_template("{{CUSTOMER_SERIES}}-{{SEQUENCE:4}}")  # no error

    def test_customer_sequence_placeholder(self) -> None:
        validate_template("{{CUSTOMER_SEQUENCE:4}}")  # no error

    def test_date_placeholder_year(self) -> None:
        validate_template("{{DATE:%Y}}-{{SEQUENCE:6}}")  # no error

    def test_date_placeholder_full(self) -> None:
        validate_template("{{DATE:%Y%m%d}}-{{SEQUENCE:6}}")  # no error

    def test_date_placeholder_with_hyphen(self) -> None:
        validate_template("{{DATE:%Y-%m-%d}}")  # no error

    def test_unknown_placeholder_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            validate_template("{{UNKNOWN_THING}}")

    def test_arbitrary_expression_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            validate_template("{{__import__('os').system('rm -rf /')}}")

    def test_date_format_with_disallowed_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            validate_template("{{DATE:../../../etc/passwd}}")

    def test_no_placeholders_valid(self) -> None:
        validate_template("FIXED-NUMBER-001")  # no error

    def test_empty_template_valid(self) -> None:
        validate_template("")  # no error

    def test_mixed_valid_placeholders(self) -> None:
        validate_template(
            "{{SERIES:FACT}}-{{DATE:%Y}}-{{SEQUENCE:6}}"
        )  # no error


class TestValidateCreditTemplate:
    def test_rejects_customer_scoped_placeholders_before_persisting(self) -> None:
        with pytest.raises(ValueError, match="does not support"):
            validate_credit_template("{{SERIES:CR}}-{{CUSTOMER_SEQUENCE:4}}-{{SEQUENCE:4}}")

    def test_keeps_company_scoped_template(self) -> None:
        validate_credit_template("{{SERIES:CR}}-{{DATE:%Y}}-{{SEQUENCE:4}}")


# ---------------------------------------------------------------------------
# needs_customer_sequence
# ---------------------------------------------------------------------------


class TestNeedsCustomerSequence:
    def test_false_for_standard_template(self) -> None:
        assert needs_customer_sequence("{{SERIES:INV}}-{{SEQUENCE:6}}") is False

    def test_true_when_customer_sequence_present(self) -> None:
        assert needs_customer_sequence("{{CUSTOMER_SEQUENCE:4}}-{{SEQUENCE:6}}") is True

    def test_false_when_only_customer_series(self) -> None:
        # CUSTOMER_SERIES does NOT require a customer-scope counter
        assert needs_customer_sequence("{{CUSTOMER_SERIES}}-{{SEQUENCE:4}}") is False

    def test_true_with_mixed_placeholders(self) -> None:
        assert needs_customer_sequence(
            "{{SERIES:INV}}-{{CUSTOMER_SEQUENCE:3}}-{{DATE:%Y}}"
        ) is True


# ---------------------------------------------------------------------------
# needs_sequence_placeholder
# ---------------------------------------------------------------------------


class TestNeedsSequencePlaceholder:
    def test_true_for_standard_template(self) -> None:
        assert needs_sequence_placeholder("{{SERIES:INV}}-{{SEQUENCE:6}}") is True

    def test_true_for_sequence_only(self) -> None:
        assert needs_sequence_placeholder("{{SEQUENCE:4}}") is True

    def test_true_when_mixed_with_customer_sequence(self) -> None:
        assert needs_sequence_placeholder(
            "{{CUSTOMER_SERIES}}-{{CUSTOMER_SEQUENCE:4}}-{{SEQUENCE:6}}"
        ) is True

    def test_false_for_static_string(self) -> None:
        assert needs_sequence_placeholder("FIXED-001") is False

    def test_false_for_series_only(self) -> None:
        assert needs_sequence_placeholder("{{SERIES:INV}}") is False

    def test_false_for_customer_sequence_only(self) -> None:
        # CUSTOMER_SEQUENCE is per-customer; does NOT satisfy company-global uniqueness.
        assert needs_sequence_placeholder("{{CUSTOMER_SEQUENCE:4}}") is False

    def test_false_for_customer_series_and_customer_sequence(self) -> None:
        assert needs_sequence_placeholder(
            "{{CUSTOMER_SERIES}}-{{CUSTOMER_SEQUENCE:4}}"
        ) is False

    def test_false_for_date_only(self) -> None:
        assert needs_sequence_placeholder("{{DATE:%Y%m%d}}") is False


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


_TODAY = date(2026, 6, 10)


class TestRenderTemplate:
    def _render(
        self,
        template: str,
        *,
        sequence: int = 1,
        customer_series: str | None = None,
        customer_sequence: int | None = None,
        invoice_date: date = _TODAY,
    ) -> str:
        return render_template(
            template,
            sequence=sequence,
            customer_series=customer_series,
            customer_sequence=customer_sequence,
            invoice_date=invoice_date,
        )

    def test_series_and_sequence(self) -> None:
        result = self._render("{{SERIES:INV}}-{{SEQUENCE:6}}", sequence=100)
        assert result == "INV-000100"

    def test_sequence_padding(self) -> None:
        assert self._render("{{SEQUENCE:6}}", sequence=1) == "000001"
        assert self._render("{{SEQUENCE:6}}", sequence=999999) == "999999"
        assert self._render("{{SEQUENCE:6}}", sequence=1000000) == "1000000"  # no truncation

    def test_sequence_padding_width_1(self) -> None:
        assert self._render("{{SEQUENCE:1}}", sequence=5) == "5"
        assert self._render("{{SEQUENCE:1}}", sequence=15) == "15"

    def test_customer_series(self) -> None:
        result = self._render(
            "{{CUSTOMER_SERIES}}-{{SEQUENCE:4}}",
            sequence=7,
            customer_series="ABC",
        )
        assert result == "ABC-0007"

    def test_customer_series_empty_when_none(self) -> None:
        result = self._render("{{CUSTOMER_SERIES}}-{{SEQUENCE:4}}", sequence=1)
        assert result == "-0001"

    def test_customer_sequence(self) -> None:
        result = self._render(
            "{{SERIES:INV}}-{{CUSTOMER_SEQUENCE:4}}",
            sequence=10,
            customer_sequence=3,
        )
        assert result == "INV-0003"

    def test_customer_sequence_missing_raises(self) -> None:
        with pytest.raises(ValueError, match="customer sequence"):
            self._render("{{CUSTOMER_SEQUENCE:4}}", customer_sequence=None)

    def test_date_year(self) -> None:
        result = self._render("{{DATE:%Y}}-{{SEQUENCE:4}}", sequence=5)
        assert result == "2026-0005"

    def test_date_full(self) -> None:
        result = self._render("{{DATE:%Y%m%d}}-{{SEQUENCE:3}}", sequence=1)
        assert result == "20260610-001"

    def test_date_hyphenated(self) -> None:
        result = self._render("{{DATE:%Y-%m-%d}}-{{SEQUENCE:3}}", sequence=2)
        assert result == "2026-06-10-002"

    def test_no_placeholders(self) -> None:
        assert self._render("FIXED-001") == "FIXED-001"

    def test_empty_template(self) -> None:
        assert self._render("") == ""

    def test_series_value_preserved_verbatim(self) -> None:
        # Series value can contain special characters
        result = self._render("{{SERIES:FACT/NL}}-{{SEQUENCE:4}}", sequence=1)
        assert result == "FACT/NL-0001"

    def test_unknown_placeholder_raises_at_render(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            self._render("{{BADTOKEN}}")

    def test_combined_template(self) -> None:
        result = self._render(
            "{{SERIES:F}}-{{DATE:%Y}}-{{CUSTOMER_SERIES}}-{{SEQUENCE:4}}",
            sequence=42,
            customer_series="XYZ",
        )
        assert result == "F-2026-XYZ-0042"

    def test_date_format_with_disallowed_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            self._render("{{DATE:../malicious}}")
