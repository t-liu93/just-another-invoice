"""Tests for M9 step 5: email template rendering + settings API.

Unit tests cover:
- render_email_template: happy path, missing placeholders, XSS/HTML-escape,
  nl2br, unknown token, nh3 strip.
- EmailTemplatesSetting schema: fallback to DEFAULT_EMAIL_TEMPLATES.
- EmailTemplatesUpdate: invalid structure rejected (422).

Integration tests (marked ``integration``) cover:
- GET /settings/email-templates returns built-in defaults when unset.
- PUT /settings/email-templates saves and round-trips.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from jai.schemas.setting import (
    DEFAULT_EMAIL_TEMPLATES,
    EmailTemplate,
    EmailTemplateLocaleMap,
    EmailTemplatesSetting,
)
from jai.services.email import render_email_template

# ---------------------------------------------------------------------------
# Unit tests – render_email_template
# ---------------------------------------------------------------------------


class TestRenderEmailTemplate:
    """Pure-function tests for ``render_email_template``."""

    def _make_template(self, subject: str, body: str) -> EmailTemplate:
        return EmailTemplate(subject=subject, body=body)

    def test_happy_path_subject_and_body(self) -> None:
        """Known placeholders are substituted in both subject and body."""
        tmpl = self._make_template(
            subject="Invoice {INVOICE_NUMBER} from {COMPANY_NAME}",
            body="Dear {CUSTOMER_NAME},\n\nAmount due: {AMOUNT_DUE}",
        )
        context = {
            "INVOICE_NUMBER": "INV-001",
            "COMPANY_NAME": "Acme BV",
            "CUSTOMER_NAME": "Bob",
            "AMOUNT_DUE": "1,234.56",
        }
        subject, html_body = render_email_template(tmpl, context)
        assert subject == "Invoice INV-001 from Acme BV"
        assert "Dear Bob," in html_body
        assert "1,234.56" in html_body

    def test_missing_placeholder_replaced_with_empty_string(self) -> None:
        """Context keys absent → empty string, no exception."""
        tmpl = self._make_template(
            subject="Invoice {INVOICE_NUMBER}",
            body="Due: {DUE_DATE}",
        )
        subject, html_body = render_email_template(tmpl, {})
        # Missing tokens → empty strings, not crashes.
        assert subject == "Invoice "
        assert "Due: " in html_body
        assert "{DUE_DATE}" not in html_body

    def test_xss_value_in_body_is_html_escaped(self) -> None:
        """Values containing HTML special chars are escaped in the body."""
        tmpl = self._make_template(
            subject="Hi {CUSTOMER_NAME}",
            body="Hello {CUSTOMER_NAME}, total is {TOTAL}",
        )
        context = {
            "CUSTOMER_NAME": "<script>alert('xss')</script>",
            "TOTAL": "100",
        }
        subject, html_body = render_email_template(tmpl, context)
        # In body: the raw <script> must NOT appear; escaped form must.
        assert "<script>" not in html_body
        assert "&lt;script&gt;" in html_body
        # Subject is plain text – the raw characters are kept (email clients
        # render subject as plain text, not HTML).
        assert "<script>" in subject  # plain-text subject keeps raw chars

    def test_html_in_body_template_is_stripped_by_nh3(self) -> None:
        """nh3 strips HTML/script tags pasted into the body."""
        tmpl = self._make_template(
            subject="Test",
            body="Hello <script>evil()</script> world",
        )
        _, html_body = render_email_template(tmpl, {})
        assert "<script>" not in html_body
        assert "evil()" not in html_body
        assert "Hello" in html_body
        assert "world" in html_body

    def test_nl2br_converts_newlines_to_br(self) -> None:
        """Newlines in the body are converted to ``<br>`` tags."""
        tmpl = self._make_template(
            subject="S",
            body="Line one\nLine two\nLine three",
        )
        _, html_body = render_email_template(tmpl, {})
        assert "<br>" in html_body
        # Both lines should be present in some form.
        assert "Line one" in html_body
        assert "Line two" in html_body
        assert "Line three" in html_body

    def test_windows_crlf_also_converted(self) -> None:
        """Windows-style \\r\\n newlines are also converted to ``<br>``."""
        tmpl = self._make_template(subject="S", body="A\r\nB\r\nC")
        _, html_body = render_email_template(tmpl, {})
        assert "<br>" in html_body
        assert "A" in html_body
        assert "B" in html_body

    def test_unknown_token_in_body_replaced_with_empty(self) -> None:
        """Placeholder tokens outside the allowlist → empty string, no crash."""
        tmpl = self._make_template(
            subject="Hi {UNKNOWN_TOKEN}",
            body="Value: {SUPER_SECRET}",
        )
        context = {"UNKNOWN_TOKEN": "XYZUNIQUE123", "SUPER_SECRET": "SECRETVALUE456"}
        _, html_body = render_email_template(tmpl, context)
        # Unknown tokens are suppressed — the raw context values must NOT appear
        # in the rendered body because the allowlist gate blocks them.
        assert "{UNKNOWN_TOKEN}" not in html_body
        assert "{SUPER_SECRET}" not in html_body
        assert "XYZUNIQUE123" not in html_body       # value of unknown token
        assert "SECRETVALUE456" not in html_body     # value of unknown token

    def test_html_shell_wraps_body(self) -> None:
        """The HTML body is wrapped in a minimal HTML shell."""
        tmpl = self._make_template(subject="S", body="Hello world")
        _, html_body = render_email_template(tmpl, {})
        assert "<!DOCTYPE html>" in html_body
        assert "<body" in html_body
        assert "Hello world" in html_body

    def test_all_common_tokens_substituted(self) -> None:
        """All common + invoice + quote tokens are recognised."""
        body = (
            "{COMPANY_NAME} {CUSTOMER_NAME} {DATE} {DOCUMENT_NUMBER} "
            "{TOTAL} {CURRENCY} {INVOICE_NUMBER} {DUE_DATE} {AMOUNT_DUE} "
            "{QUOTE_NUMBER} {VALID_UNTIL}"
        )
        tmpl = self._make_template(subject="S", body=body)
        context = {k: f"[{k}]" for k in [
            "COMPANY_NAME", "CUSTOMER_NAME", "DATE", "DOCUMENT_NUMBER",
            "TOTAL", "CURRENCY", "INVOICE_NUMBER", "DUE_DATE", "AMOUNT_DUE",
            "QUOTE_NUMBER", "VALID_UNTIL",
        ]}
        _, html_body = render_email_template(tmpl, context)
        for k, v in context.items():
            assert v in html_body, f"Token {k} not found in html_body"

    def test_ampersand_and_quotes_escaped(self) -> None:
        """& and quotes in values are HTML-escaped."""
        tmpl = self._make_template(subject="S", body="Company: {COMPANY_NAME}")
        _, html_body = render_email_template(tmpl, {"COMPANY_NAME": "A & B \"Co\""})
        assert "&amp;" in html_body
        assert "&quot;" in html_body
        assert "A & B" not in html_body


# ---------------------------------------------------------------------------
# Unit tests – DEFAULT_EMAIL_TEMPLATES + schema
# ---------------------------------------------------------------------------


class TestDefaultEmailTemplates:
    """Validate the built-in default templates and schema fallback."""

    def test_default_templates_have_all_locales(self) -> None:
        """DEFAULT_EMAIL_TEMPLATES covers every configurable type × en + zh."""
        for doc_type in ("invoice", "quote", "advance", "final", "credit_note", "refund"):
            locale_map = getattr(DEFAULT_EMAIL_TEMPLATES, doc_type)
            assert locale_map.en.subject
            assert locale_map.zh.subject

    def test_default_invoice_en_contains_expected_placeholders(self) -> None:
        """Built-in invoice EN body contains the key placeholder tokens."""
        body = DEFAULT_EMAIL_TEMPLATES.invoice.en.body
        assert "{INVOICE_NUMBER}" in body
        assert "{AMOUNT_DUE}" in body
        assert "{DUE_DATE}" in body

    def test_default_quote_zh_contains_expected_placeholders(self) -> None:
        """Built-in quote ZH body contains the key placeholder tokens."""
        body = DEFAULT_EMAIL_TEMPLATES.quote.zh.body
        assert "{QUOTE_NUMBER}" in body
        assert "{VALID_UNTIL}" in body

    def test_email_templates_setting_validates_structure(self) -> None:
        """EmailTemplatesSetting is created correctly from valid data."""
        tmpl = EmailTemplate(subject="S", body="B")
        locale_map = EmailTemplateLocaleMap(en=tmpl, zh=tmpl)
        setting = EmailTemplatesSetting(invoice=locale_map, quote=locale_map)
        assert setting.invoice.en.subject == "S"
        # Existing M9 JSON lacks the M12 fields and must parse into defaults.
        assert setting.credit_note.en.subject
        assert setting.refund.zh.body

    def test_email_templates_setting_rejects_missing_fields(self) -> None:
        """EmailTemplatesSetting rejects partially missing fields."""
        import pydantic
        with pytest.raises((pydantic.ValidationError, TypeError)):
            EmailTemplatesSetting(invoice={"en": {"subject": "S"}})  # type: ignore[arg-type]

    def test_default_templates_render_without_error(self) -> None:
        """All four default templates render cleanly with a full context."""
        context = {
            "COMPANY_NAME": "Acme BV",
            "CUSTOMER_NAME": "Bob Ltd",
            "DATE": "2026-06-14",
            "DOCUMENT_NUMBER": "INV-001",
            "TOTAL": "1,000.00",
            "CURRENCY": "EUR",
            "INVOICE_NUMBER": "INV-001",
            "DUE_DATE": "2026-07-14",
            "AMOUNT_DUE": "1,000.00",
            "QUOTE_NUMBER": "QUO-001",
            "VALID_UNTIL": "2026-07-14",
        }
        for doc_type, locale_map in [
            ("invoice", DEFAULT_EMAIL_TEMPLATES.invoice),
            ("quote", DEFAULT_EMAIL_TEMPLATES.quote),
        ]:
            for locale in ("en", "zh"):
                tmpl = getattr(locale_map, locale)
                subject, html_body = render_email_template(tmpl, context)
                assert subject, f"{doc_type}/{locale} subject is empty"
                assert "<!DOCTYPE html>" in html_body


# ---------------------------------------------------------------------------
# Integration tests – GET/PUT /settings/email-templates
# ---------------------------------------------------------------------------

import pyotp  # noqa: E402 – imported here to match existing test structure


async def _full_auth(client: AsyncClient) -> str:
    """Register → login → MFA setup → MFA verify. Returns TOTP secret."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "testpassword1"},
    )
    assert resp.status_code == 201
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "testpassword1"},
    )
    assert resp.status_code == 200
    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": code})
    assert resp.status_code == 204
    return secret


async def _setup_company(client: AsyncClient) -> None:
    """Create the singleton company so that the user has a company_id."""
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": "EUR"},
    )
    assert resp.status_code == 200


@pytest.mark.integration
class TestEmailTemplatesApi:
    """Integration tests for GET/PUT /api/v1/settings/email-templates."""

    @pytest.mark.asyncio
    async def test_get_returns_builtin_defaults_when_not_set(
        self, db_client: AsyncClient
    ) -> None:
        """GET returns the built-in default templates when no setting is saved."""
        await _full_auth(db_client)
        resp = await db_client.get("/api/v1/settings/email-templates")
        assert resp.status_code == 200
        data = resp.json()
        assert "invoice" in data
        assert "quote" in data
        # Must have both locales.
        assert "en" in data["invoice"]
        assert "zh" in data["invoice"]
        # Must have subject + body.
        assert data["invoice"]["en"]["subject"]
        assert data["invoice"]["en"]["body"]
        # Defaults should contain placeholder tokens.
        assert "{INVOICE_NUMBER}" in data["invoice"]["en"]["body"]

    @pytest.mark.asyncio
    async def test_put_and_get_roundtrip(self, db_client: AsyncClient) -> None:
        """PUT saves the templates; subsequent GET returns the saved values."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        payload = {
            "invoice": {
                "en": {"subject": "Custom invoice subject", "body": "Custom body\nLine 2"},
                "zh": {"subject": "自定义主题", "body": "自定义正文"},
            },
            "quote": {
                "en": {"subject": "Custom quote subject", "body": "Quote body"},
                "zh": {"subject": "报价主题", "body": "报价正文"},
            },
        }
        resp = await db_client.put("/api/v1/settings/email-templates", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["invoice"]["en"]["subject"] == "Custom invoice subject"
        assert data["invoice"]["zh"]["subject"] == "自定义主题"

        # GET should now return the saved values.
        resp = await db_client.get("/api/v1/settings/email-templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["invoice"]["en"]["body"] == "Custom body\nLine 2"
        assert data["quote"]["zh"]["body"] == "报价正文"

    @pytest.mark.asyncio
    async def test_get_requires_auth(self, client: AsyncClient) -> None:
        """GET without auth returns 401."""
        resp = await client.get("/api/v1/settings/email-templates")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_put_requires_auth(self, client: AsyncClient) -> None:
        """PUT without auth returns 401."""
        resp = await client.put("/api/v1/settings/email-templates", json={})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_put_rejects_missing_locale(self, db_client: AsyncClient) -> None:
        """PUT with a missing locale key is rejected with 422."""
        await _full_auth(db_client)
        # Missing zh under invoice.
        payload = {
            "invoice": {
                "en": {"subject": "S", "body": "B"},
                # "zh" deliberately omitted
            },
            "quote": {
                "en": {"subject": "S", "body": "B"},
                "zh": {"subject": "S", "body": "B"},
            },
        }
        resp = await db_client.put("/api/v1/settings/email-templates", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_put_rejects_missing_doc_type(self, db_client: AsyncClient) -> None:
        """PUT with a missing doc_type key is rejected with 422."""
        await _full_auth(db_client)
        # Missing "quote" entirely.
        payload = {
            "invoice": {
                "en": {"subject": "S", "body": "B"},
                "zh": {"subject": "S", "body": "B"},
            },
        }
        resp = await db_client.put("/api/v1/settings/email-templates", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_put_rejects_missing_subject(self, db_client: AsyncClient) -> None:
        """PUT with a template missing the subject field is rejected with 422."""
        await _full_auth(db_client)
        payload = {
            "invoice": {
                "en": {"body": "No subject here"},  # missing "subject"
                "zh": {"subject": "S", "body": "B"},
            },
            "quote": {
                "en": {"subject": "S", "body": "B"},
                "zh": {"subject": "S", "body": "B"},
            },
        }
        resp = await db_client.put("/api/v1/settings/email-templates", json=payload)
        assert resp.status_code == 422
